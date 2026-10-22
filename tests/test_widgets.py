import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fakegoogle import CLIENT_ID, CLIENT_SECRET, FakeGoogle
from fastapi.testclient import TestClient

from jarvis_hud.cache import Cache
from jarvis_hud.connections import google
from jarvis_hud.plugins.calendar import CalendarPlugin, parse_event, today_and_upcoming
from jarvis_hud.plugins.core import CorePlugin
from jarvis_hud.plugins.gmail import GmailPlugin
from jarvis_hud.timezone import resolve_timezone

TZ = ZoneInfo("Europe/Bratislava")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"


def query(request: httpx2.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


def gmail_api(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path.removeprefix("/gmail/v1/users/me")
    if path == "/labels/INBOX":
        return httpx2.Response(200, json={"threadsUnread": 7})
    if path == "/messages":
        if "is:unread" in query(request)["q"][0]:
            return httpx2.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx2.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})
    if path == "/messages/m1":
        headers = [
            {"name": "From", "value": "Pepper Potts <pepper@stark.example>"},
            {"name": "Subject", "value": "Board meeting <today>"},
        ]
        return httpx2.Response(
            200,
            json={
                "threadId": "t1",
                "internalDate": "1792656000000",
                "payload": {"headers": headers},
            },
        )
    return httpx2.Response(404)


def calendar_api(request: httpx2.Request) -> httpx2.Response:
    if request.url.path.endswith("/calendarList"):
        items = [
            {"id": "tony@stark.example", "primary": True, "summary": "Tony"},
            {
                "id": "en.slovak#holiday@group.v.calendar.google.com",
                "selected": True,
                "summary": "Holidays",
            },
            {"id": "hidden", "summary": "Not selected"},
        ]
        return httpx2.Response(200, json={"items": items})
    if "holiday" in request.url.path:
        items = [
            {"summary": "Holiday", "start": {"date": "2099-01-02"}, "end": {"date": "2099-01-03"}}
        ]
        return httpx2.Response(200, json={"items": items})
    if "hidden" in request.url.path:
        return httpx2.Response(500)
    items = [
        {
            "summary": "Suit fitting",
            "start": {"dateTime": "2099-01-02T10:00:00+01:00"},
            "end": {"dateTime": "2099-01-02T11:00:00+01:00"},
            "hangoutLink": "https://meet.google.com/abc",
            "htmlLink": "https://calendar.google.com/event?eid=1",
        }
    ]
    return httpx2.Response(200, json={"items": items})


@pytest.fixture
def fake_google() -> FakeGoogle:
    fake = FakeGoogle()
    fake.routes[GMAIL] = gmail_api
    fake.routes[CALENDAR] = calendar_api
    return fake


@pytest.fixture
def client(make_client: MakeClient, fake_google: FakeGoogle) -> TestClient:
    plugins = {"core": CorePlugin(), "gmail": GmailPlugin(), "calendar": CalendarPlugin()}
    client = make_client(transport=httpx2.MockTransport(fake_google), plugins=plugins)
    complete_setup(client)
    return client


def connect_google(client: TestClient, fake_google: FakeGoogle) -> None:
    data = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    client.post("/integrations/google/setup", data=data)
    response = client.post("/integrations/google/connect", follow_redirects=False)
    state, code = fake_google.authorize(response.headers["location"])
    client.get("/integrations/google/callback", params={"state": state, "code": code})


# Widgets


def test_dashboard_orders_widgets_by_plugin_position(client: TestClient):
    text = client.get("/").text
    assert text.index("widget-core-today") < text.index("widget-calendar-upcoming")
    assert text.index("widget-calendar-upcoming") < text.index("widget-gmail-inbox")


@pytest.mark.parametrize("path", ["/widgets/gmail/inbox", "/widgets/calendar/upcoming"])
def test_widgets_without_google_ask_to_connect(client: TestClient, path: str):
    response = client.get(path)
    assert "Connect Google" in response.text
    assert 'class="num"' not in response.text
    assert "is-empty" in response.text


def test_gmail_widget(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    text = client.get("/widgets/gmail/inbox").text
    assert '<div class="num" style="font-size: 32px">7</div>' in text
    assert '<div class="num" style="font-size: 32px">3</div>' in text
    assert "Board meeting &lt;today&gt;" in text
    assert "Pepper Potts" in text
    assert "#inbox/t1" in text
    assert "now" in text
    assert "Open inbox" in text


def test_calendar_widget(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    text = client.get("/widgets/calendar/upcoming").text
    # 2099 is outside the 7-day window in reality, but the fake ignores the time range.
    assert "Suit fitting" in text
    assert "Holiday" in text
    assert "Not selected" not in text
    assert text.index("Holiday") < text.index("Suit fitting")


def test_widget_data_is_cached(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    calls: list[str] = []
    fake_google.routes[GMAIL] = lambda request: calls.append(request.url.path) or gmail_api(request)
    client.get("/widgets/gmail/inbox")
    count = len(calls)
    client.get("/widgets/gmail/inbox")
    assert len(calls) == count
    # The access token is reused too: one refresh when connecting, one for the widget.
    assert fake_google.refreshes == 2


def test_disabled_api_is_shown_in_widget(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    fake_google.routes[GMAIL] = lambda request: httpx2.Response(
        403, json={"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}
    )
    text = client.get("/widgets/gmail/inbox").text
    assert "The Gmail API is not enabled in your Google project." in text
    assert "Check the connection" in text


def test_missing_permission_is_shown_in_widget(client: TestClient, fake_google: FakeGoogle):
    fake_google.granted_scopes = " ".join(google.BASE_SCOPES)
    connect_google(client, fake_google)
    assert "no permission" in client.get("/widgets/calendar/upcoming").text


def test_failing_widget_shows_error(client: TestClient):
    class BrokenPlugin(CorePlugin):
        id = "broken"

        async def _load_today(self, context: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

    client.app.state.plugins = {"broken": BrokenPlugin()}
    response = client.get("/widgets/broken/today")
    assert response.status_code == 200
    assert "could not be loaded" in response.text


def test_disconnect_clears_cached_data(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    assert "7" in client.get("/widgets/gmail/inbox").text
    client.post("/integrations/google/1/disconnect")
    assert "Connect Google" in client.get("/widgets/gmail/inbox").text


# Calendar parsing


def event(**fields: Any) -> dict[str, Any]:
    return {
        "summary": "Meeting",
        "start": {"dateTime": "2026-10-22T08:00:00Z"},
        "end": {"dateTime": "2026-10-22T09:00:00Z"},
        **fields,
    }


def test_parse_event_converts_to_local_time():
    parsed = parse_event(event(), {"primary": True}, TZ)
    assert parsed["start"] == datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
    assert parsed["calendar"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "cancelled"},
        {"eventType": "workingLocation"},
        {"attendees": [{"self": True, "responseStatus": "declined"}]},
    ],
)
def test_parse_event_hides_irrelevant_events(fields: dict[str, Any]):
    assert parse_event(event(**fields), {}, TZ) is None


def test_parse_event_all_day_and_video_link():
    parsed = parse_event(
        event(
            start={"date": "2026-10-23"},
            end={"date": "2026-10-24"},
            conferenceData={
                "entryPoints": [{"entryPointType": "video", "uri": "https://zoom.example/1"}]
            },
        ),
        {"summary": "Team"},
        TZ,
    )
    assert parsed["all_day"]
    assert parsed["video_url"] == "https://zoom.example/1"
    assert parsed["calendar"] == "Team"


def test_today_and_upcoming():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=TZ)
    events = [
        parse_event(event(start={"date": "2026-10-20"}, end={"date": "2026-10-25"}), {}, TZ),
        parse_event(event(), {}, TZ),
        parse_event(
            event(
                start={"dateTime": "2026-10-22T15:00:00+02:00"},
                end={"dateTime": "2026-10-22T16:30:00+02:00"},
                hangoutLink="https://meet.google.com/x",
            ),
            {},
            TZ,
        ),
        parse_event(event(start={"dateTime": "2026-10-26T10:00:00+01:00"}), {}, TZ),
    ]
    result = today_and_upcoming(events, now)
    assert [e["kind"] for e in result["today"]] == ["event", "past", "now", "event"]
    assert result["today_count"] == 3
    assert result["today"][3]["sub"] == "1 h 30 min · Video call"
    assert [e["start"].day for e in result["upcoming"]] == [26]


# Cache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.anyio
async def test_cache_returns_fresh_value_without_fetching():
    clock, calls = Clock(), []

    async def fetch() -> int:
        calls.append(1)
        return len(calls)

    cache = Cache(clock)
    assert (await cache.get("k", fetch, fresh=10, max_age=100)).value == 1
    clock.now += 5
    assert (await cache.get("k", fetch, fresh=10, max_age=100)).value == 1
    assert len(calls) == 1


@pytest.mark.anyio
async def test_cache_serves_stale_value_and_refreshes_in_background():
    clock, calls = Clock(), []

    async def fetch() -> int:
        calls.append(1)
        return len(calls)

    cache = Cache(clock)
    await cache.get("k", fetch, fresh=10, max_age=100)
    clock.now += 20
    assert (await cache.get("k", fetch, fresh=10, max_age=100)).value == 1
    await asyncio.sleep(0)
    assert (await cache.get("k", fetch, fresh=10, max_age=100)).value == 2


@pytest.mark.anyio
async def test_cache_shares_concurrent_fetches():
    calls = []

    async def fetch() -> str:
        calls.append(1)
        await asyncio.sleep(0.01)
        return "value"

    cache = Cache(Clock())
    results = await asyncio.gather(*(cache.get("k", fetch, 10, 100) for _ in range(5)))
    assert {result.value for result in results} == {"value"}
    assert len(calls) == 1


@pytest.mark.anyio
async def test_cache_does_not_cache_failures_and_keeps_last_good_value():
    clock = Clock()
    cache = Cache(clock)
    fail = False

    async def fetch() -> str:
        if fail:
            raise RuntimeError("down")
        return "good"

    with pytest.raises(RuntimeError):
        await Cache(clock).get("k", _raise, 10, 100)

    await cache.get("k", fetch, fresh=10, max_age=100)
    fail = True
    clock.now += 200
    result = await cache.get("k", fetch, fresh=10, max_age=100)
    assert result.value == "good"
    assert isinstance(result.error, RuntimeError)


@pytest.mark.anyio
async def test_cache_invalidate_by_prefix():
    cache, calls = Cache(Clock()), []

    async def fetch() -> int:
        calls.append(1)
        return len(calls)

    await cache.get("google:1:gmail", fetch, 10, 100)
    cache.invalidate("google:1:")
    assert (await cache.get("google:1:gmail", fetch, 10, 100)).value == 2


async def _raise() -> None:
    raise RuntimeError("down")


# Time zone


def test_resolve_timezone(monkeypatch: pytest.MonkeyPatch):
    assert resolve_timezone("Europe/Bratislava") == ZoneInfo("Europe/Bratislava")
    monkeypatch.setenv("TZ", "America/New_York")
    assert resolve_timezone("Not/AZone") == ZoneInfo("America/New_York")
    monkeypatch.setenv("TZ", "")
    assert resolve_timezone(None) is not None
