import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient

from jarvis_hud.music import parse_youtube

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
    assert 'data-source="ambient"' in client.get("/").text


def test_choose_youtube(client: TestClient):
    url = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    response = client.post("/integrations/music", data={"source": "youtube", "url": url})
    assert "The music settings were saved." in response.text
    text = client.get("/").text
    assert 'data-source="youtube"' in text
    assert f'data-video="{VIDEO}"' in text and f'data-playlist="{PLAYLIST}"' in text
    assert f'value="{url.replace("&", "&amp;")}"' in client.get("/integrations/music").text

    client.post("/integrations/music", data={"source": "ambient"})
    assert 'data-source="ambient"' in client.get("/").text


def test_invalid_youtube_link(client: TestClient):
    response = client.post(
        "/integrations/music", data={"source": "youtube", "url": "https://vimeo.com/1"}
    )
    assert response.status_code == 400
    assert "Enter a link to a YouTube video" in response.text
