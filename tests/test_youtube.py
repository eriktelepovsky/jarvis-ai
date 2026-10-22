from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fakegoogle import FakeGoogle
from fastapi.testclient import TestClient
from test_widgets import connect_google

from jarvis_hud.plugins.youtube import YouTubePlugin, age, parse_feed

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  {entries}
</feed>"""
ENTRY = """<entry>
  <yt:videoId>{id}</yt:videoId>
  <title>{title}</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v={id}"/>
  <published>{published}</published>
</entry>"""


def feed(*videos: tuple[str, str, datetime]) -> str:
    entries = "".join(
        ENTRY.format(id=vid, title=escape(title), published=when.isoformat())
        for vid, title, when in videos
    )
    return FEED.format(entries=entries)


def test_parse_feed():
    when = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    [video] = parse_feed(feed(("abc", "HTMX & dashboards", when)).encode(), "Server Side")
    assert video == {
        "id": "abc",
        "title": "HTMX & dashboards",
        "url": "https://www.youtube.com/watch?v=abc",
        "published": when,
        "channel": "Server Side",
    }


def test_age():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert age(now - timedelta(minutes=20), now) == "just now"
    assert age(now - timedelta(hours=3), now) == "3h ago"
    assert age(now - timedelta(days=2), now) == "2d ago"


@pytest.fixture
def fake_google() -> FakeGoogle:
    now = datetime.now(UTC)
    fake = FakeGoogle()
    fake.routes["https://www.googleapis.com/youtube/v3/subscriptions"] = lambda r: httpx2.Response(
        200,
        json={
            "items": [
                {"snippet": {"title": "Server Side", "resourceId": {"channelId": "UC1"}}},
                {"snippet": {"title": "Gone", "resourceId": {"channelId": "UC2"}}},
            ]
        },
    )
    feeds = {
        "UC1": feed(
            ("new", "Fresh <video>", now - timedelta(hours=3)),
            ("old", "Last week", now - timedelta(days=5)),
            ("ancient", "Long ago", now - timedelta(days=30)),
        )
    }

    def youtube_feed(request: httpx2.Request) -> httpx2.Response:
        body = feeds.get(request.url.params["channel_id"])
        return httpx2.Response(200, text=body) if body else httpx2.Response(404)

    fake.public_routes = {"https://www.youtube.com/feeds/videos.xml": youtube_feed}
    return fake


@pytest.fixture
def client(make_client: MakeClient, fake_google: FakeGoogle) -> TestClient:
    def transport(request: httpx2.Request) -> httpx2.Response:
        for prefix, handler in fake_google.public_routes.items():
            if str(request.url).startswith(prefix):
                return handler(request)
        return fake_google(request)

    plugins = {"youtube": YouTubePlugin()}
    client = make_client(transport=httpx2.MockTransport(transport), plugins=plugins)
    complete_setup(client)
    return client


def test_new_videos_from_subscriptions(client: TestClient, fake_google: FakeGoogle):
    assert "Google is not connected." in client.get("/welcome/widgets").text
    connect_google(client, fake_google)
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">1</span>' in text
    assert "Fresh &lt;video&gt;" in text
    assert "Server Side · 3h ago" in text
    assert "Last week" in text
    assert "Long ago" not in text
    assert ">2</div><div" in text  # channels, including one whose feed is gone


def test_youtube_needs_its_permission(client: TestClient, fake_google: FakeGoogle):
    from jarvis_hud.connections import google

    fake_google.granted_scopes = " ".join([*google.BASE_SCOPES, google.CAPABILITIES[0].scope])
    connect_google(client, fake_google)
    assert "no permission" in client.get("/welcome/widgets").text
