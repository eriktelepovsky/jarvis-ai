import asyncio
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jarvis_hud.connections.google_accounts import GoogleAccount, GoogleAccounts
from jarvis_hud.plugins.base import Plugin, Widget, WidgetContext
from jarvis_hud.plugins.google_data import load_accounts

API = "https://www.googleapis.com/calendar/v3"
API_NAME = "Google Calendar API"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
DAYS_AHEAD = 7
EVENTS_SHOWN = 10
# Event types that are not meetings and would only clutter the list.
HIDDEN_EVENT_TYPES = {"workingLocation"}


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


def group_by_day(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Group events under "Today", "Tomorrow" or a date, in order."""
    groups: dict[date, list[dict[str, Any]]] = {}
    for event in events:
        # Multi-day events that started earlier are shown under today.
        day = max(event["start"].date(), now.date())
        groups.setdefault(day, []).append(event)
    labels = {now.date(): "Today", now.date() + timedelta(days=1): "Tomorrow"}
    return [
        {"label": labels.get(day, day.strftime("%A %d %b")), "events": day_events}
        for day, day_events in groups.items()
    ]


async def fetch_events(
    google: GoogleAccounts, account: GoogleAccount, now: datetime
) -> list[dict[str, Any]]:
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
        # Events are listed by their end time, so ongoing events are included.
        "timeMin": now.isoformat(),
        "timeMax": (day_start + timedelta(days=DAYS_AHEAD)).isoformat(),
        "maxResults": EVENTS_SHOWN,
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
    return events[:EVENTS_SHOWN]


class CalendarPlugin(Plugin):
    """Today's and upcoming events from connected Google Calendars."""

    id = "calendar"
    name = "Calendar"
    templates_dir = Path(__file__).parent / "templates"
    position = 20

    def widgets(self) -> list[Widget]:
        return [Widget(id="upcoming", title="Calendar", template="upcoming.html", load=self._load)]

    async def _load(self, context: WidgetContext) -> dict[str, Any]:
        async def fetch(account: GoogleAccount) -> list[dict[str, Any]]:
            return await fetch_events(context.google, account, context.now)

        result = await load_accounts(
            context, SCOPE, "calendar", fetch, fresh=5 * 60, max_age=2 * 60 * 60
        )
        # Events that ended since they were fetched are dropped.
        result["days"] = {
            account.email: group_by_day(
                [event for event in account.data if event["end"] > context.now], context.now
            )
            for account in result["accounts"]
            if account.data is not None
        }
        return result
