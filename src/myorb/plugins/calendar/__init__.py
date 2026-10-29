import asyncio
import logging
import re
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sqlalchemy import select

from myorb.connections.google_accounts import GoogleAccount, GoogleAccounts
from myorb.connections.providers import Provider, ProviderError
from myorb.overview.glance import duration, focus_blocks, free_blocks
from myorb.plugins.base import (
    Action,
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
from myorb.plugins.calendar.commute import Commute, Pause, plan, route_minutes
from myorb.plugins.calendar.models import IgnoredEvent
from myorb.plugins.google_data import AccountData, account_status, load_accounts

if TYPE_CHECKING:
    from fastapi import APIRouter

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
# Time for lunch is kept out of the focus blocks: this long, somewhere in this window, as
# close to noon as it fits without cutting a focus block short.
LUNCH_MINUTES = 45
LUNCH_FROM = (11, 30)
LUNCH_TO = (13, 30)
LUNCH_AT = (12, 0)
# An event of the user's own for lunch takes its place, and is shown as lunch.
LUNCH_EVENT = re.compile(r"(?i)\b(lunch|obed|oběd|mittag\w*)\b")


def is_lunch(event: dict[str, Any]) -> bool:
    """The user's own lunch in the calendar: a timed event called so, not ignored."""
    return (
        event.get("kind") != "now"
        and not event.get("all_day")
        and not event.get("ignored")
        and bool(LUNCH_EVENT.search(event.get("title") or ""))
    )


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
        # Shown as "Free" in Google Calendar: it leaves the time open for focus. Not an event
        # Gmail made from an email (a booking, a flight): Gmail marks every one free, the user
        # did not.
        "free": event.get("transparency") == "transparent"
        and event.get("eventType") != "fromGmail",
        # What Ask needs to change the event (see actions.py).
        "id": event.get("id", ""),
        # The series a repeating event belongs to: ignoring one day ignores them all.
        "series": event.get("recurringEventId") or event.get("id", ""),
        "calendar_id": calendar.get("id", ""),
    }


def event_ref(event: dict[str, Any]) -> str:
    """ "event 1 primary@x.example 7kq3…": the account in myOrb, the calendar and the event.
    Events cached by an earlier version have none."""
    if not event.get("id") or not event.get("account_id"):
        return ""
    return f"event {event['account_id']} {event['calendar_id']} {event['id']}"


def event_key(event: dict[str, Any]) -> str:
    """The event (or its series) in its calendar, as an ignored event is kept; "" for events
    cached by an earlier version without an id."""
    series = event.get("series") or event.get("id")
    return f"{event.get('calendar_id', '')}/{series}" if series else ""


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
        "today_count": sum(
            1 for e in today if e["kind"] != "now" and not e.get("ignored") and not is_lunch(e)
        ),
        "upcoming": upcoming,
    }


def events_on(
    events: list[dict[str, Any]], date: Any, ignored: set[str] = frozenset()
) -> list[dict[str, Any]]:
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
            day.append(
                {
                    **event,
                    "kind": "event",
                    "sub": describe(event),
                    "ignored": event_key(event) in ignored,
                }
            )
    return day


def _busy(events: list[dict[str, Any]], trips: list[dict[str, Any]]) -> list[TimelineItem]:
    """What takes today's time: the events (not those shown as free or ignored) and the ways
    to them. An event shown as free still takes it when the user goes there."""
    gone_to = {(t["event"]["title"], t["event"]["start"]) for t in trips}
    return [
        TimelineItem(e["start"], e["end"], e["title"])
        for e in events
        if e.get("kind") != "now"
        and not e["all_day"]
        and (not e.get("free") or (e["title"], e["start"]) in gone_to)
        and not e.get("ignored")
    ] + [TimelineItem(t["start"], t["end"], "Travel") for t in trips]


def _focus_minutes(busy: list[TimelineItem], now: datetime) -> int:
    return sum(int((end - start).total_seconds() // 60) for start, end in focus_blocks(busy, now))


def lunch_time(
    events: list[dict[str, Any]], now: datetime, trips: list[dict[str, Any]] = ()
) -> dict[str, Any] | None:
    """Today's lunch (kind "lunch"): the user's own lunch event (with its title and link), or
    else time kept free in the lunch window where it leaves the most time for focus, then
    closest to noon. None once that is over, or when the window has no room for it."""
    if own := next((e for e in events if is_lunch(e)), None):
        minutes = int((own["end"] - own["start"]).total_seconds() // 60)
        return {
            "kind": "lunch",
            "start": own["start"],
            "end": own["end"],
            "length": duration(minutes),
            "title": own["title"],
            "url": own.get("url"),
            "over": own["end"] <= now,
        }
    day = now.replace(second=0, microsecond=0)
    earliest = max(day.replace(hour=LUNCH_FROM[0], minute=LUNCH_FROM[1]), day)
    earliest += timedelta(minutes=-earliest.minute % 5)
    latest = day.replace(hour=LUNCH_TO[0], minute=LUNCH_TO[1])
    noon = day.replace(hour=LUNCH_AT[0], minute=LUNCH_AT[1])
    length = timedelta(minutes=LUNCH_MINUTES)
    busy = _busy(events, trips)
    best: tuple[int, float, datetime] | None = None
    for begin, end in free_blocks(busy, earliest, latest):
        if end - begin < length:
            continue
        # At the start of the free time, at noon, or at its end.
        for start in {begin, min(max(noon, begin), end - length), end - length}:
            left = _focus_minutes([*busy, TimelineItem(start, start + length, "Lunch")], now)
            score = (left, -abs((start - noon).total_seconds()), start)
            if best is None or score[:2] > best[:2]:
                best = score
    if best is None:
        return None
    start = best[2]
    return {
        "kind": "lunch",
        "start": start,
        "end": start + length,
        "length": duration(LUNCH_MINUTES),
    }


def focus_time(
    events: list[dict[str, Any]], now: datetime, trips: list[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The rest of today's free blocks for deep work, between the events that take the time,
    the ways to them (see commute.plan) and lunch (see lunch_time)."""
    busy = _busy(events, trips)
    if lunch := lunch_time(events, now, trips):
        busy.append(TimelineItem(lunch["start"], lunch["end"], "Lunch"))
    return [
        {"start": start, "end": end, "length": duration(int((end - start).total_seconds() // 60))}
        for start, end in focus_blocks(busy, now)
    ]


def among_events(
    events: list[dict[str, Any]], extras: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Today's events with free blocks (kind "focus"), lunch (kind "lunch") and ways to them
    (kind "travel") among them, in time order. Only among meetings: a day without any is all
    free, and says so."""
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
        self.commute_pause = Pause()

    def router(self) -> "APIRouter":
        from myorb.plugins.calendar.routes import router

        return router

    def connections(self) -> list[Provider]:
        return [self.commute]

    async def _trips(
        self, context: WidgetContext, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Today's ways to the events ahead, when Commute is set up (see commute.plan); not to
        ignored ones."""
        events = [e for e in events if not e.get("ignored")]
        accounts = await context.connections.list(self.commute.id)
        if not accounts:
            return []
        account = accounts[0]

        async def route(
            origin: str, destination: str, departure: datetime
        ) -> tuple[int, str] | None:
            # The same way asked again within a quarter of an hour comes from the cache.
            slot = departure.replace(minute=departure.minute // 15 * 15, second=0, microsecond=0)
            key = f"commute:{account.id}:{origin}|{destination}|{slot:%Y-%m-%d %H:%M}"

            async def fetch() -> tuple[int, str] | None:
                if self.commute_pause.paused(account.credentials):
                    raise ProviderError("Google refused a way a moment ago.")
                try:
                    return await route_minutes(
                        context.http, account.credentials, origin, destination, departure
                    )
                except ProviderError:
                    self.commute_pause.refused(account.credentials)
                    raise

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
            Panel(
                id="today", title="Schedule", template="today.html", load=self._today, toggle="past"
            ),
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

    async def _ignored(self, context: WidgetContext) -> set[str]:
        async with context.sessionmaker() as db:
            return set(await db.scalars(select(IgnoredEvent.key)))

    async def ignored_today(self, context: WidgetContext) -> set[tuple[str, datetime]]:
        """Today's ignored events, by title and start (as the brief keeps its calendar)."""
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        day = self._merged(loaded, context.now, await self._ignored(context))
        return {(e["title"], e["start"]) for e in day["today"] if e.get("ignored")}

    @staticmethod
    def _merged(
        accounts: list[AccountData], now: datetime, ignored: set[str] = frozenset()
    ) -> dict[str, Any]:
        """Today's and upcoming events of all accounts together; ended events dropped, the
        ignored ones marked."""
        events = sorted(
            (
                {**e, "ignored": event_key(e) in ignored}
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
        day = self._merged(loaded, context.now, await self._ignored(context))
        return [
            TimelineItem(e["start"], e["end"], e["title"])
            for e in day["today"]
            if e["kind"] != "now" and not e["all_day"] and not e["ignored"]
        ]

    async def trips_today(self, context: WidgetContext) -> list[dict[str, Any]]:
        """Today's ways to the events ahead (see commute.plan), for the report's timeline."""
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        day = self._merged(loaded, context.now, await self._ignored(context))
        return await self._trips(context, day["today"])

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        if not loaded:
            return None
        now = context.now
        ignored = await self._ignored(context)
        day = self._merged(loaded, now, ignored)
        lines = []
        for event in day["today"]:
            if event["kind"] == "now":
                lines.append(f"-- now is {now:%H:%M} --")
                continue
            if event["ignored"] or is_lunch(event):
                continue  # the user ignores it: it does not concern them; lunch comes below
            when = "all day" if event["all_day"] else f"{event['start']:%H:%M}–{event['end']:%H:%M}"
            done = " (already over)" if event["kind"] == "past" else ""
            details = f" ({event['sub']})" if event["sub"] else ""
            lines.append(f"Today {when}: {event['title']}{details}{done}")
        if not day["today"]:
            lines.append("Nothing on the calendar today.")
        trips = await self._trips(context, day["today"])
        lines += [trip_line(trip) for trip in trips]
        if lunch := lunch_time(day["today"], now, trips):
            own = f" ({lunch['title']}, in the calendar)" if lunch.get("title") else ""
            lines.append(f"Lunch kept free: {lunch['start']:%H:%M}–{lunch['end']:%H:%M}{own}")
        lines.append(focus_line(focus_time(day["today"], now, trips)))
        # All of tomorrow, with times and places: a flight or an early start needs preparing
        # today. Then a few events of the rest of the week.
        tomorrow = (now + timedelta(days=1)).date()
        events = [e for a in loaded for e in a.data["events"] if event_key(e) not in ignored]
        tomorrows = events_on(events, tomorrow)
        for event in tomorrows:
            when = "all day" if event["all_day"] else f"{event['start']:%H:%M}–{event['end']:%H:%M}"
            details = f" ({event['sub']})" if event["sub"] else ""
            lines.append(f"Tomorrow {when}: {event['title']}{details}")
        if not tomorrows:
            lines.append("Nothing on the calendar tomorrow.")
        later = [e for e in day["upcoming"] if e["start"].date() > tomorrow and not e["ignored"]]
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
        day = self._merged(loaded, now, await self._ignored(context))
        today = [e for e in day["today"] if e["kind"] != "now"]
        week = [e for e in day["upcoming"] if e["start"] < now + timedelta(days=DAYS_AHEAD)]
        # The next timed event: later today, or else the first of the week ahead.
        timed = [
            e
            for e in [*today, *week]
            if not e["all_day"] and e["start"] > now and not e["ignored"] and not is_lunch(e)
        ]
        nxt = timed[0] if timed else None
        trips = await self._trips(context, today)
        focus = focus_time(today, now, trips)
        lunch = lunch_time(today, now, trips)
        rows = []
        # All of today (what is over is dimmed) with its free blocks for focus, lunch and the
        # ways to the events (each of today, in its colour, as in the side panel), then the week
        # ahead, by calendar; the numbers above filter them: today, the next event, focus
        # time, the next seven days.
        extras = [*focus, *([lunch] if lunch else []), *({**t, "kind": "travel"} for t in trips)]
        # The user's own lunch shows as lunch, not as one of the meetings.
        meetings = [e for e in today if not is_lunch(e)]
        listed = among_events(meetings, sorted(extras, key=lambda e: e["start"]))
        for event in [*listed, *week]:
            if event.get("kind") == "travel":
                soon = event["start"] - now <= timedelta(minutes=15)
                rows.append(
                    Row(
                        title=f"Leave for {event['event']['title']}",
                        subtitle=f"{event['length']} {event['by']} from {event['origin']}",
                        badge=f"{event['start']:%H:%M}",
                        tone="warn" if soon else "info",
                        tags=("today", "travel"),
                    )
                )
                continue
            if event.get("kind") == "lunch":
                rows.append(
                    Row(
                        title=event.get("title") or "Lunch",
                        subtitle=f"{event['start']:%H:%M}–{event['end']:%H:%M} · {event['length']}",
                        badge=f"{event['start']:%H:%M}",
                        url=event.get("url"),
                        tags=("today", "lunch"),
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
                        tags=("today", "focus"),
                    )
                )
                continue
            tags = ["today" if event in today else "week"]
            if event is nxt:
                tags.append("next")
            # An ignored event stays, grey, with the button to count it again.
            key = quote(event_key(event), safe="")
            if event["ignored"]:
                tags.append("ignored")
                actions = [Action("Count again", f"/calendar/count?key={key}")]
            else:
                actions = [Action("Ignore", f"/calendar/ignore?key={key}")] if key else []
            rows.append(
                Row(
                    title=event["title"],
                    subtitle=describe(event) + (" · Ignored" if event["ignored"] else ""),
                    badge="Over" if event.get("kind") == "past" else event_badge(event, now),
                    tone="neutral" if event.get("kind") == "past" or event["ignored"] else "info",
                    url=event["url"],
                    actions=actions,
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
        ignored = await self._ignored(context)
        if offset == 0:
            day = self._merged(loaded, now, ignored) if loaded else None
            if day:
                trips = await self._trips(context, day["today"])
                extras = [*focus_time(day["today"], now, trips)]
                if lunch := lunch_time(day["today"], now, trips):
                    extras.append(lunch)
                extras += [{**t, "kind": "travel"} for t in trips]
                extras.sort(key=lambda e: e["start"])
                meetings = [e for e in day["today"] if not is_lunch(e)]
                day = {**day, "today": among_events(meetings, extras)}
        else:
            events = [e for a in loaded for e in a.data["events"]]
            day = {"today": events_on(events, date, ignored)}
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
        day = self._merged(loaded, context.now, await self._ignored(context))
        upcoming = [
            e
            for e in [*day["today"], *day["upcoming"]]
            if e.get("kind") != "now"
            and not e["all_day"]
            and e["start"] > context.now
            and not e["ignored"]
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
