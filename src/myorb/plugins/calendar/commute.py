"""Commute: how long the way to today's events takes, with the traffic expected then, and when
to leave.

A way of at most 2 km is walked, however the user travels otherwise.

Google's Routes API, with an API key of a Google Cloud project that has it enabled. The way
starts at home, at work on a weekday's working hours when a work address is set, or at the
event before when that was somewhere too and ended not long before.
"""

import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2

from myorb.connections.providers import Field, Provider, ProviderError, Verified, request_json

ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
SERVICE = "Google Routes API"
# How the user travels: the Routes API's mode and how the dashboard says it.
MODES = {
    "drive": ("DRIVE", "by car"),
    "transit": ("TRANSIT", "by public transport"),
    "walk": ("WALK", "on foot"),
    "bicycle": ("BICYCLE", "by bike"),
}
# A way this short (in meters) is walked, however the user travels otherwise.
WALKED = 2000
# Arriving this long before an event starts; before a flight takes off, longer.
MARGIN = timedelta(minutes=5)
FLIGHT_MARGIN = timedelta(minutes=90)
# The way starts at the event before when it ended at most this long before.
CHAIN = timedelta(hours=3)
# Work is where the way starts on weekdays between these hours, when its address is set.
WORK_HOURS = (9, 17)
# Today's events looked at, so a long day does not ask for many routes.
MAX_TRIPS = 6
# Where the traffic is guessed for before the way's length is known: leaving this long before.
GUESS = timedelta(minutes=30)
# A location that is not a place to go to, e.g. "Online, link in the invitation".
NOT_A_PLACE = re.compile(
    r"https?://|\b(online|zoom|teams|meet|webex|skype|call|link|hangouts?)\b", re.IGNORECASE
)
# A flight: by its title ("Flight to Košice", "W6 7003") or an airport for its location.
FLIGHT_TITLE = re.compile(r"^\s*(flight|let)\b|\b[A-Z][A-Z0-9]\s?\d{3,4}\b", re.IGNORECASE)
AIRPORT = re.compile(r"airport|letisko|flughafen|lotnisko|\bletiště\b", re.IGNORECASE)

# After Google refuses a way (a wrong or restricted key, a spent quota), no way is asked for
# this long: the dashboard asks on every page it loads, and each refusal is a request too.
PAUSE = 15 * 60

# A way's minutes and how it is travelled (a key of MODES); None when there is no way.
Route = Callable[[str, str, datetime], Awaitable[tuple[int, str] | None]]


class Pause:
    """The keys Google refused lately. Kept by a hash of the key, so a key changed in the
    settings is asked with at once."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    @staticmethod
    def _key(credentials: dict[str, Any]) -> str:
        return hashlib.sha256(credentials["api_key"].encode()).hexdigest()

    def paused(self, credentials: dict[str, Any]) -> bool:
        return self._until.get(self._key(credentials), 0) > self._clock()

    def refused(self, credentials: dict[str, Any]) -> None:
        self._until[self._key(credentials)] = self._clock() + PAUSE


def is_place(location: str | None) -> bool:
    return bool(location and location.strip()) and not NOT_A_PLACE.search(location or "")


def is_flight(event: dict[str, Any]) -> bool:
    return bool(FLIGHT_TITLE.search(event["title"]) or AIRPORT.search(event.get("location") or ""))


async def route_minutes(
    http: httpx2.AsyncClient,
    credentials: dict[str, Any],
    origin: str,
    destination: str,
    departure: datetime | None = None,
) -> tuple[int, str] | None:
    """Minutes from `origin` to `destination` (addresses), leaving at `departure`, with the
    traffic expected then when driving, and how the way is travelled: on foot when it is at
    most WALKED long by car or public transport. None when there is no way."""
    chosen = credentials.get("mode") or "drive"
    key = credentials["api_key"]
    found = await _route(http, key, MODES[chosen][0], origin, destination, departure)
    if found is None:
        return None
    minutes, meters = found
    if chosen in ("drive", "transit") and meters <= WALKED:
        walked = await _route(http, key, "WALK", origin, destination, departure)
        if walked is not None:
            return walked[0], "walk"
    return minutes, chosen


async def _route(
    http: httpx2.AsyncClient,
    api_key: str,
    mode: str,
    origin: str,
    destination: str,
    departure: datetime | None,
) -> tuple[int, int] | None:
    """Minutes and meters of the way, travelled by `mode` (the Routes API's)."""
    body: dict[str, Any] = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": mode,
    }
    if mode == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"
    if departure is not None:
        body["departureTime"] = departure.astimezone(UTC).isoformat().replace("+00:00", "Z")
    data = await request_json(
        http,
        "POST",
        ROUTES,
        SERVICE,
        json=body,
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
        },
    )
    routes = (data or {}).get("routes") or []
    if not routes:
        return None
    seconds = int(str(routes[0].get("duration", "0s")).rstrip("s") or 0)
    return max(1, round(seconds / 60)), int(routes[0].get("distanceMeters") or 0)


def travel_label(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if hours and rest else f"{hours} h" if hours else f"{rest} min"


def start_of_way(
    event: dict[str, Any], before: tuple[datetime, str] | None, credentials: dict[str, Any]
) -> tuple[str, str]:
    """Where the way to an event starts, and what the dashboard calls it: "home", "work" or
    the place of the event before (its address, as "from" asks for a place)."""
    if before and event["start"] - before[0] <= CHAIN:
        return before[1], before[1]
    work = credentials.get("work")
    start = event["start"]
    if work and start.weekday() < 5 and WORK_HOURS[0] <= start.hour < WORK_HOURS[1]:
        return work, "work"
    return credentials["home"], "home"


async def plan(
    events: list[dict[str, Any]], now: datetime, credentials: dict[str, Any], route: Route
) -> list[dict[str, Any]]:
    """Today's ways to the events still ahead that happen somewhere: when to leave and how long
    it takes. Each is {"event", "start" (when to leave), "minutes", "length", "origin", "by"}."""
    trips: list[dict[str, Any]] = []
    before: tuple[datetime, str] | None = None
    timed = sorted(
        (e for e in events if e.get("kind") != "now" and not e["all_day"]), key=lambda e: e["start"]
    )
    for event in timed:
        location = event.get("location")
        if not is_place(location):
            continue
        origin, origin_label = start_of_way(event, before, credentials)
        before = (event["end"], location)
        if event["start"] <= now or origin.strip().lower() == location.strip().lower():
            continue
        if len(trips) >= MAX_TRIPS:
            break
        margin = FLIGHT_MARGIN if is_flight(event) else MARGIN
        # The traffic of when the user leaves, guessed; never in the past, which the API refuses.
        departure = max(now + timedelta(minutes=1), event["start"] - margin - GUESS)
        way = await route(origin, location, departure)
        if way is None:
            continue
        minutes, mode = way
        trips.append(
            {
                "event": event,
                "start": event["start"] - margin - timedelta(minutes=minutes),
                "end": event["start"] - margin,
                "minutes": minutes,
                "length": travel_label(minutes),
                "origin": origin_label,
                "by": MODES[mode][1],
                "flight": margin == FLIGHT_MARGIN,
            }
        )
    return trips


class Commute(Provider):
    id = "commute"
    name = "Commute"
    logo = "km"
    summary = "When to leave for today's events, with the traffic expected then (Google Routes)."
    instructions = "commute.html"
    multiple = False
    api_hosts = ("routes.googleapis.com",)
    fields = [
        Field(
            "api_key",
            "Google API key",
            "password",
            help="A key of a Google Cloud project with the Routes API enabled.",
        ),
        Field("home", "Home address", "text", "e.g. Hlavná 1, Košice"),
        Field(
            "work",
            "Work address",
            "text",
            "optional",
            help="Where the way starts on weekdays from 9:00 to 17:00.",
            required=False,
        ),
        Field(
            "mode",
            "Travel by",
            "text",
            "drive, transit, walk or bicycle",
            help="Driving uses the traffic expected when you leave. Ways up to 2 km are walked.",
            required=False,
        ),
    ]

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        mode = (values.get("mode") or "drive").strip().lower()
        if mode not in MODES:
            raise ProviderError("Travel by drive, transit, walk or bicycle.")
        credentials = {
            "api_key": values["api_key"].strip(),
            "home": values["home"].strip(),
            "work": (values.get("work") or "").strip(),
            "mode": mode,
        }
        # The key is checked on the way from home to work (or around home, without work).
        to = credentials["work"] or credentials["home"]
        minutes = await route_minutes(http, credentials, credentials["home"], to)
        if minutes is None and credentials["work"]:
            raise ProviderError("Google finds no way from home to work. Check the addresses.")
        return Verified(f"From {credentials['home']}", credentials)
