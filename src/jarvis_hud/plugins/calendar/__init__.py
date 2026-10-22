import asyncio
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jarvis_hud.connections.google_accounts import GoogleAccount, GoogleAccounts
from jarvis_hud.plugins.base import Plugin, Widget, WidgetContext
from jarvis_hud.plugins.google_data import card_for, load_accounts

API = "https://www.googleapis.com/calendar/v3"
API_NAME = "Google Calendar API"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
DAYS_AHEAD = 7
EVENTS_PER_CALENDAR = 25
UPCOMING_SHOWN = 4
# Event types that are not meetings and would only clutter the list.
HIDDEN_EVENT_TYPES = {"workingLocation"}


def calendar_url(email: str) -> str:
    return f"https://calendar.google.com/calendar/u/?authuser={quote(email)}"


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
    }


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
    upcoming = [e for e in events if e["start"] >= tomorrow][:UPCOMING_SHOWN]
    return {
        "today": today,
        "today_count": sum(1 for e in today if e["kind"] != "now"),
        "upcoming": upcoming,
    }


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
        parsed
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
    position = 10

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="upcoming",
                title="Calendar",
                template="upcoming.html",
                load=self._load,
                icon="calendar",
                rows=2,
            )
        ]

    async def _load(self, context: WidgetContext) -> dict[str, Any]:
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
        accounts = result["accounts"]
        result["days"] = {
            account.email: today_and_upcoming(account.data["events"], context.now)
            for account in accounts
            if account.data is not None
        }
        calendars = sum(account.data["calendars"] for account in accounts if account.data)
        source = "Google Calendar" + (f" · {calendars} calendars" if calendars > 1 else "")
        action = (
            {"label": "Open calendar", "url": calendar_url(accounts[0].email)} if accounts else None
        )
        result["card"] = card_for(accounts, source, action)
        return result
