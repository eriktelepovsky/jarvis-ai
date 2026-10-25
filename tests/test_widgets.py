import asyncio
import base64
import json
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
    section = asyncio.run(plugin.brief(SimpleNamespace(now=now)))
    assert "Tomorrow 19:00–21:00: Flight to Bratislava (2 h · VIE)" in section.lines
    assert sum(line.startswith("Tomorrow all day") for line in section.lines) == 6
    assert any(line.startswith("Later this week, Monday 09:00: Dentist") for line in section.lines)


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
    assert (
        "https:"
        in client.get("/email/1/t1", params={"images": "true"}).text.split("img-src")[1][:30]
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
