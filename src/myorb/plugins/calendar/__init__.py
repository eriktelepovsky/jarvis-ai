import asyncio
import logging
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from myorb.connections.google_accounts import GoogleAccount, GoogleAccounts
from myorb.connections.providers import Provider, ProviderError
from myorb.overview.glance import duration, focus_blocks
from myorb.plugins.base import (
    AskAction,
    BriefSection,
    Panel,
    Plugin,
    Row,
    Stat,
    TimelineItem,
    Widget,
    WidgetContext,
    WidgetData,
    open_buttons,
)
from myorb.plugins.calendar.commute import Commute, plan, route_minutes
from myorb.plugins.google_data import AccountData, account_status, load_accounts

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3"
API_NAME = "Google Calendar API"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
# Adding and changing events from Ask; accounts connected before it was asked for only read.
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DAYS_AHEAD = 7
EVENTS_PER_CALENDAR = 25
ICON = "M3 5h18v16H3zM3 10h18M8 3v4M16 3v4"
# Event types that are not meetings and would only clutter the list.
HIDDEN_EVENT_TYPES = {"workingLocation"}


def calendar_url(email: str) -> str:
    # "/calendar/u/?authuser=" is a 404; "/calendar/r" picks the account from authuser.
    return f"https://calendar.google.com/calendar/r?authuser={quote(email)}"


def parse_event(
    event: dict[str, Any], calendar: dict[str, Any], timezone: tzinfo
) -> dict[str, Any] | None:
    """Turn a Google Calendar event into what the widget shows, or None to hide it."""
    if event.get("status") == "cancelled" or event.get("eventType") in HIDDEN_EVENT_TYPES:
        return None
    attendees = event.get("attendees", [])
    if any(a.get("self") and a.get("responseStatus") == "declined" for a in attendees):
        return None
    start, end = event.get("start", {}), event.get("end", {})
    all_day = "dateTime" not in start
    if all_day:
        starts = datetime.combine(date.fromisoformat(start["date"]), datetime.min.time(), timezone)
        ends = datetime.combine(date.fromisoformat(end["date"]), datetime.min.time(), timezone)
    else:
        starts = datetime.fromisoformat(start["dateTime"]).astimezone(timezone)
        ends = datetime.fromisoformat(end["dateTime"]).astimezone(timezone)
    video = event.get("hangoutLink") or next(
        (
            entry.get("uri")
            for entry in event.get("conferenceData", {}).get("entryPoints", [])
            if entry.get("entryPointType") == "video"
        ),
        None,
    )
    return {
        "title": event.get("summary") or "(No title)",
        "start": starts,
        "end": ends,
        "all_day": all_day,
        "location": event.get("location"),
        "video_url": video,
        "url": event.get("htmlLink"),
        "calendar": None if calendar.get("primary") else calendar.get("summary"),
        # Shown as "Free" in Google Calendar: it leaves the time open for focus.
        "free": event.get("transparency") == "transparent",
        # What Ask needs to change the event (see actions.py).
        "id": event.get("id", ""),
        "calendar_id": calendar.get("id", ""),
    }


def event_ref(event: dict[str, Any]) -> str:
    """ "event 1 primary@x.example 7kq3…": the account in myOrb, the calendar and the event.
    Events cached by an earlier version have none."""
    if not event.get("id") or not event.get("account_id"):
        return ""
    return f"event {event['account_id']} {event['calendar_id']} {event['id']}"


def describe(event: dict[str, Any]) -> str:
    """The line under an event title: duration, then where it happens."""
    parts = []
    if not event["all_day"]:
        minutes = int((event["end"] - event["start"]).total_seconds() // 60)
        hours, rest = divmod(minutes, 60)
        parts.append(
            f"{hours} h {rest} min" if hours and rest else f"{hours} h" if hours else f"{rest} min"
        )
    if event["video_url"] and not event["location"]:
        parts.append("Video call")
    parts += [part for part in (event["location"], event["calendar"]) if part]
    return " · ".join(parts)


def today_and_upcoming(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Split events into today's (with a "now" marker) and the next days'."""
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today: list[dict[str, Any]] = []
    marked = False
    for event in [e for e in events if e["start"] < tomorrow]:
        kind = "past" if event["end"] <= now else "event"
        if not marked and event["start"] > now:
            today.append({"kind": "now", "time": now})
            marked = True
        today.append({**event, "kind": kind, "sub": describe(event)})
    if today and not marked and not all(e.get("all_day") for e in today):
        today.append({"kind": "now", "time": now})
    upcoming = [e for e in events if e["start"] >= tomorrow]
    return {
        "today": today,
        "today_count": sum(1 for e in today if e["kind"] != "now"),
        "upcoming": upcoming,
    }


def events_on(events: list[dict[str, Any]], date: Any) -> list[dict[str, Any]]:
    """A whole other day's events (all-day ones first), for looking ahead in the side panel."""
    tz = events[0]["start"].tzinfo if events else None
    start = datetime.combine(date, datetime.min.time(), tz)
    end = start + timedelta(days=1)
    seen: set[tuple[str, datetime]] = set()
    day = []
    for event in sorted(events, key=lambda e: (not e["all_day"], e["start"])):
        key = (event["title"], event["start"])
        if event["start"] < end and event["end"] > start and key not in seen:
            seen.add(key)
            day.append({**event, "kind": "event", "sub": describe(event)})
    return day


def focus_time(
    events: list[dict[str, Any]], now: datetime, trips: list[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The rest of today's free blocks for deep work, between the events that take the time
    and the ways to them (see commute.plan)."""
    busy = [
        TimelineItem(e["start"], e["end"], e["title"])
        for e in events
        if e.get("kind") != "now" and not e["all_day"] and not e.get("free")
    ] + [TimelineItem(t["start"], t["end"], "Travel") for t in trips]
    return [
        {"start": start, "end": end, "length": duration(int((end - start).total_seconds() // 60))}
        for start, end in focus_blocks(busy, now)
    ]


def among_events(
    events: list[dict[str, Any]], extras: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Today's events with free blocks (kind "focus") and ways to them (kind "travel") among
    them, in time order. Only among meetings: a day without any is all free, and says so."""
    day = list(events)
    if not any(e["kind"] != "now" and not e["all_day"] for e in day):
        return day
    for block in extras:
        index = next(
            (
                i
                for i, e in enumerate(day)
                if e["kind"] != "now" and not e["all_day"] and e["start"] >= block["start"]
            ),
            len(day),
        )
        day.insert(index, {"kind": "focus", **block, "all_day": False})
    return day


def trip_line(trip: dict[str, Any]) -> str:
    event = trip["event"]
    traffic = " in the traffic expected then" if trip["by"] == "by car" else ""
    return (
        f"Leave at {trip['start']:%H:%M} for {event['title']} ({event['start']:%H:%M}, "
        f"{event['location']}): {trip['length']} {trip['by']} from {trip['origin']}{traffic}"
        + (", arriving 90 minutes before the flight" if trip["flight"] else "")
    )


def focus_line(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return "No free block of an hour or more left in today's working hours."
    spans = ", ".join(f"{b['start']:%H:%M}–{b['end']:%H:%M} ({b['length']})" for b in blocks)
    return f"Free for focus today (an hour or more, until 18:00): {spans}"


def until(start: datetime, now: datetime) -> str:
    """How long until an event starts, e.g. "in 1 h 18 m", or "now" when it has started."""
    minutes = int((start - now).total_seconds() // 60)
    if minutes <= 0:
        return "now"
    hours, rest = divmod(minutes, 60)
    return "in " + (f"{hours} h " if hours else "") + f"{rest} m"


def event_badge(event: dict[str, Any], now: datetime) -> str:
    if event["start"].date() <= now.date():
        return "All day" if event["all_day"] else event["start"].strftime("%H:%M")
    return event["start"].strftime("%a" if event["all_day"] else "%a %H:%M")


async def fetch_events(
    google: GoogleAccounts, account: GoogleAccount, now: datetime
) -> dict[str, Any]:
    calendar_list = await google.get_json(
        account, f"{API}/users/me/calendarList", API_NAME, {"minAccessRole": "reader"}
    )
    calendars = [
        calendar
        for calendar in calendar_list.get("items", [])
        if calendar.get("selected") or calendar.get("primary")
    ]
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        # Events are listed by their end time: everything that ends today or later.
        "timeMin": day_start.isoformat(),
        "timeMax": (day_start + timedelta(days=DAYS_AHEAD)).isoformat(),
        "maxResults": EVENTS_PER_CALENDAR,
    }
    responses = await asyncio.gather(
        *(
            google.get_json(
                account,
                f"{API}/calendars/{quote(calendar['id'], safe='')}/events",
                API_NAME,
                params,
            )
            for calendar in calendars
        )
    )
    events = [
        {**parsed, "account_id": account.id}
        for calendar, response in zip(calendars, responses, strict=True)
        for event in response.get("items", [])
        if (parsed := parse_event(event, calendar, now.tzinfo)) is not None
    ]
    # All-day events first within a day, then by start time.
    events.sort(key=lambda e: (max(e["start"], day_start).date(), not e["all_day"], e["start"]))
    return {"events": events, "calendars": len(calendars)}


class CalendarPlugin(Plugin):
    """Today's and upcoming events from connected Google Calendars."""

    id = "calendar"
    name = "Calendar"
    templates_dir = Path(__file__).parent / "templates"
    position = 20

    def __init__(self) -> None:
        self.commute = Commute()

    def connections(self) -> list[Provider]:
        return [self.commute]

    async def _trips(
        self, context: WidgetContext, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Today's ways to the events ahead, when Commute is set up (see commute.plan)."""
        accounts = await context.connections.list(self.commute.id)
        if not accounts:
            return []
        account = accounts[0]

        async def route(origin: str, destination: str, departure: datetime) -> int | None:
            # The same way asked again within a quarter of an hour comes from the cache.
            slot = departure.replace(minute=departure.minute // 15 * 15, second=0, microsecond=0)
            key = f"commute:{account.id}:{origin}|{destination}|{slot:%Y-%m-%d %H:%M}"

            async def fetch() -> int | None:
                return await route_minutes(
                    context.http, account.credentials, origin, destination, departure
                )

            try:
                cached = await context.cache.get(key, fetch, fresh=15 * 60, max_age=60 * 60)
            except ProviderError:
                return None
            return cached.value

        try:
            return await plan(events, context.now, account.credentials, route)
        except Exception:
            logger.exception("Commute failed")
            return []

    def ask_actions(self) -> list[AskAction]:
        from myorb.plugins.calendar.actions import ask_actions

        return ask_actions()

    def widgets(self) -> list[Widget]:
        return [Widget(id="today", label="Events", title="Calendar", icon=ICON, load=self._widget)]

    def panels(self) -> list[Panel]:
        return [
            Panel(id="today", title="Schedule", template="today.html", load=self._today),
            Panel(
                id="next", title="Next", template="next.html", load=self._next, placement="header"
            ),
        ]

    async def _accounts(self, context: WidgetContext) -> list[AccountData]:
        async def fetch(account: GoogleAccount) -> dict[str, Any]:
            return await fetch_events(context.google, account, context.now)

        result = await load_accounts(
            context,
            SCOPE,
            f"calendar:{context.now.date()}",
            fetch,
            fresh=5 * 60,
            max_age=2 * 60 * 60,
        )
        return result["accounts"]

    @staticmethod
    def _merged(accounts: list[AccountData], now: datetime) -> dict[str, Any]:
        """Today's and upcoming events of all accounts together; ended events dropped."""
        events = sorted(
            (
                e
                for a in accounts
                if a.data
                for e in a.data["events"]
                if e["end"] > now or e["start"].date() == now.date()
            ),
            key=lambda e: (
                max(e["start"], now.replace(hour=0, minute=0)).date(),
                not e["all_day"],
                e["start"],
            ),
        )
        return today_and_upcoming(events, now)

    async def timeline(self, context: WidgetContext) -> list[TimelineItem]:
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        day = self._merged(loaded, context.now)
        return [
            TimelineItem(e["start"], e["end"], e["title"])
            for e in day["today"]
            if e["kind"] != "now" and not e["all_day"]
        ]

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        if not loaded:
            return None
        now = context.now
        day = self._merged(loaded, now)
        lines = []
        for event in day["today"]:
            if event["kind"] == "now":
                lines.append(f"-- now is {now:%H:%M} --")
                continue
            when = "all day" if event["all_day"] else f"{event['start']:%H:%M}–{event['end']:%H:%M}"
            done = " (already over)" if event["kind"] == "past" else ""
            details = f" ({event['sub']})" if event["sub"] else ""
            lines.append(f"Today {when}: {event['title']}{details}{done}")
        if not day["today"]:
            lines.append("Nothing on the calendar today.")
        trips = await self._trips(context, day["today"])
        lines += [trip_line(trip) for trip in trips]
        lines.append(focus_line(focus_time(day["today"], now, trips)))
        # All of tomorrow, with times and places: a flight or an early start needs preparing
        # today. Then a few events of the rest of the week.
        tomorrow = (now + timedelta(days=1)).date()
        events = [e for a in loaded for e in a.data["events"]]
        tomorrows = events_on(events, tomorrow)
        for event in tomorrows:
            when = "all day" if event["all_day"] else f"{event['start']:%H:%M}–{event['end']:%H:%M}"
            details = f" ({event['sub']})" if event["sub"] else ""
            lines.append(f"Tomorrow {when}: {event['title']}{details}")
        if not tomorrows:
            lines.append("Nothing on the calendar tomorrow.")
        later = [e for e in day["upcoming"] if e["start"].date() > tomorrow]
        for event in later[:5]:
            when = event["start"].strftime("%A" if event["all_day"] else "%A %H:%M")
            lines.append(f"Later this week, {when}: {event['title']}")
        return BriefSection("Calendar", lines)

    async def _widget(self, context: WidgetContext) -> WidgetData:
        accounts = await self._accounts(context)
        status = account_status(accounts, "Google Calendar")
        loaded = [account for account in accounts if account.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        now = context.now
        day = self._merged(loaded, now)
        today = [e for e in day["today"] if e["kind"] != "now"]
        week = [e for e in day["upcoming"] if e["start"] < now + timedelta(days=DAYS_AHEAD)]
        # The next timed event: later today, or else the first of the week ahead.
        timed = [e for e in [*today, *week] if not e["all_day"] and e["start"] > now]
        nxt = timed[0] if timed else None
        trips = await self._trips(context, today)
        focus = focus_time(today, now, trips)
        rows = []
        # All of today (what is over is dimmed) with its free blocks for focus, then the week
        # ahead, by calendar; the numbers above filter them: today, the next event, focus
        # time, the next seven days.
        listed = among_events(today, [*focus, *({**t, "kind": "travel"} for t in trips)])
        for event in [*listed, *week]:
            if event.get("kind") == "travel":
                soon = event["start"] - now <= timedelta(minutes=15)
                rows.append(
                    Row(
                        title=f"Leave for {event['event']['title']}",
                        subtitle=f"{event['length']} {event['by']} from {event['origin']}",
                        badge=f"{event['start']:%H:%M}",
                        tone="warn" if soon else "info",
                        tags=("travel",),
                    )
                )
                continue
            if event.get("kind") == "focus":
                rows.append(
                    Row(
                        title="Free for focus",
                        subtitle=f"{event['start']:%H:%M}–{event['end']:%H:%M} · {event['length']}",
                        badge=f"{event['start']:%H:%M}",
                        tone="ok",
                        tags=("focus",),
                    )
                )
                continue
            tags = ["today" if event in today else "week"]
            if event is nxt:
                tags.append("next")
            rows.append(
                Row(
                    title=event["title"],
                    subtitle=describe(event),
                    badge="Over" if event.get("kind") == "past" else event_badge(event, now),
                    tone="neutral" if event.get("kind") == "past" else "info",
                    url=event["url"],
                    ref=event_ref(event),
                    tags=tuple(tags),
                    group=event["calendar"] or event.get("account") or "Primary",
                )
            )
        focus_minutes = sum(int((b["end"] - b["start"]).total_seconds() // 60) for b in focus)
        return WidgetData(
            value=day["today_count"],
            summary="events today" + (f" · next {event_badge(nxt, now)}" if nxt else ""),
            stats=[
                Stat(day["today_count"], "Today", filter="today"),
                Stat(event_badge(nxt, now) if nxt else "—", "Next", filter="next" if nxt else None),
                Stat(
                    duration(focus_minutes) if focus else "—",
                    "Focus",
                    filter="focus" if any(e.get("kind") == "focus" for e in listed) else None,
                ),
                Stat(len(week), "Next 7 days", filter="week"),
            ],
            list_title="Schedule",
            rows=rows,
            group_label="calendars",
            **open_buttons("Open calendar", [(a.email, calendar_url(a.email)) for a in loaded]),
            **status,
        )

    async def _today(self, context: WidgetContext) -> dict[str, Any]:
        """The side panel: today with a "now" marker, or another day of the week ahead."""
        accounts = await self._accounts(context)
        loaded = [account for account in accounts if account.data is not None]
        try:
            offset = min(max(int(context.params.get("day", 0)), 0), DAYS_AHEAD - 1)
        except ValueError:
            offset = 0
        now = context.now
        date = (now + timedelta(days=offset)).date()
        if offset == 0:
            day = self._merged(loaded, now) if loaded else None
            if day:
                trips = await self._trips(context, day["today"])
                extras = [*focus_time(day["today"], now, trips)]
                extras += [{**t, "kind": "travel"} for t in trips]
                day = {**day, "today": among_events(day["today"], extras)}
        else:
            day = {"today": events_on([e for a in loaded for e in a.data["events"]], date)}
        label = "Today" if offset == 0 else "Tomorrow" if offset == 1 else f"{date:%a %-d %b}"
        return {
            "connected": bool(accounts),
            "loaded": bool(loaded),
            "day": day,
            "offset": offset,
            "last": DAYS_AHEAD - 1,
            "label": label,
        }

    async def _next(self, context: WidgetContext) -> dict[str, Any]:
        accounts = await self._accounts(context)
        loaded = [account for account in accounts if account.data is not None]
        if not loaded:
            return {"event": None}
        day = self._merged(loaded, context.now)
        upcoming = [
            e
            for e in [*day["today"], *day["upcoming"]]
            if e.get("kind") != "now" and not e["all_day"] and e["start"] > context.now
        ]
        event = upcoming[0] if upcoming else None
        # When to leave for it, with Commute set up and the event somewhere today.
        trips = await self._trips(context, day["today"]) if event else []
        trip = next((t for t in trips if t["event"]["start"] == event["start"]), None)
        return {
            "event": event,
            "leave": trip["start"] if trip else None,
            "leave_soon": bool(trip) and trip["start"] - context.now <= timedelta(minutes=15),
            "when": (
                until(event["start"], context.now)
                if event["start"].date() == context.now.date()
                else event_badge(event, context.now)
            )
            if event
            else None,
        }
