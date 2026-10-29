import asyncio
import base64
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fakegoogle import CLIENT_ID, CLIENT_SECRET, FakeGoogle
from fastapi.testclient import TestClient

from myorb.cache import Cache
from myorb.connections import google
from myorb.plugins.calendar import CalendarPlugin, parse_event, today_and_upcoming
from myorb.plugins.gmail import GmailPlugin
from myorb.timezone import resolve_timezone

TZ = ZoneInfo("Europe/Bratislava")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"


def query(request: httpx2.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


# What was sent to mark threads as read.
modified: list[str] = []
# Drafts saved in Gmail.
drafts: list[dict] = []


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_api(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path.removeprefix("/gmail/v1/users/me")
    if path == "/labels/INBOX":
        return httpx2.Response(200, json={"threadsUnread": 7})
    if path == "/messages":
        return httpx2.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})
    if path == "/settings/sendAs":
        addresses = ["tony@stark.example", "tony@starkindustries.example"]
        return httpx2.Response(200, json={"sendAs": [{"sendAsEmail": a} for a in addresses]})
    if path == "/threads":
        assert "is:unread" in query(request)["q"][0]
        return httpx2.Response(200, json={"threads": [{"id": "t1"}]})
    if path == "/threads/t1/modify":
        assert request.method == "POST"
        modified.append(request.content.decode())
        return httpx2.Response(200, json={"id": "t1"})
    if path == "/drafts/d1":
        assert request.method == "DELETE"
        drafts.clear()
        return httpx2.Response(204)
    if path == "/drafts":
        assert request.method == "POST"
        drafts.append(json.loads(request.content))
        return httpx2.Response(200, json={"id": "d1"})
    if path.startswith("/messages/m-") and path.endswith("/attachments/logo1"):
        return httpx2.Response(200, json={"data": "iVBORw0KGgo_-w", "size": 10})
    if path == "/threads/t1":
        # A conversation with a read email and, newest, an unread reply: listed once, by it.
        def message(sender: str, subject: str, labels: list[str]) -> dict:
            headers = [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                # Sent to another of the user's mailboxes, which Gmail collects.
                {"name": "Delivered-To", "value": "tony@stark.example"},
                {"name": "Message-ID", "value": f"<m{len(subject)}@mail.example>"},
                {
                    "name": "To",
                    "value": "Pepper <pepper@x.example>, Tony <Tony@StarkIndustries.example>",
                },
            ]
            payload: dict = {"headers": headers}
            if query(request).get("format") == ["full"]:
                html = (
                    '<p>Can we move it?</p><img src="https://track.example/pixel.gif">'
                    '<img src="cid:Logo1@stark">'
                )
                logo = [{"name": "Content-ID", "value": "<logo1@stark>"}]
                payload["parts"] = [
                    {"mimeType": "text/plain", "body": {"data": b64("Can we move it?")}},
                    {"mimeType": "text/html", "body": {"data": b64(html)}},
                    {"filename": "agenda.pdf", "body": {"attachmentId": "a1", "size": 245000}},
                    # An image sent inside the email, shown by its cid: address.
                    {
                        "filename": "logo.png",
                        "mimeType": "image/png",
                        "headers": logo,
                        "body": {"attachmentId": "logo1", "size": 10},
                    },
                ]
            return {
                "id": "m-" + sender.split()[0].lower(),
                "threadId": "t1",
                "labelIds": labels,
                "snippet": "Can we move it to 10:00 &amp; meet in the lab?",
                "internalDate": "1792656000000",
                "payload": payload,
            }

        messages = [
            message("Tony <tony@stark.example>", "Board meeting", ["INBOX"]),
            message(
                "Pepper Potts <pepper@stark.example>",
                "Board meeting <today>",
                ["UNREAD", "IMPORTANT", "STARRED"],
            ),
        ]
        return httpx2.Response(200, json={"id": "t1", "messages": messages})
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
    plugins = {"gmail": GmailPlugin(), "calendar": CalendarPlugin()}
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


def test_widgets_are_ordered_by_plugin_position(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    text = client.get("/welcome/widgets").text
    assert text.index('data-open="detail-gmail-inbox"') < text.index(
        'data-open="detail-calendar-today"'
    )


def test_widgets_without_google_ask_to_connect(client: TestClient):
    text = client.get("/welcome/widgets").text
    # Nothing around the orb until Google is connected; Integrations lists it instead.
    assert "node-value" not in text
    assert "Connect a service to get started" in text
    assert "Online · 0/0 sources" in text
    assert "Connect Google to see today" in client.get("/panels/calendar/today").text


def test_gmail_widget(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">7</span>' in text
    assert "unread · 3 emails today" in text
    assert "Board meeting &lt;today&gt;" in text
    assert "Pepper Potts" in text and "Tony" not in text
    assert text.count('<div class="modal-row') == 1  # one per unread conversation
    # Grouped by the user's address it was sent to, here another mailbox Gmail collects.
    assert 'data-group="tony@starkindustries.example"' in text
    # The search finds an email by the start of its text too, as Gmail does.
    assert 'data-search="Can we move it to 10:00 &amp; meet in the lab?"' in text
    # Each email opens in myOrb; marking it as read needs the permission, granted here.
    assert '<a class="row-link" href="/email/1/t1" data-view>' in text and ">Read<" not in text
    assert 'hx-post="/email/1/t1/read"' in text
    assert '<span class="row-mark" aria-hidden="true">★</span>Pepper Potts' in text  # starred
    # Gmail marks it important: counted, and the number narrows the list to such emails.
    assert 'data-filter="important"' in text and ">Important<" in text
    assert 'data-tags="important' in text  # and "today" too on the day it was received
    assert 'data-filter="today"' in text
    assert "In inbox" not in text
    assert "Open inbox" in text
    assert "Online · 2/2 sources" in text
    assert "All systems nominal" in text


def test_calendar_invitations_are_marked(client: TestClient, fake_google: FakeGoogle):
    def gmail(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/messages") and "filename:ics" in query(request)["q"][0]:
            return httpx2.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
        return gmail_api(request)

    fake_google.routes[GMAIL] = gmail
    connect_google(client, fake_google)
    # Answered in the calendar, not by email: the list says so (Claude reads it too).
    assert (
        "Board meeting &lt;today&gt; · Calendar invitation" in client.get("/welcome/widgets").text
    )


def test_calendar_widget_and_panels(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    text = client.get("/welcome/widgets").text
    # The fake returns events in 2099: none today, but they would be upcoming. Only the
    # next 7 days are listed in the details.
    assert '<span class="node-value">0</span>' in text
    assert "Suit fitting" not in text
    assert "Nothing on the calendar today." in client.get("/panels/calendar/today").text
    assert "Suit fitting" in client.get("/panels/calendar/next").text


def test_widget_data_is_cached(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    calls: list[str] = []
    fake_google.routes[GMAIL] = lambda request: calls.append(request.url.path) or gmail_api(request)
    client.get("/welcome/widgets")
    count = len(calls)
    client.get("/welcome/widgets")
    assert len(calls) == count
    # The access token is reused too: one refresh when connecting, one for the widgets.
    assert fake_google.refreshes == 2


def test_disabled_api_is_shown(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    fake_google.routes[GMAIL] = lambda request: httpx2.Response(
        403, json={"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}
    )
    text = client.get("/welcome/widgets").text
    assert "The Gmail API is not enabled in your Google project." in text
    assert "Check the connection" in text
    assert "1 source needs attention" in text


def test_rate_limit_is_waited_out_not_a_missing_permission(
    client: TestClient, fake_google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("myorb.connections.google_accounts.RATE_WAIT", 0)
    connect_google(client, fake_google)
    limited = httpx2.Response(
        403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}
    )
    calls: list[str] = []

    def at_first_too_many(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return limited if len(calls) <= 2 else gmail_api(request)

    fake_google.routes[GMAIL] = at_first_too_many
    text = client.get("/welcome/widgets").text
    assert "no permission" not in text and "needs attention" not in text
    # Limited for good: said as such, not as a permission.
    client.post("/integrations/google/1/disconnect")
    connect_google(client, fake_google)
    fake_google.routes[GMAIL] = lambda request: limited
    text = client.get("/welcome/widgets").text
    assert "Google asked myOrb to slow down" in text and "no permission" not in text


def test_missing_permission_is_shown(client: TestClient, fake_google: FakeGoogle):
    fake_google.granted_scopes = " ".join(google.BASE_SCOPES)
    connect_google(client, fake_google)
    assert "no permission" in client.get("/welcome/widgets").text


def test_disconnect_clears_cached_data(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    assert '<span class="node-value">7</span>' in client.get("/welcome/widgets").text
    client.post("/integrations/google/1/disconnect")
    assert 'data-open="detail-gmail-inbox"' not in client.get("/welcome/widgets").text


def test_today_panel_labels_all_day_events_once(client: TestClient, fake_google: FakeGoogle):
    today = datetime.now(client.app.state.timezone).date()  # the app's "today", e.g. UTC in CI
    all_day = {"start": {"date": str(today)}, "end": {"date": str(today + timedelta(days=1))}}
    fake_google.routes[CALENDAR] = lambda request: (
        httpx2.Response(200, json={"items": [{"id": "primary", "primary": True}]})
        if request.url.path.endswith("/calendarList")
        else httpx2.Response(
            200,
            json={"items": [{"summary": "Name day", **all_day}, {"summary": "Garden", **all_day}]},
        )
    )
    connect_google(client, fake_google)
    text = client.get("/panels/calendar/today").text
    assert "Name day" in text and "Garden" in text
    assert text.count("All day") == 1
    widgets = client.get("/welcome/widgets").text
    assert 'data-filter="today"' in widgets and 'data-filter="week"' in widgets
    assert 'data-tags="today"' in widgets


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


def test_parse_event_free_unless_gmail_made_it():
    assert parse_event(event(transparency="transparent"), {}, TZ)["free"]
    # Gmail marks every event it makes from an email (a booking, a flight) free.
    booking = event(transparency="transparent", eventType="fromGmail")
    assert not parse_event(booking, {}, TZ)["free"]


def test_an_event_gone_to_takes_the_time_even_shown_as_free():
    from myorb.plugins.calendar import _busy

    base = {"all_day": False, "kind": "event", "free": True}
    start = datetime(2026, 10, 28, 16, tzinfo=TZ)
    gone = {**base, "title": "Physio", "start": start, "end": start + timedelta(hours=1)}
    stay = {**base, "title": "Hold", "start": start, "end": start + timedelta(hours=1)}
    trip = {"event": gone, "start": start - timedelta(minutes=18), "end": start}
    assert [i.title for i in _busy([gone, stay], [trip])] == ["Physio", "Travel"]


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


def test_calendar_panel_moves_between_days(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    today = client.get("/panels/calendar/today").text
    assert '<span class="label strong">Today</span>' in today
    assert 'aria-label="Previous day" disabled' in today
    tomorrow = client.get("/panels/calendar/today", params={"day": 1}).text
    assert '<span class="label strong">Tomorrow</span>' in tomorrow
    assert "Nothing on the calendar tomorrow." in tomorrow
    assert 'hx-get="/panels/calendar/today?day=2"' in tomorrow
    last = client.get("/panels/calendar/today", params={"day": 99}).text
    assert 'aria-label="Next day" disabled' in last
    assert "Today" in client.get("/panels/calendar/today", params={"day": "x"}).text


def test_events_on_another_day():
    from datetime import UTC, datetime, timedelta

    from myorb.plugins.calendar import events_on

    day = datetime(2026, 10, 23, tzinfo=UTC)
    event = {"title": "Standup", "all_day": False, "video_url": None, "location": None}
    events = [
        {**event, "start": day + timedelta(hours=9), "end": day + timedelta(hours=9, minutes=15)},
        {**event, "start": day + timedelta(hours=9), "end": day + timedelta(hours=9, minutes=15)},
        {
            **event,
            "title": "Trip",
            "all_day": True,
            "start": day - timedelta(days=1),
            "end": day + timedelta(days=2),
            "calendar": None,
        },
        {
            **event,
            "title": "Later",
            "start": day + timedelta(days=1),
            "end": day + timedelta(days=1, hours=1),
        },
    ]
    for e in events:
        e.setdefault("calendar", None)
    titles = [e["title"] for e in events_on(events, day.date())]
    assert titles == ["Trip", "Standup"]  # all-day first, the duplicate (two accounts) once


class NoConnections:
    """Nothing connected through a form, e.g. no Commute."""

    async def list(self, provider: str) -> list:
        return []


class NoIgnored:
    """A database where no event is ignored, for a widget's context."""

    def __call__(self) -> "NoIgnored":
        return self

    async def __aenter__(self) -> "NoIgnored":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def scalars(self, query: object) -> list:
        return []


def test_brief_includes_all_of_tomorrow(monkeypatch):
    import asyncio
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from myorb.plugins.calendar import CalendarPlugin
    from myorb.plugins.google_data import AccountData

    now = datetime(2026, 10, 22, 17, 0, tzinfo=ZoneInfo("Europe/Bratislava"))
    tomorrow = now.replace(hour=0) + timedelta(days=1)
    base = {"location": None, "video_url": None, "url": None, "calendar": None}
    events = [
        {
            **base,
            "title": f"Name day {i}",
            "all_day": True,
            "start": tomorrow,
            "end": tomorrow + timedelta(days=1),
        }
        for i in range(6)
    ] + [
        {
            **base,
            "title": "Flight to Bratislava",
            "all_day": False,
            "location": "VIE",
            "start": tomorrow + timedelta(hours=19),
            "end": tomorrow + timedelta(hours=21),
        },
        {
            **base,
            "title": "Dentist",
            "all_day": False,
            "start": tomorrow + timedelta(days=3, hours=9),
            "end": tomorrow + timedelta(days=3, hours=10),
        },
    ]
    plugin = CalendarPlugin()

    async def accounts(context):
        return [AccountData("me@example.com", {"events": events})]

    monkeypatch.setattr(plugin, "_accounts", accounts)
    section = asyncio.run(
        plugin.brief(
            SimpleNamespace(now=now, connections=NoConnections(), sessionmaker=NoIgnored())
        )
    )
    assert "Tomorrow 19:00–21:00: Flight to Bratislava (2 h · VIE)" in section.lines
    assert sum(line.startswith("Tomorrow all day") for line in section.lines) == 6
    assert any(line.startswith("Later this week, Monday 09:00: Dentist") for line in section.lines)


def test_free_blocks_for_focus_today(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from myorb.plugins.calendar import CalendarPlugin
    from myorb.plugins.google_data import AccountData

    now = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("Europe/Bratislava"))
    base = {"location": None, "video_url": None, "url": None, "calendar": None, "all_day": False}

    def at(hour: int, minute: int = 0) -> datetime:
        return now.replace(hour=hour, minute=minute)

    events = [
        {**base, "title": "Standup", "start": at(9), "end": at(9, 15)},
        {**base, "title": "Planning", "start": at(11), "end": at(12)},
        # The user's own lunch: lunch, even shown as free in Google Calendar.
        {**base, "title": "Lunch", "start": at(12, 30), "end": at(13), "free": True},
        {**base, "title": "Review", "start": at(15), "end": at(16)},
    ]
    plugin = CalendarPlugin()

    async def accounts(context):
        return [AccountData("me@example.com", {"events": events})]

    monkeypatch.setattr(plugin, "_accounts", accounts)
    context = SimpleNamespace(
        now=now, params={}, connections=NoConnections(), sessionmaker=NoIgnored()
    )
    section = asyncio.run(plugin.brief(context))
    assert (
        "Free for focus today (an hour or more, until 18:00): "
        "10:00–11:00 (1 h), 13:00–15:00 (2 h), 16:00–18:00 (2 h)" in section.lines
    )
    data = asyncio.run(plugin._widget(context))
    assert [(s.value, s.filter) for s in data.stats if s.label == "Focus"] == [("5 h", "focus")]
    rows = [(r.title, r.badge) for r in data.rows]
    assert rows == [
        ("Standup", "Over"),
        ("Free for focus", "10:00"),
        ("Planning", "11:00"),
        ("Lunch", "12:30"),
        ("Free for focus", "13:00"),
        ("Review", "15:00"),
        ("Free for focus", "16:00"),
    ]
    assert data.rows[1].tags == ("today", "focus") and data.rows[1].subtitle == "10:00–11:00 · 1 h"
    panel = asyncio.run(plugin._today(context))
    kinds = [e["kind"] for e in panel["day"]["today"]]
    assert kinds == ["past", "now", "focus", "event", "lunch", "focus", "event", "focus"]
    # Nothing left once the working day is over.
    late = SimpleNamespace(
        now=at(17, 30), params={}, connections=NoConnections(), sessionmaker=NoIgnored()
    )
    assert any(line.startswith("No free block") for line in asyncio.run(plugin.brief(late)).lines)


def test_lunch_is_kept_out_of_focus_time(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from myorb.plugins.calendar import CalendarPlugin
    from myorb.plugins.google_data import AccountData

    now = datetime(2026, 10, 27, 8, 17, tzinfo=ZoneInfo("Europe/Bratislava"))
    base = {"location": None, "video_url": None, "url": None, "calendar": None, "all_day": False}

    def at(hour: int, minute: int = 0) -> datetime:
        return now.replace(hour=hour, minute=minute)

    events = [
        {**base, "title": "Refinement", "start": at(10), "end": at(11, 30)},
        {**base, "title": "Purchases", "start": at(13), "end": at(13, 30)},
        {**base, "title": "Madero", "start": at(14), "end": at(15)},
        {**base, "title": "Open day", "start": at(17), "end": at(19)},
    ]
    plugin = CalendarPlugin()

    async def accounts(context):
        return [AccountData("me@example.com", {"events": events})]

    monkeypatch.setattr(plugin, "_accounts", accounts)
    context = SimpleNamespace(
        now=now, params={}, connections=NoConnections(), sessionmaker=NoIgnored()
    )
    data = asyncio.run(plugin._widget(context))
    # 11:30–13:00 is free, but lunch takes its middle: no hour of focus is left around it.
    assert [(r.title, r.badge) for r in data.rows][:6] == [
        ("Free for focus", "09:00"),
        ("Refinement", "10:00"),
        ("Lunch", "12:00"),
        ("Purchases", "13:00"),
        ("Madero", "14:00"),
        ("Free for focus", "15:00"),
    ]
    assert data.rows[2].subtitle == "12:00–12:45 · 45 m"
    section = asyncio.run(plugin.brief(context))
    assert "Lunch kept free: 12:00–12:45" in section.lines
    panel = asyncio.run(plugin._today(context))
    assert "lunch" in [e["kind"] for e in panel["day"]["today"]]

    # Lunch where it leaves an hour of focus: at the start of a long free stretch.
    events[1:2] = [{**base, "title": "Purchases", "start": at(13, 45), "end": at(14)}]
    data = asyncio.run(plugin._widget(context))
    lunch = next(r for r in data.rows if r.title == "Lunch")
    assert lunch.subtitle == "11:30–12:15 · 45 m"
    assert any(r.subtitle == "12:15–13:45 · 1 h 30 m" for r in data.rows)
    # The user's own lunch in the calendar takes its place, shown as lunch, not a meeting.
    events.append({**base, "title": "Obed", "start": at(12), "end": at(12, 30)})
    data = asyncio.run(plugin._widget(context))
    assert not any(r.title == "Lunch" for r in data.rows)
    assert [(r.title, r.tags) for r in data.rows if r.title == "Obed"] == [
        ("Obed", ("today", "lunch"))
    ]
    assert data.value == 4  # not one of today's events


def test_node_size_follows_attention():
    from myorb.plugins import WidgetData
    from myorb.web.routes import node_size

    assert node_size(1.0, WidgetData(value=60, attention=True)) == 1.25
    assert node_size(1.0, WidgetData(value=8)) == 1.0
    assert node_size(1.3, WidgetData(value=0)) == 0.92  # empty, even for a heavy widget
    assert node_size(1.0, WidgetData(value=None, message="Failed", message_tone="fail")) == 1.25
    assert node_size(2.0, WidgetData(value=1, attention=True)) == 1.4  # capped


def test_open_buttons_cover_every_account():
    from myorb.plugins import Link, open_buttons

    assert open_buttons("Open Jira", []) == {}
    assert open_buttons("Open Jira", [("archivis", "https://a/")]) == {
        "action": Link("Open Jira", "https://a/")
    }
    assert open_buttons("Open Jira", [("archivis", "https://a/"), ("swida", "https://s/")]) == {
        "links": [Link("Open archivis", "https://a/"), Link("Open swida", "https://s/")]
    }


# Reading an email


def test_email_opens_in_myorb_and_is_marked_read(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    page = client.get("/email/1/t1").text
    assert '<h1 class="email-subject">Board meeting &lt;today&gt;</h1>' in page
    assert "Pepper Potts" in page and "pepper@stark.example" in page
    # The email's HTML in a frame without scripts, its images blocked until asked for.
    assert 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in page
    assert "img-src data:&#34;" in page or "img-src data:&quot;" in page
    assert "agenda.pdf · 245 kB" in page
    # Images sent inside the email show without asking, and are not listed as attachments.
    assert "data:image/png;base64,iVBORw0KGgo/+w==" in page and "cid:" not in page
    assert "logo.png" not in page
    assert "Mark as read" in page and 'action="/email/1/t1/images"' in page
    # Back opens the email details again rather than the bare welcome screen.
    assert 'href="/#detail-gmail-inbox"' in page
    shown = client.get("/email/1/t1", params={"images": "true"}).text
    assert "https:" in shown.split("img-src")[1][:30]
    # Asked for without a referrer: some senders turn other sites' requests down.
    assert "name=&#34;referrer&#34; content=&#34;no-referrer&#34;" in shown or (
        "name=&quot;referrer&quot; content=&quot;no-referrer&quot;" in shown
    )

    modified.clear()
    response = client.post("/email/1/t1/read", follow_redirects=False)
    assert response.headers["location"] == "/email/1/t1?read=true"
    assert modified == ['{"removeLabelIds":["UNREAD"]}']
    assert client.post("/email/1/t1/read", headers={"HX-Request": "true"}).status_code == 204
    # Gmail's search still finds it unread for a while; the list leaves it out already.
    assert 'href="/email/1/t1"' not in client.get("/welcome/widgets").text
    assert client.get("/email/9/t1").status_code == 404


def test_images_are_remembered_for_the_senders_domain(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    blocked = client.get("/email/1/t1").text
    assert '<input type="hidden" name="domain" value="stark.example">' in blocked
    assert "img-src data:&#34;" in blocked or "img-src data:&quot;" in blocked

    response = client.post(
        "/email/1/t1/images", data={"domain": "stark.example"}, follow_redirects=False
    )
    assert response.headers["location"] == "/email/1/t1"
    shown = client.get("/email/1/t1").text
    assert "https:" in shown.split("img-src")[1][:30]
    assert "Images from stark.example are shown." in shown

    client.post("/email/1/t1/images", data={"domain": "stark.example", "show": "false"})
    assert "are shown." not in client.get("/email/1/t1").text
    assert "https:" not in client.get("/email/1/t1").text.split("img-src")[1][:30]


def test_email_opens_over_the_details(client: TestClient, fake_google: FakeGoogle):
    connect_google(client, fake_google)
    view = {"HX-Request": "true", "HX-Target": "page-view"}
    page = client.get("/email/1/t1", headers=view).text
    assert "<html" not in page and "email-subject" in page
    assert "data-close-view" in page and 'hx-post="/email/1/t1/read"' in page
    response = client.post("/email/1/t1/read", headers=view)
    assert "Marked as read." in response.text
    assert response.headers["HX-Trigger"] == '{"myorb:row-done": "/email/1/t1"}'
    shown = client.post("/email/1/t1/images", data={"domain": "stark.example"}, headers=view)
    assert "Images from stark.example are shown." in shown.text and "<html" not in shown.text


def test_marking_read_needs_the_permission(client: TestClient, fake_google: FakeGoogle):
    fake_google.granted_scopes = " ".join(
        s for s in fake_google.granted_scopes.split() if not s.endswith("gmail.modify")
    )
    connect_google(client, fake_google)
    assert "/email/1/t1/read" not in client.get("/welcome/widgets").text
    page = client.get("/email/1/t1").text
    assert "Mark as read" not in page and "connect this Google account again" in page
    assert client.post("/email/1/t1/read").status_code == 403


def test_pages_ask_proxies_not_to_change_them(client: TestClient):
    # Cloudflare would hide every email address on the page behind "[email protected]".
    assert client.get("/").headers["cache-control"] == "no-transform"
    assert "no-transform" not in client.get("/static/style.css").headers["cache-control"]


def test_rows_keep_their_fields_by_position():
    # Plugins pass a row's first fields by position (Jira, YouTube…): new ones go last.
    from myorb.plugins import Row

    row = Row("AR-16 Import", "Due 26 Feb", "26 Feb", "fail", "https://jira.example/AR-16")
    assert (row.subtitle, row.badge, row.tone, row.url, row.mark) == (
        "Due 26 Feb",
        "26 Feb",
        "fail",
        "https://jira.example/AR-16",
        "",
    )


# Commute


def test_commute_plans_when_to_leave():
    import asyncio
    from datetime import datetime

    from myorb.plugins.calendar.commute import plan

    now = datetime(2026, 10, 26, 7, 0, tzinfo=TZ)  # a Monday

    def event(hour: int, title: str, location: str | None, minutes: int = 60) -> dict:
        start = now.replace(hour=hour)
        return {
            "title": title,
            "start": start,
            "end": start + timedelta(minutes=minutes),
            "all_day": False,
            "location": location,
            "kind": "event",
        }

    events = [
        {**event(0, "Name day", None), "all_day": True},
        event(8, "Quarterly meeting", "Hlavná 1, Košice"),
        event(9, "Refinement", "Online, link in the invitation"),  # nowhere to go
        event(10, "Workshop", "Mlynská 5, Košice"),  # an hour after the meeting: from there
        event(15, "1:1", "Office, Košice"),  # from work on a weekday afternoon
        event(20, "Flight to Prague", "Košice Airport"),  # from home, long after the 1:1
    ]
    asked = []

    async def route(origin: str, destination: str, departure: datetime) -> int | None:
        asked.append((origin, destination, departure))
        return 25

    credentials = {"home": "Home St 1", "work": "Work St 2", "mode": "drive", "api_key": "k"}
    trips = asyncio.run(plan(events, now, credentials, route))
    assert [(t["event"]["title"], t["origin"], f"{t['start']:%H:%M}") for t in trips] == [
        ("Quarterly meeting", "home", "07:30"),  # 25 min plus 5 min to spare before 08:00
        ("Workshop", "Hlavná 1, Košice", "09:30"),  # from where the meeting was
        ("1:1", "work", "14:30"),
        ("Flight to Prague", "home", "18:05"),  # at the airport 90 minutes before
    ]
    assert asked[1][0] == "Hlavná 1, Košice" and trips[0]["length"] == "25 min"
    # The traffic of when the user leaves, never in the past.
    assert asked[0][2] == now.replace(hour=7, minute=25)
    late = asyncio.run(plan(events, now.replace(hour=10, minute=5), credentials, route))
    assert [t["event"]["title"] for t in late] == ["1:1", "Flight to Prague"]
    assert late[0]["origin"] == "work"


def test_commute_asks_google_routes():
    import asyncio
    import json as jsonlib

    import httpx2

    from myorb.connections.providers import ProviderError
    from myorb.plugins.calendar.commute import Commute, route_minutes

    sent = []

    def routes(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.headers, jsonlib.loads(request.content)))
        if request.headers["X-Goog-Api-Key"] == "bad":
            return httpx2.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})
        if "Nowhere" in request.content.decode():
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json={"routes": [{"duration": "1530s"}]})

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(routes)) as http:
            credentials = {"api_key": "key", "mode": "drive"}
            when = datetime(2026, 10, 26, 7, 25, tzinfo=TZ)
            assert await route_minutes(http, credentials, "A", "B", when) == 26
            headers, body = sent[-1]
            assert headers["X-Goog-FieldMask"] == "routes.duration"
            assert body["travelMode"] == "DRIVE" and body["routingPreference"] == "TRAFFIC_AWARE"
            assert body["departureTime"] == "2026-10-26T06:25:00Z"  # CET
            transit = {"api_key": "key", "mode": "transit"}
            await route_minutes(http, transit, "A", "B")
            assert sent[-1][1]["travelMode"] == "TRANSIT" and "routingPreference" not in sent[-1][1]
            assert await route_minutes(http, credentials, "A", "Nowhere") is None
            provider = Commute()
            values = {"api_key": "key", "home": "Home", "work": "", "mode": ""}
            verified = await provider.verify(http, values)
            assert verified.credentials["mode"] == "drive" and verified.account == "From Home"
            with pytest.raises(ProviderError, match="no way from home to work"):
                await provider.verify(http, {**values, "work": "Nowhere"})
            with pytest.raises(ProviderError, match="rejected"):
                await provider.verify(http, {**values, "api_key": "bad"})
            with pytest.raises(ProviderError, match="drive, transit"):
                await provider.verify(http, {**values, "mode": "boat"})

    asyncio.run(run())


def test_commute_pauses_after_google_refuses():
    from myorb.plugins.calendar.commute import PAUSE, Pause

    now = [0.0]
    pause = Pause(clock=lambda: now[0])
    key, other = {"api_key": "key"}, {"api_key": "new"}
    assert not pause.paused(key)
    pause.refused(key)
    assert pause.paused(key)
    assert not pause.paused(other)  # a key changed in the settings is asked with at once
    now[0] = PAUSE - 1
    assert pause.paused(key)
    now[0] = PAUSE
    assert not pause.paused(key)


def test_commute_shows_when_to_leave(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from myorb.plugins.calendar import CalendarPlugin
    from myorb.plugins.google_data import AccountData

    now = datetime(2026, 10, 26, 7, 0, tzinfo=TZ)
    base = {"video_url": None, "url": None, "calendar": None, "all_day": False}
    meeting = {
        **base,
        "title": "Quarterly meeting",
        "location": "Hlavná 1",
        "start": now.replace(hour=8),
        "end": now.replace(hour=10),
    }
    plugin = CalendarPlugin()

    async def accounts(context):
        return [AccountData("me@example.com", {"events": [meeting]})]

    async def trips(context, events):
        [event] = [e for e in events if e.get("kind") not in ("now", None)]
        start = now.replace(hour=7, minute=10)
        return [
            {
                "event": event,
                "start": start,
                "end": now.replace(hour=7, minute=55),
                "minutes": 45,
                "length": "45 min",
                "origin": "home",
                "by": "by car",
                "flight": False,
            }
        ]

    monkeypatch.setattr(plugin, "_accounts", accounts)
    monkeypatch.setattr(plugin, "_trips", trips)
    context = SimpleNamespace(now=now, params={}, sessionmaker=NoIgnored())
    data = asyncio.run(plugin._widget(context))
    assert [(r.title, r.badge, r.tone) for r in data.rows][:2] == [
        ("Leave for Quarterly meeting", "07:10", "warn"),  # in a quarter of an hour
        ("Quarterly meeting", "08:00", "info"),
    ]
    assert data.rows[0].subtitle == "45 min by car from home"
    section = asyncio.run(plugin.brief(context))
    assert (
        "Leave at 07:10 for Quarterly meeting (08:00, Hlavná 1): 45 min by car from home "
        "in the traffic expected then" in section.lines
    )
    # The way is no time for focus: free from 10:00, not from 07:00 (and lunch at noon).
    assert any("10:00–12:00 (2 h), 12:45–18:00" in line for line in section.lines)
    nxt = asyncio.run(plugin._next(context))
    assert nxt["leave"] == now.replace(hour=7, minute=10) and nxt["leave_soon"]
    panel = asyncio.run(plugin._today(context))
    assert [e["kind"] for e in panel["day"]["today"]][:3] == ["now", "travel", "event"]


def test_commute_is_set_up_on_integrations(client: TestClient):
    assert "<strong>Commute</strong>" in client.get("/integrations").text
    form = client.get("/integrations/commute/connect").text
    assert 'name="home"' in form and 'name="api_key"' in form and "Routes API" in form


def test_attachments_open_with_a_preview(client: TestClient, fake_google: FakeGoogle):
    import io

    import pypdfium2
    from PIL import Image

    pdf = pypdfium2.PdfDocument.new()
    pdf.new_page(200, 300)
    pdf_bytes = io.BytesIO()
    pdf.save(pdf_bytes)
    png = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(png, "PNG")

    def encoded(content: bytes) -> str:
        return base64.urlsafe_b64encode(content).decode()

    def part(part_id: str, name: str, kind: str, attachment: str) -> dict:
        return {
            "partId": part_id,
            "filename": name,
            "mimeType": kind,
            "body": {"attachmentId": attachment, "size": 1000},
        }

    message = {
        "id": "m9",
        "threadId": "t9",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "UpCloud <billing@upcloud.example>"}],
            "parts": [
                {"partId": "0", "mimeType": "text/plain", "body": {"data": encoded(b"Paid.")}},
                part("1", "invoice.pdf", "application/octet-stream", "a-pdf"),
                part("2", "photo.png", "image/png", "a-png"),
                part("3", "page.html", "text/html", "a-html"),
            ],
        },
    }
    contents = {"a-pdf": pdf_bytes.getvalue(), "a-png": png.getvalue(), "a-html": b"<script>"}

    def gmail(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        if path == "/threads/t9":
            return httpx2.Response(200, json={"messages": [message]})
        if path == "/messages/m9":
            return httpx2.Response(200, json=message)
        if path.startswith("/messages/m9/attachments/"):
            attachment = path.rsplit("/", 1)[1]
            return httpx2.Response(200, json={"data": encoded(contents[attachment])})
        return gmail_api(request)

    fake_google.routes[GMAIL] = gmail
    connect_google(client, fake_google)
    page = client.get("/email/1/t9").text
    # Cards, as in Gmail: a picture of the PDF's first page and of the image.
    assert (
        'href="/email/1/attachment/m9/1"' in page
        and 'src="/email/1/attachment/m9/1/thumbnail"' in page
    )
    assert 'src="/email/1/attachment/m9/2/thumbnail"' in page
    assert "/m9/3/thumbnail" not in page and ">HTML<" in page
    # A PDF (its type from its name) and an image show in the browser.
    shown = client.get("/email/1/attachment/m9/1")
    assert shown.headers["content-type"] == "application/pdf"
    assert shown.headers["content-disposition"].startswith("inline")
    assert shown.content == pdf_bytes.getvalue()
    # HTML never runs from myOrb's address: it is downloaded.
    html = client.get("/email/1/attachment/m9/3")
    assert html.headers["content-type"] == "application/octet-stream"
    assert html.headers["content-disposition"].startswith("attachment")
    for part_id in ("1", "2"):
        picture = client.get(f"/email/1/attachment/m9/{part_id}/thumbnail")
        assert picture.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(picture.content)).width == 360
    assert client.get("/email/1/attachment/m9/3/thumbnail").status_code == 404
    assert client.get("/email/1/attachment/m9/7").status_code == 404


def test_an_event_is_ignored_and_counted_again(client: TestClient, fake_google: FakeGoogle):
    now = datetime.now(TZ)

    def calendar(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/calendarList"):
            items = [{"id": "family@group.example", "selected": True, "summary": "Family"}]
            return httpx2.Response(200, json={"items": items})
        # A day of someone else's repeating event, in a shared calendar.
        event = {
            "id": "madero_20261027",
            "recurringEventId": "madero",
            "summary": "LuciA madero",
            "start": {"dateTime": (now - timedelta(minutes=1)).isoformat()},
            "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
        }
        return httpx2.Response(200, json={"items": [event]})

    fake_google.routes[CALENDAR] = calendar
    connect_google(client, fake_google)
    text = client.get("/welcome/widgets").text
    assert 'hx-post="/calendar/ignore?key=family%40group.example%2Fmadero"' in text
    assert (
        client.post("/calendar/ignore", params={"key": "family@group.example/madero"}).status_code
        == 204
    )

    text = client.get("/welcome/widgets").text
    # Still listed, grey, with the button to count it again; no longer one of today's.
    assert 'data-tags="today ignored"' in text and ">Count again<" in text
    assert "· Ignored" in text
    panel = client.get("/panels/calendar/today").text
    assert 'class="ti ignored"' in panel

    client.post("/calendar/count", params={"key": "family@group.example/madero"})
    assert ">Ignore<" in client.get("/welcome/widgets").text
    assert client.post("/calendar/ignore", params={"key": "x"}).status_code == 400


def test_ignored_events_take_no_time(monkeypatch):
    from types import SimpleNamespace

    from myorb.plugins.google_data import AccountData

    now = datetime(2026, 10, 27, 8, 0, tzinfo=TZ)
    base = {"location": None, "video_url": None, "url": None, "all_day": False}

    def event(title: str, hour: int, calendar: str | None = None) -> dict:
        start = now.replace(hour=hour)
        return {
            **base,
            "title": title,
            "start": start,
            "end": start + timedelta(hours=1),
            "id": title,
            "calendar": calendar,
            "calendar_id": calendar or "me",
        }

    events = [event("Refinement", 10), event("LuciA madero", 14, "Family")]
    plugin = CalendarPlugin()

    async def accounts(context):
        return [AccountData("me@example.com", {"events": events})]

    async def ignored(context):
        return {"Family/LuciA madero"}

    monkeypatch.setattr(plugin, "_accounts", accounts)
    monkeypatch.setattr(plugin, "_ignored", ignored)
    context = SimpleNamespace(now=now, params={}, connections=NoConnections())
    assert [i.title for i in asyncio.run(plugin.timeline(context))] == ["Refinement"]
    lines = asyncio.run(plugin.brief(context)).lines
    assert not any("LuciA" in line for line in lines)
    # Its hour is free for focus: 11:00–18:00, less lunch.
    assert any("12:45–18:00 (5 h 15 m)" in line for line in lines)
    data = asyncio.run(plugin._widget(context))
    assert data.value == 1 and [r.title for r in data.rows if "ignored" in r.tags] == [
        "LuciA madero"
    ]
    assert asyncio.run(plugin.ignored_today(context)) == {("LuciA madero", now.replace(hour=14))}


def test_the_report_follows_an_ignored_event(client: TestClient, fake_google: FakeGoogle):
    from myorb.brief.models import Brief

    now = datetime.now(TZ).replace(second=0, microsecond=0)
    start, end = now + timedelta(minutes=30), now + timedelta(minutes=90)

    def calendar(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/calendarList"):
            items = [{"id": "family@group.example", "selected": True, "summary": "Family"}]
            return httpx2.Response(200, json={"items": items})
        event = {
            "id": "madero",
            "summary": "LuciA madero",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        return httpx2.Response(200, json={"items": [event]})

    fake_google.routes[CALENDAR] = calendar
    connect_google(client, fake_google)

    async def brief() -> None:
        async with client.app.state.sessionmaker() as db:
            timeline = [
                {"start": start.isoformat(), "end": end.isoformat(), "title": "LuciA madero"}
            ]
            db.add(
                Brief(
                    created_at=datetime.now(TZ) - timedelta(minutes=5),
                    kind="morning",
                    content={"headline": "", "summary": [], "priorities": [], "timeline": timeline},
                    sources=["Calendar"],
                    model="claude-sonnet-5-5",
                    language="en",
                )
            )
            await db.commit()

    client.portal.call(brief)
    # Loaded again whenever it is opened (see welcome.js).
    welcome = client.get("/").text
    assert re.search(
        r'class="report" hx-get="[^"]*/brief/report" hx-trigger="[^"]*myorb:open"', welcome
    )
    report = client.get("/brief/report").text
    on_the_day = 'class="glance-tip"><strong>LuciA madero</strong>'
    assert on_the_day in report and "The calendar changed" not in report

    client.post("/calendar/ignore", params={"key": "family@group.example/madero"})
    report = client.get("/brief/report").text
    # Gone from the day, and the brief, written with it, says to write it again.
    assert on_the_day not in report
    assert "The calendar changed since this brief was written" in report
    assert "<strong>LuciA madero</strong> at" not in report
    assert "LuciA madero</strong> ignored" in report


def test_unsubscribe_and_images_in_the_email_header(client: TestClient, fake_google: FakeGoogle):
    from myorb.plugins.gmail.message import unsubscribe_link

    assert (
        unsubscribe_link(
            '<p>Tento e-mail <a href="https://a.cz/u?x=1&amp;y=2">Odhlásit se z odběru</a>'
        )
        == "https://a.cz/u?x=1&y=2"
    )
    assert unsubscribe_link('<a href="https://news.example/unsubscribe/abc">here</a>')
    # Subscribing is no way out, nor is a mail link.
    assert not unsubscribe_link('<a href="https://x.sk/odber">Prihlásiť sa na odber</a>')
    assert not unsubscribe_link('<a href="mailto:u@x.example">Unsubscribe</a>')

    connect_google(client, fake_google)
    page = client.get("/email/1/t1").text
    head = page.split('<div class="email-top">')[1].split("</div>\n</div>")[0]
    # The email blocks its images: the button is in the header too; it has no unsubscribe link.
    assert "Show images" in head and "email-actions-end" in head
    assert "Unsubscribe" not in head
    # On a phone the rest of the actions are behind ⋮.
    assert '<details class="email-more">' in head and 'aria-label="More actions"' in head
