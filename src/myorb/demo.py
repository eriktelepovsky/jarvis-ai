"""Demo mode (MYORB_DEMO=1): myOrb with a made-up day, to try it before connecting anything and
for the README's screenshots. The real plugins show it; only their data is made up. The brief
and Ask's answer are written in advance, so no service is called and
nothing goes to Claude.

The day is the same every day: the brief is the morning's, written at 07:48, and Ask's plan
takes what is left of it when asked.
"""

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb import profile
from myorb.activity.models import ActivityEvent
from myorb.activity.store import record
from myorb.brief.models import Brief
from myorb.brief.plugin import BriefPlugin
from myorb.brief.service import KICKERS, BriefService, ClaudeSettings, kind_for
from myorb.connections import store
from myorb.crypto import SecretBox
from myorb.plugins.base import Link, Plugin, Row, Stat, Widget, WidgetContext, WidgetData
from myorb.plugins.calendar import CalendarPlugin
from myorb.plugins.calendar.commute import plan
from myorb.plugins.google_data import AccountData
from myorb.plugins.uptime import history as uptime_history
from myorb.plugins.uptime.models import Incident, Monitor
from myorb.plugins.weather import WeatherPlugin
from myorb.tasks.models import Task
from myorb.tasks.plugin import TasksPlugin
from myorb.tasks.store import add_task

EXAMPLE = "https://example.com"
MODEL = "claude-sonnet-5-5"


# The day: when each thing starts, as hour and minute.
DAY = {
    "gym": (7, 0),
    "written": (7, 48),
    "review": (9, 0),
    "review_end": (12, 0),
    # Commute: 17 min by car and the 5 min margin before lunch (see commute.plan).
    "leave": (12, 8),
    "lunch": (12, 30),
    "one_on_one": (13, 45),
    "free": (14, 15),
    "rain": (14, 0),
    "flight": (17, 0),
    "run": (18, 0),
}


def times(now: datetime) -> dict[str, datetime]:
    """The day's times on the day of `now`."""
    day = now.replace(second=0, microsecond=0)
    return {name: day.replace(hour=h, minute=m) for name, (h, m) in DAY.items()}


def hm(value: datetime) -> str:
    return value.strftime("%H:%M")


# The numbers around the orb that are not shown by a real plugin here, with their plugins' icons.
ICONS = {
    id: import_module(f"myorb.plugins.{module}").ICON
    for id, module in [
        ("unread", "gmail"),
        ("prs", "code_review"),
        ("jira", "jira"),
        ("mentions", "slack"),
        ("uptime", "uptime"),
        ("sleep", "health"),
        ("videos", "youtube"),
        ("finance", "custom"),
    ]
}


def _rows(items: list[tuple[str, str, str, str]], **row: Any) -> list[Row]:
    return [
        Row(title=title, subtitle=subtitle, badge=badge, tone=tone, url=EXAMPLE, **row)
        for title, subtitle, badge, tone in items
    ]


def _unread() -> WidgetData:
    return WidgetData(
        value=12,
        summary="unread · 5 new today",
        stats=[Stat(12, "Unread"), Stat(5, "New today"), Stat(2, "From customers")],
        list_title="Unread",
        rows=_rows(
            [
                ("Pepper Potts", "Corrected Q4 invoice", "08:12", "warn"),
                ("Happy Hogan", "Agenda for our 1:1", "07:55", "neutral"),
                ("Rhodey", "Saturday at the track?", "07:31", "neutral"),
                (
                    "Stark Industries Legal",
                    "Contract renewal: signature needed",
                    "Yesterday",
                    "neutral",
                ),
                ("GitHub", "[app] PR #318: 2 checks failed", "Yesterday", "fail"),
            ],
            open_row=True,
        ),
    )


def _prs() -> WidgetData:
    return WidgetData(
        value=3,
        attention=True,
        summary="waiting for your review",
        stats=[Stat(3, "Review"), Stat(1, "Failing"), Stat(2, "Yours")],
        list_title="Waiting for your review",
        rows=_rows(
            [
                ("#318 Auth: refresh tokens before they expire", "app · Harley", "CI FAIL", "fail"),
                ("#321 Invoices as PDF", "app · Peter", "REVIEW", "warn"),
                ("#77 New pricing page", "web · Pepper", "REVIEW", "warn"),
            ]
        ),
    )


JIRA = [  # key, title, workspace, status, tone
    ("APP-412", "Token refresh fails after 30 days", "app", "IN PROGRESS", "info"),
    ("APP-398", "Export invoices as PDF", "app", "OVERDUE", "fail"),
    ("WEB-77", "New pricing page", "web", "IN REVIEW", "warn"),
    ("APP-405", "Dark mode for settings", "app", "TO DO", "neutral"),
    ("OPS-31", "Rotate the staging certificates", "ops", "OVERDUE", "fail"),
    ("WEB-81", "Newsletter sign-up form", "web", "TO DO", "neutral"),
    ("OPS-29", "Nightly database backup check", "ops", "IN PROGRESS", "info"),
]


def _jira() -> WidgetData:
    return WidgetData(
        value=7,
        attention=True,
        summary="assigned to you · 2 overdue",
        stats=[
            Stat(7, "Assigned"),
            Stat(2, "Overdue"),
            Stat(3, "In progress"),
            Stat(41, "Backlog"),
        ],
        list_title="Assigned to you",
        group_label="workspaces",
        rows=[
            Row(
                title=f"{key} {title}",
                subtitle=f"{group} · Medium",
                badge=status,
                tone=tone,
                url=EXAMPLE,
                group=group,
            )
            for key, title, group, status, tone in JIRA
        ],
        action=Link("Open Jira", EXAMPLE),
    )


def _finance() -> WidgetData:
    return WidgetData(
        value="€1.8k",
        attention=True,
        summary="spent this month · 2 invoices due",
        stats=[Stat("€1.8k", "Spent"), Stat(2, "Invoices due")],
        list_title="Invoices",
        rows=_rows(
            [
                ("Q4 consulting · Stark Industries", "€12,400 · due in 3 days", "DUE", "warn"),
                ("Workshop rent · October", "€2,150 · due today", "DUE", "warn"),
                ("Cloud hosting · September", "€318 · paid", "PAID", "ok"),
            ]
        ),
    )


def _mentions() -> WidgetData:
    return WidgetData(
        value=2,
        summary="Slack mentions · 24 h",
        stats=[Stat(2, "Mentions")],
        list_title="Mentions",
        rows=_rows(
            [
                ("#releases · Harley", "Can you look at the failing tests on #318?", "", "neutral"),
                ("#design · Pepper", "Mark 85 slides are in the folder", "", "neutral"),
            ]
        ),
    )


# The outages behind the bars: (days ago, hour, minutes down).
OUTAGES = {
    "starkindustries.example": [(11, 16, 4)],
    "staging.example.com": [(0, 3, 4)],
}


def _with_history(rows: list[Row], now: datetime) -> list[Row]:
    """The monitors' 30 days as bars, all up but for OUTAGES, drawn by the real plugin."""
    watched = Monitor(created_at=now - timedelta(days=60))
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    made = []
    for row in rows:
        incidents = [
            Incident(started_at=start, ended_at=start + timedelta(minutes=minutes))
            for back, hour, minutes in OUTAGES.get(row.title, [])
            if (start := day - timedelta(days=back) + timedelta(hours=hour)) < now
        ]
        made.append(replace(row, history=uptime_history(watched, incidents, now)))
    return made


def _uptime(now: datetime) -> WidgetData:
    made = WidgetData(
        value=0,
        summary="all 7 monitors are up",
        stats=[Stat(0, "Down"), Stat(7, "Monitors"), Stat(1, "Incidents · 30 d")],
        list_title="Monitors",
        rows=_rows(
            [
                ("starkindustries.example", "99.99 % · 30 d", "UP", "ok"),
                ("shop.example.com", "100 % · TLS expires in 9 days", "UP", "warn"),
                ("staging.example.com", "99.91 % · down 4 min at night", "UP", "ok"),
                # Checked by Uptime Kuma, which also watches what is not a website.
                ("api.example.com", "100 % · 30 d · Uptime Kuma", "UP", "ok"),
                ("status.example.com", "100 % · 30 d · Uptime Kuma", "UP", "ok"),
                ("Database", "db.internal:5432 · 100 % · 30 d · Uptime Kuma", "UP", "ok"),
                ("docs.example.com", "100 % · 30 d", "UP", "ok"),
            ]
        ),
    )
    return replace(made, rows=_with_history(made.rows, now))


def _sleep() -> WidgetData:
    return WidgetData(
        value="7h24",
        summary="sleep last night",
        stats=[Stat("7h24", "Last night"), Stat("6h58", "Week's average")],
        list_title="This week",
        rows=_rows(
            [
                ("Last night", "23:41 – 07:05", "7h24", "ok"),
                ("Monday", "00:32 – 06:50", "6h18", "warn"),
                ("Sunday", "23:10 – 07:30", "8h20", "ok"),
            ]
        ),
    )


def _videos() -> WidgetData:
    return WidgetData(
        value=5,
        summary="new videos",
        stats=[Stat(5, "New")],
        list_title="New videos",
        rows=_rows(
            [
                (
                    "Building an arc reactor at home (don't)",
                    "Maker's Garage · 18 min",
                    "",
                    "neutral",
                ),
                ("Carbon fibre layup, start to finish", "Composite Lab · 24 min", "", "neutral"),
                ("Formula E in Monaco: onboard lap", "Race Onboard · 3 min", "", "neutral"),
                ("How jet turbines really work", "Engineering Explained · 21 min", "", "neutral"),
                ("Rainy Malibu coast, 4K", "Slow Drives · 1 h", "", "neutral"),
            ]
        ),
    )


WIDGETS = [  # id, label, title, data
    ("unread", "Unread", "Email", _unread),
    ("prs", "PRs", "Pull requests", _prs),
    ("finance", "Finance", "Finance", _finance),
    ("jira", "Jira", "Jira", _jira),
    ("mentions", "Mentions", "Slack", _mentions),
    ("uptime", "Incidents", "Uptime", _uptime),
    ("sleep", "Sleep", "Sleep", _sleep),
    ("videos", "Videos", "YouTube", _videos),
]


def _widget(id: str, label: str, title: str, data: Any) -> Widget:
    async def load(context: WidgetContext) -> WidgetData:
        made = data(context.now) if data is _uptime else data()
        return replace(made, source=f"{title} · demo", updated_at=time.time())

    return Widget(id=id, label=label, title=title, icon=ICONS[id], load=load)


class DemoSources(Plugin):
    """The numbers of services that are not connected in the demo, all made up."""

    id = "demo"
    name = "Demo"

    def widgets(self) -> list[Widget]:
        return [_widget(*spec) for spec in WIDGETS]


def events(now: datetime) -> list[dict[str, Any]]:
    at = times(now)
    day = [  # start, minutes, title, location, video
        (at["gym"], 60, "Gym", "Fitness club", False),
        (at["review"], 180, "Design review: Mark 85", "Workshop", False),
        (at["lunch"], 60, "Lunch with Pepper", "Nobu Malibu", False),
        (at["one_on_one"], 30, "1:1 with Happy", None, True),
        (at["run"], 50, "Easy run 8 km", None, False),
    ]
    return [
        {
            "title": title,
            "start": start,
            "end": start + timedelta(minutes=minutes),
            "all_day": False,
            "location": location,
            "url": EXAMPLE,
            "video_url": "https://meet.example.com" if video else None,
            "calendar": None,
        }
        for start, minutes, title, location, video in day
    ]


class DemoCalendar(CalendarPlugin):
    async def _accounts(self, context: WidgetContext) -> list[AccountData]:
        made = {"events": events(context.now), "calendars": 1}
        return [AccountData("tony@example.com", made, time.time())]

    async def _trips(self, context: WidgetContext, events: list[dict[str, Any]]) -> list:
        # As if set up with Google Routes: the real plan, only the way's length made up.
        async def route(origin: str, destination: str, departure: datetime) -> int | None:
            return {"Nobu Malibu": 17}.get(destination)

        home = {"home": "10880 Malibu Point", "mode": "drive"}
        return await plan([e for e in events if not e.get("ignored")], context.now, home, route)


class DemoWeather(WeatherPlugin):
    async def _panel(self, context: WidgetContext) -> dict[str, Any]:
        hour = context.now.replace(minute=0, second=0, microsecond=0)
        temps = [9, 10, 12, 13, 12, 10]
        rain = [5, 10, 20, 70, 60, 30]
        return {
            "connected": True,
            "place": "Malibu",
            "error": None,
            "weather": {
                "temperature": 9,
                "feels_like": 7,
                "condition": "Overcast",
                "high": 13,
                "low": 5,
                "rain_chance": 70,
                "rain_from": times(context.now)["rain"],
                "hours": [
                    {"time": hour + timedelta(hours=1 + 2 * i), "temperature": t, "rain": r}
                    for i, (t, r) in enumerate(zip(temps, rain, strict=True))
                ],
            },
        }


def plugins() -> dict[str, Plugin]:
    return {
        "demo": DemoSources(),
        "calendar": DemoCalendar(),
        "tasks": TasksPlugin(),
        "brief": BriefPlugin(),
        "weather": DemoWeather(),
    }


def brief_content(now: datetime) -> dict[str, Any]:
    at = {name: hm(value) for name, value in times(now).items()}
    return {
        "headline": f"Three things need you before {at['review']}.",
        "summary": [
            "A busy morning: the build on PR #318 is failing, Pepper is waiting for your reply "
            "about the corrected Q4 invoice, and the Mark 85 design review starts at "
            f"{at['review']}.",
            f"The review runs until noon; leave at {at['leave']} for lunch with Pepper at "
            f"Nobu, and your 1:1 with Happy follows at {at['one_on_one']}. Then you are free "
            f"until {at['run']}, a good time for APP-398, which is two days overdue.",
            "12 emails are unread, 5 of them new today; two are from customers. Staging went "
            "down for 4 minutes at night and is back up. It stays overcast at 9° to 13°, with "
            f"rain from {at['rain']}, so take an umbrella for the evening run.",
        ],
        "priorities": [
            {
                "tag": "CI fail",
                "tone": "fail",
                "title": "PR #318 auth refresh",
                "detail": "2 tests failing",
            },
            {
                "tag": "Reply",
                "tone": "warn",
                "title": "Pepper Potts",
                "detail": "Corrected Q4 invoice",
            },
            {
                "tag": at["review"],
                "tone": "info",
                "title": "Design review: Mark 85",
                "detail": "In the workshop, until lunch",
            },
        ],
    }


class DemoBriefService(BriefService):
    """Claude counts as connected, so the command line and quick actions offer Ask, and the
    brief is the one above, written again each day and on Refresh."""

    async def settings(self) -> ClaudeSettings | None:
        return ClaudeSettings(api_key="demo", model=MODEL)

    async def generate(self) -> Brief:
        async with self._lock:
            context = self._context()
            timeline = await self._timeline(context)
            # The morning's brief, also when written again later in the day.
            written = min(context.now, times(context.now)["written"])
            brief = Brief(
                created_at=written.astimezone(UTC),
                kind=kind_for(written.hour),
                content={**brief_content(context.now), "timeline": timeline},
                sources=["Calendar", "Gmail", "Tasks", "Pull requests", "Weather"],
                model=MODEL,
                language="en",
            )
            kind = KICKERS.get(brief.kind, "Brief")
            async with self._sessionmaker() as db:
                db.add(brief)
                # Noted when it was written, not now.
                text = f"{kind} written: {brief.content['headline']}"
                db.add(ActivityEvent(at=brief.created_at, tone="info", text=text, url=None))
                await db.commit()
            self.error = None
            return brief

    async def due(self) -> bool:
        if self.generating:
            return False
        latest = await self.latest()
        now = self._context().now
        return latest is None or latest.created_at.astimezone(now.tzinfo).date() < now.date()


ACTIVITY = [  # minutes ago, tone, text
    (150, "ok", "Calendar and Jira synced"),
    (128, "warn", "TLS certificate for shop.example.com expires in 9 days"),
    (101, "ok", "PR #314 dark mode was merged"),
    (86, "fail", "Pipeline #2214 failed on main"),
    (38, "fail", "staging.example.com is down (HTTP 502)"),
    (24, "warn", "Bank consent for Finance expired"),
    (5, "ok", "Gmail synced · 6 new"),
]


async def seed(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime, fresh: bool = False
) -> None:
    """A fresh demo's tasks and recent activity; once, so what the user does with them stays.
    `fresh`: the day's again, in place of what is there (the public demo, each morning)."""
    async with sessionmaker() as db:
        if fresh:
            for model in (Task, ActivityEvent, Brief):
                await db.execute(delete(model))
        elif await db.scalar(select(func.count()).select_from(Task)):
            return
        today = now.date()
        for text in [
            "Call the accountant today",
            f"Book flight to Monaco today at {hm(times(now)['flight'])}",
            "Buy a birthday gift for Pepper on Friday",
        ]:
            await add_task(db, text, today)
        stamp = datetime.now(UTC)
        db.add_all(
            ActivityEvent(at=stamp - timedelta(minutes=ago), tone=tone, text=text, url=None)
            for ago, tone, text in ACTIVITY
        )
        await db.commit()


async def each_morning(
    sessionmaker: async_sessionmaker[AsyncSession], now: Callable[[], datetime]
) -> None:
    """The public demo's day again after midnight, so its tasks stay today's and what visitors
    added from Ask goes."""
    day = now().date()
    while True:
        await asyncio.sleep(60)
        if now().date() != day:
            day = now().date()
            await seed(sessionmaker, now(), fresh=True)


async def name_the_public_demo(
    sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox
) -> None:
    """The names a visitor of the public demo would have chosen at setup, which it skips."""
    async with sessionmaker() as db:
        if not await store.get_provider_settings(db, box, profile.PROVIDER):
            names = {"name": "Tony", "assistant": "J.A.R.V.I.S.", "color": profile.DEFAULT_COLOR}
            await store.save_provider_settings(db, box, profile.PROVIDER, names)


QUICK_PLAN = "plan my day"
PROPOSAL = "demo-task"


def _line(**data: Any) -> bytes:
    return (json.dumps(data) + "\n").encode()


def _fix_time(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0) + timedelta(minutes=15 - now.minute % 15)


async def answer(question: str, now: datetime) -> AsyncIterator[bytes]:
    """Ask's answer in the demo, the same lines as a real one (see ask.routes.ask)."""
    yield _line(conversation=f"demo-{secrets.token_urlsafe(6)}")
    if QUICK_PLAN not in question.lower():
        yield _line(
            text="This is the demo, so my answers are written in advance: try **Plan today** "
            "in the quick actions. With your own Claude API key in Integrations, you can ask "
            "anything about your dashboard."
        )
        return
    at = times(now)
    plan = [  # start, end, what to do
        (
            now,
            at["review"],
            "fix the 2 failing tests on **PR #318**, so the build is green before the review.",
        ),
        (at["review"], at["review_end"], "Design review: Mark 85, in the workshop."),
        (
            at["leave"],
            at["free"],
            "leave for lunch with Pepper at Nobu (17 min by car), then "
            f"your 1:1 with Happy at {hm(at['one_on_one'])}.",
        ),
        (
            at["free"],
            at["run"],
            "your free afternoon: reply to Pepper about the corrected Q4 "
            f"invoice, then **APP-398** (overdue). Rain starts at {hm(at['rain'])}, so take an "
            "umbrella for the run.",
        ),
        (at["run"], at["run"] + timedelta(minutes=50), "Easy run 8 km, then the evening is yours."),
    ]
    left = [(start, end, text) for start, end, text in plan if end > now]
    if not left:
        yield _line(text="Your day is done: the run was the last thing on it. Rest well.")
        return
    yield _line(
        text="Here is a plan for "
        + ("your day" if now < at["review"] else "the rest of your day")
        + ":\n\n"
    )
    for start, end, text in left:
        when = "Now" if start <= now else hm(start)
        span = when if start == at["leave"] and start > now else f"{when} – {hm(end)}"
        yield _line(text=f"- **{span}:** {text}\n")
    yield _line(
        text="\nShall I add the PR fix as a task?"
        if now < at["review"]
        else "\nPR #318 still has 2 failing tests: shall I add fixing them as a task?"
    )
    summary = f"Add the task “Fix the failing tests on PR #318” for today at {hm(_fix_time(now))}"
    yield _line(proposal={"id": PROPOSAL, "summary": summary})


async def decide(
    sessionmaker: async_sessionmaker[AsyncSession],
    approved: set[str],
    now: datetime,
    assistant: str,
) -> AsyncIterator[bytes]:
    """The answer to the demo's proposal: the task is added for real, in the demo's database."""
    if PROPOSAL not in approved:
        yield _line(done={"id": PROPOSAL, "message": "Cancelled.", "cancelled": True})
        yield _line(text="All right, I left it.")
        return
    title = "Fix the failing tests on PR #318"
    async with sessionmaker() as db:
        # Once: asked again (or by another visitor of the public demo), it is already there.
        if not await db.scalar(select(Task.id).where(Task.title == title, Task.done_at.is_(None))):
            await add_task(db, f"{title} today at {hm(_fix_time(now))}", now.date())
    message = f"Task added: {title}"
    await record(sessionmaker, f"Asked {assistant}: {message}", "ok")
    yield _line(done={"id": PROPOSAL, "message": message})
    yield _line(refresh=True)
    yield _line(text="Done, it is on today's list.")
