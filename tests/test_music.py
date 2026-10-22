import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fakegoogle import FakeGoogle
from fastapi.testclient import TestClient
from test_widgets import connect_google

from myorb.music import parse_youtube

VIDEO = "jfKfPfyJRdk"
PLAYLIST = "PLOzDu-MXXLljkyXTcZ0x7a0xNr5ITz2mB"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VIDEO}", (VIDEO, None)),
        (f"https://youtu.be/{VIDEO}?si=abc", (VIDEO, None)),
        (f"https://www.youtube.com/live/{VIDEO}", (VIDEO, None)),
        (f"https://www.youtube.com/playlist?list={PLAYLIST}", (None, PLAYLIST)),
        (f"https://music.youtube.com/watch?v={VIDEO}&list={PLAYLIST}", (VIDEO, PLAYLIST)),
    ],
)
def test_parse_youtube(url: str, expected: tuple):
    assert parse_youtube(url) == expected


@pytest.mark.parametrize(
    "url",
    ["https://vimeo.com/123", "https://www.youtube.com/watch?v=bad", "javascript:alert(1)", ""],
)
def test_parse_youtube_rejects(url: str):
    with pytest.raises(ValueError):
        parse_youtube(url)


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    client = make_client()
    complete_setup(client)
    return client


def test_music_defaults_to_ambient(client: TestClient):
    assert client.get("/music/config").json() == {
        "source": "ambient",
        "video": None,
        "playlist": None,
        "title": "",
    }
    assert 'id="media-dock"' in client.get("/").text
    assert 'id="media-dock"' in client.get("/integrations").text


def test_choose_youtube(client: TestClient):
    url = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    response = client.post("/integrations/music", data={"source": "youtube", "url": url})
    assert "The music settings were saved." in response.text
    config = client.get("/music/config").json()
    assert config == {"source": "youtube", "video": VIDEO, "playlist": PLAYLIST, "title": ""}
    assert f'value="{url.replace("&", "&amp;")}"' in client.get("/integrations/music").text

    client.post("/integrations/music", data={"source": "ambient"})
    assert client.get("/music/config").json()["source"] == "ambient"


def test_invalid_youtube_link(client: TestClient):
    response = client.post(
        "/integrations/music", data={"source": "youtube", "url": "https://vimeo.com/1"}
    )
    assert response.status_code == 400
    assert "Enter a link to a YouTube video" in response.text


# Finding music in myOrb


@pytest.fixture
def google_client(make_client: MakeClient) -> tuple[TestClient, FakeGoogle]:
    fake = FakeGoogle()
    api = "https://www.googleapis.com/youtube/v3"
    fake.routes[f"{api}/search"] = lambda request: httpx2.Response(
        200,
        json={
            "items": [
                {
                    "id": {"videoId": VIDEO},
                    "snippet": {
                        "title": "Lofi <radio>",
                        "channelTitle": "Lofi Girl",
                        "liveBroadcastContent": "live",
                        "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/x/mq.jpg"}},
                    },
                },
                {
                    "id": {"playlistId": PLAYLIST},
                    "snippet": {"title": "Focus", "channelTitle": "Me"},
                },
            ]
        },
    )
    fake.routes[f"{api}/playlists"] = lambda request: httpx2.Response(
        200, json={"items": [{"id": PLAYLIST, "snippet": {"title": "My focus mix"}}]}
    )
    fake.routes[f"{api}/videos"] = lambda request: httpx2.Response(
        200,
        json={
            "items": [{"id": request.url.params["id"], "snippet": {"title": "Rain &amp; piano"}}]
        },
    )
    client = make_client(transport=httpx2.MockTransport(fake))
    complete_setup(client)
    return client, fake


def test_search_needs_youtube_access(client: TestClient):
    response = client.get("/music/search", params={"q": "lofi"})
    assert response.status_code == 409
    assert "Connect a Google account" in response.json()["error"]
    assert "Connect a Google account" in client.get("/music/library").json()["error"]


def test_search_play_and_recent(google_client):
    client, fake = google_client
    connect_google(client, fake)
    results = client.get("/music/search", params={"q": "lofi"}).json()["results"]
    assert results == [
        {
            "kind": "live",
            "id": VIDEO,
            "title": "Lofi <radio>",
            "channel": "Lofi Girl",
            "thumbnail": "https://i.ytimg.com/vi/x/mq.jpg",
        },
        {"kind": "playlist", "id": PLAYLIST, "title": "Focus", "channel": "Me", "thumbnail": ""},
    ]
    assert client.get("/music/library").json()["playlists"][0]["title"] == "My focus mix"

    played = client.post("/music/play", data={**results[1]}).json()
    assert played == {"source": "youtube", "video": None, "playlist": PLAYLIST, "title": "Focus"}
    client.post("/music/play", data={**results[0], "thumbnail": "https://evil.example/x.jpg"})
    assert client.get("/music/config").json()["video"] == VIDEO
    recent = client.get("/music/library").json()["recent"]
    assert [r["id"] for r in recent] == [VIDEO, PLAYLIST]
    assert recent[0]["thumbnail"] == ""
    # Saving the settings page keeps the recent picks.
    client.post("/integrations/music", data={"source": "ambient"})
    assert len(client.get("/music/library").json()["recent"]) == 2
    assert client.post("/music/play", data={"kind": "video", "id": "bad id"}).status_code == 400


def test_search_finds_a_pasted_link(google_client):
    client, fake = google_client
    connect_google(client, fake)
    link = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    results = client.get("/music/search", params={"q": link}).json()["results"]
    assert [(r["kind"], r["id"], r["title"]) for r in results] == [
        ("video", VIDEO, "Rain & piano"),
        ("playlist", PLAYLIST, "My focus mix"),
    ]
    assert client.get("/music/search", params={"q": "https://vimeo.com/1"}).json() == {
        "results": []
    }
