"""What Claude may do in Google Calendar from Ask, each after the user confirms it: add an
event, change one (its time, title or place), delete one, or answer an invitation. All can be
undone. Guests are invited only when the user asks for it; Google then emails them."""

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from myorb.connections.google import GoogleError
from myorb.connections.google_accounts import GoogleAccount
from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.calendar import API, API_NAME, EVENTS_SCOPE

EVENT = {
    "type": "string",
    "description": "The event's ref, e.g. event 1 tony@stark.example 7kq3b2.",
}
WHEN = (
    "Local time as YYYY-MM-DDTHH:MM, or a date YYYY-MM-DD for an all-day event (its end is "
    "the last day)."
)
RESPONSES = {"accepted": "Accept", "declined": "Decline", "tentative": "Maybe"}


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _writable(account: GoogleAccount) -> GoogleAccount:
    if EVENTS_SCOPE not in account.scopes:
        raise ActionError(
            f"myOrb may not change the calendar of {account.email}. Connect the account again "
            "in Integrations and allow it."
        )
    return account


async def _account(context: WidgetContext, email: str) -> GoogleAccount:
    """The account by its address; with none given, the first connected one."""
    accounts = await context.google.list()
    wanted = email.strip().lower()
    for account in accounts:
        if not wanted or account.email.lower() == wanted:
            return _writable(account)
    raise ActionError(f"No Google account {email} is connected." if wanted else "Connect Google.")


async def _event_of(context: WidgetContext, ref: str) -> tuple[GoogleAccount, str, str]:
    parts = ref.split()
    if len(parts) != 4 or parts[0] != "event" or not parts[1].isdigit():
        raise ActionError("That is not an event's ref.")
    accounts = {a.id: a for a in await context.google.list()}
    account = accounts.get(int(parts[1]))
    if account is None:
        raise ActionError("That Google account is not connected.")
    return _writable(account), parts[2], parts[3]


def _url(calendar_id: str, event_id: str = "") -> str:
    url = f"{API}/calendars/{quote(calendar_id, safe='')}/events"
    return f"{url}/{quote(event_id, safe='')}" if event_id else url


async def _call(
    context: WidgetContext, account: GoogleAccount, method: str, url: str, **kwargs: Any
) -> Any:
    try:
        return await context.google.request_json(account, method, url, API_NAME, **kwargs)
    except GoogleError as error:
        raise ActionError(str(error)) from error


def _fresh(context: WidgetContext, account: GoogleAccount) -> None:
    """The events are read again, so the dashboard shows the change."""
    context.cache.invalidate(f"google:{account.id}:calendar:")


def _times(context: WidgetContext, start: str, end: str) -> tuple[dict, dict]:
    """Google's start and end from Claude's local times or dates. An end before the start, or
    none, makes an hour (or a single day)."""
    zone = str(context.now.tzinfo)
    try:
        if "T" not in start:
            first = date.fromisoformat(start)
            last = date.fromisoformat(end) if end else first
            last = max(last, first)
            # Google's end date is the day after the event.
            return {"date": first.isoformat()}, {"date": (last + timedelta(days=1)).isoformat()}
        starts = datetime.fromisoformat(start).replace(tzinfo=None, second=0, microsecond=0)
        ends = datetime.fromisoformat(end).replace(tzinfo=None) if end and "T" in end else None
    except ValueError as error:
        raise ActionError("The time is not in the YYYY-MM-DDTHH:MM format.") from error
    if ends is None or ends <= starts:
        ends = starts + timedelta(hours=1)
    return (
        {"dateTime": starts.isoformat(), "timeZone": zone},
        {"dateTime": ends.isoformat(), "timeZone": zone},
    )


def _when(start: dict, end: dict) -> str:
    if "date" in start:
        first = date.fromisoformat(start["date"])
        last = date.fromisoformat(end["date"]) - timedelta(days=1)
        days = f"{first:%a %-d %b}" + (f" – {last:%a %-d %b}" if last > first else "")
        return f"{days}, all day"
    starts = datetime.fromisoformat(start["dateTime"])
    ends = datetime.fromisoformat(end["dateTime"])
    same_day = ends.date() == starts.date()
    return (
        f"{starts:%a %-d %b %H:%M}–{ends:%H:%M}"
        if same_day
        else (f"{starts:%a %-d %b %H:%M} – {ends:%a %-d %b %H:%M}")
    )


def _guests(data: dict[str, Any]) -> list[str]:
    return [g.strip() for g in data["guests"] if "@" in g.strip()]


# Adding an event


def _new_event(context: WidgetContext, data: dict[str, Any]) -> dict[str, Any]:
    if not data["title"].strip():
        raise ActionError("The event needs a title.")
    start, end = _times(context, data["start"], data["end"])
    event: dict[str, Any] = {"summary": data["title"].strip(), "start": start, "end": end}
    if data["location"].strip():
        event["location"] = data["location"].strip()
    if data["description"].strip():
        event["description"] = data["description"].strip()
    if guests := _guests(data):
        event["attendees"] = [{"email": g} for g in guests]
    return event


async def _create_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    account = await _account(context, data["account"])
    event = _new_event(context, data)
    where = f" · {event['location']}" if "location" in event else ""
    guests = _guests(data)
    invite = f" Google emails an invitation to {', '.join(guests)}." if guests else ""
    several = len(await context.google.list()) > 1
    calendar = f" in {account.email}'s calendar" if several else ""
    return (
        f"Add “{event['summary']}”{calendar}: {_when(event['start'], event['end'])}{where}.{invite}"
    )


async def _create(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account = await _account(context, data["account"])
    event = _new_event(context, data)
    params = {"sendUpdates": "all" if "attendees" in event else "none"}
    created = await _call(context, account, "POST", _url("primary"), params=params, json=event)
    _fresh(context, account)
    event_id = (created or {}).get("id")

    async def undo(context: WidgetContext) -> str:
        await _call(context, account, "DELETE", _url("primary", event_id), params=params)
        _fresh(context, account)
        return f"Removed “{event['summary']}” from the calendar."

    when = _when(event["start"], event["end"])
    return ActionResult(f"Added “{event['summary']}” ({when}).", undo if event_id else None)


# Changing an event


async def _current(context: WidgetContext, data: dict[str, Any]) -> tuple[Any, ...]:
    account, calendar_id, event_id = await _event_of(context, data["event"])
    event = await _call(context, account, "GET", _url(calendar_id, event_id))
    return account, calendar_id, event_id, event or {}


def _changes(context: WidgetContext, event: dict, data: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if data["title"].strip():
        changes["summary"] = data["title"].strip()
    if data["location"].strip():
        changes["location"] = data["location"].strip()
    if data["start"].strip():
        end = data["end"].strip()
        if not end and "dateTime" in event.get("start", {}) and "T" in data["start"]:
            # Moved, not resized: the event keeps its length.
            length = datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(
                event["start"]["dateTime"]
            )
            end = (datetime.fromisoformat(data["start"]) + length).isoformat()
        changes["start"], changes["end"] = _times(context, data["start"], end)
    if not changes:
        raise ActionError("Nothing to change.")
    return changes


def _change_text(event: dict, changes: dict[str, Any]) -> str:
    parts = []
    if "start" in changes:
        parts.append(f"to {_when(changes['start'], changes['end'])}")
    if "summary" in changes:
        parts.append(f"rename it to “{changes['summary']}”")
    if "location" in changes:
        parts.append(f"at {changes['location']}")
    guests = [a for a in event.get("attendees", []) if not a.get("self")]
    notify = " Google tells its guests." if guests and "start" in changes else ""
    return f"Change “{event.get('summary', '(No title)')}”: {', '.join(parts)}.{notify}"


async def _update_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    _, _, _, event = await _current(context, data)
    return _change_text(event, _changes(context, event, data))


async def _update(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account, calendar_id, event_id, event = await _current(context, data)
    changes = _changes(context, event, data)
    before = {key: event.get(key) for key in changes}
    url = _url(calendar_id, event_id)
    params = {"sendUpdates": "all"}
    await _call(context, account, "PATCH", url, params=params, json=changes)
    _fresh(context, account)
    title = changes.get("summary") or event.get("summary", "(No title)")

    async def undo(context: WidgetContext) -> str:
        await _call(context, account, "PATCH", url, params=params, json=before)
        _fresh(context, account)
        return f"“{event.get('summary', title)}” is back as it was."

    return ActionResult(f"Changed “{title}”.", undo)


# Deleting an event


async def _delete_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    _, _, _, event = await _current(context, data)
    when = _when(event["start"], event["end"]) if "start" in event else ""
    guests = [a for a in event.get("attendees", []) if not a.get("self")]
    notify = " Google tells its guests." if guests else ""
    return f"Delete “{event.get('summary', '(No title)')}” ({when}).{notify}"


async def _delete(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account, calendar_id, event_id, event = await _current(context, data)
    url = _url(calendar_id, event_id)
    await _call(context, account, "DELETE", url, params={"sendUpdates": "all"})
    _fresh(context, account)
    title = event.get("summary", "(No title)")

    async def undo(context: WidgetContext) -> str:
        # A deleted event stays in the calendar as cancelled for a while: confirmed again.
        await _call(context, account, "PATCH", url, json={"status": "confirmed"})
        _fresh(context, account)
        return f"“{title}” is back in the calendar."

    return ActionResult(f"Deleted “{title}”.", undo)


# Answering an invitation


def _response(event: dict, data: dict[str, Any]) -> tuple[list[dict], str]:
    attendees = event.get("attendees", [])
    me = next((a for a in attendees if a.get("self")), None)
    if me is None or me.get("organizer"):
        raise ActionError("This event is not an invitation to the user.")
    if data["response"] not in RESPONSES:
        raise ActionError("Answer accepted, declined or tentative.")
    return attendees, me.get("responseStatus", "needsAction")


def _answered(attendees: list[dict], response: str) -> list[dict]:
    return [{**a, "responseStatus": response} if a.get("self") else a for a in attendees]


async def _respond_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    _, _, _, event = await _current(context, data)
    _response(event, data)
    organizer = (event.get("organizer") or {}).get("email", "the organizer")
    return (
        f"{RESPONSES[data['response']]} “{event.get('summary', '(No title)')}”. "
        f"Google tells {organizer}."
    )


async def _respond(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account, calendar_id, event_id, event = await _current(context, data)
    attendees, before = _response(event, data)
    url = _url(calendar_id, event_id)
    params = {"sendUpdates": "all"}
    answered = _answered(attendees, data["response"])
    await _call(context, account, "PATCH", url, params=params, json={"attendees": answered})
    _fresh(context, account)
    title = event.get("summary", "(No title)")

    async def undo(context: WidgetContext) -> str:
        back = _answered(attendees, before)
        await _call(context, account, "PATCH", url, params=params, json={"attendees": back})
        _fresh(context, account)
        return f"The answer to “{title}” is back as it was."

    verb = {"accepted": "Accepted", "declined": "Declined", "tentative": "Answered maybe to"}
    return ActionResult(f"{verb[data['response']]} “{title}”.", undo)


def ask_actions() -> list[AskAction]:
    text = {"type": "string"}
    return [
        AskAction(
            "create_event",
            "Add an event to the user's Google Calendar. title is required; location, "
            "description and account may be empty (account is a connected Google account's "
            "address, empty for the main one). guests are email addresses to invite: only "
            "people the user asked to invite, as Google emails them.",
            _schema(
                title=text,
                start={"type": "string", "description": WHEN},
                end={"type": "string", "description": WHEN + " Empty for an hour."},
                location=text,
                description=text,
                guests={"type": "array", "items": {"type": "string"}},
                account=text,
            ),
            _create_summary,
            _create,
        ),
        AskAction(
            "update_event",
            "Change an event in the user's calendar: move it (start, and end when its length "
            "changes; empty end keeps its length), rename it (title) or change its place "
            "(location). Leave empty what stays.",
            _schema(
                event=EVENT,
                start={"type": "string", "description": WHEN + " Empty to keep."},
                end={"type": "string", "description": WHEN + " Empty to keep the length."},
                title=text,
                location=text,
            ),
            _update_summary,
            _update,
        ),
        AskAction(
            "delete_event",
            "Delete an event from the user's calendar.",
            _schema(event=EVENT),
            _delete_summary,
            _delete,
        ),
        AskAction(
            "respond_to_event",
            "Answer an invitation in the user's calendar: accepted, declined or tentative.",
            _schema(event=EVENT, response={"type": "string", "enum": list(RESPONSES)}),
            _respond_summary,
            _respond,
        ),
    ]
