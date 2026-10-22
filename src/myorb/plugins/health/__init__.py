"""Health: sleep and steps from Apple Health (sent by an iPhone Shortcut) and Strava activities."""

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from myorb.connections.providers import ProviderError
from myorb.plugins.base import (
    BriefSection,
    Link,
    Plugin,
    Row,
    SettingsLink,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from myorb.plugins.health import strava
from myorb.plugins.health.models import HealthDay

ICON = "M3 12h4l2-5 4 10 2-5h6"
SETUP = Link("Set up health", "/integrations/health")
# Strava activities listed in the details; "km this week" counts the last 7 days of them.
HISTORY_DAYS = 30


def sleep_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}"


class HealthPlugin(Plugin):
    id = "health"
    name = "Health"
    templates_dir = Path(__file__).parent / "templates"
    position = 55
    settings = SettingsLink(
        "♥", "Sleep and steps from Apple Health, activities from Strava.", "/integrations/health"
    )

    def router(self) -> APIRouter:
        from myorb.plugins.health.routes import router

        return router

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="today", label="Sleep", title="Health", icon=ICON, load=self._widget, weight=0.85
            )
        ]

    async def _days(self, context: WidgetContext) -> list[HealthDay]:
        since = context.now.date() - timedelta(days=7)
        async with context.sessionmaker() as db:
            query = select(HealthDay).where(HealthDay.day >= since).order_by(HealthDay.day.desc())
            return list(await db.scalars(query))

    async def _activities(
        self, context: WidgetContext
    ) -> tuple[list[strava.Activity], str | None, bool]:
        """Strava activities of the last HISTORY_DAYS, an error, and whether Strava is connected."""
        accounts = await context.connections.list(strava.PROVIDER)
        if not accounts:
            return [], None, False
        client = await context.connections.settings(strava.PROVIDER)
        if client is None:
            return [], "The Strava application is missing. Set it up again.", True
        account = accounts[0]

        async def fetch() -> list[strava.Activity]:
            token, updated = await strava.access_token(context.http, client, account.credentials)
            if updated:
                await context.connections.update_credentials(account.id, updated)
            since = context.now - timedelta(days=HISTORY_DAYS)
            return await strava.activities(context.http, token, since)

        try:
            cached = await context.cache.get(
                f"strava:{account.id}:history", fetch, 15 * 60, 6 * 60 * 60
            )
        except ProviderError as error:
            return [], str(error), True
        error = str(cached.error) if cached.error else None
        return cached.value, error, True

    async def _widget(self, context: WidgetContext) -> WidgetData:
        days = await self._days(context)
        activities, error, strava_connected = await self._activities(context)
        today = context.now.date()
        latest: HealthDay | None = days[0] if days else None
        if latest is None and not strava_connected:
            return WidgetData(
                value=None,
                source="Apple Health · Strava",
                message="Nothing is connected yet.",
                connected=False,
                message_action=SETUP,
            )
        sleep = latest.sleep_minutes if latest and latest.day == today else None
        steps = next((d.steps for d in days if d.day == today and d.steps is not None), None)
        week_ago = context.now - timedelta(days=7)
        week_km = round(sum(a.distance_km for a in activities if a.start >= week_ago), 1)
        rows = [
            Row(
                a.name,
                " · ".join(
                    p
                    for p in (
                        a.sport,
                        f"{a.distance_km:g} km" if a.distance_km else "",
                        f"{a.moving_minutes} min",
                    )
                    if p
                ),
                badge=f"{a.start.astimezone(context.now.tzinfo):%a %-d %b}",
                tone="info",
                url=a.url,
            )
            # Newest first.
            for a in sorted(activities, key=lambda a: a.start, reverse=True)
        ]
        rows += [
            Row(
                f"{d.day:%A %-d %b}",
                " · ".join(
                    p
                    for p in (
                        f"Sleep {sleep_text(d.sleep_minutes)}"
                        if d.sleep_minutes is not None
                        else "",
                        f"{d.steps:,} steps" if d.steps is not None else "",
                    )
                    if p
                ),
            )
            for d in days
        ]
        sources = [
            s for s, on in (("Apple Health", bool(days)), ("Strava", strava_connected)) if on
        ]
        status: dict[str, Any] = {"source": " · ".join(sources)}
        if error:
            status |= {
                "message": f"Strava: {error}",
                "message_tone": "warn",
                "message_action": SETUP,
            }
        return WidgetData(
            value=sleep_text(sleep)
            if sleep is not None
            else (f"{week_km:g}" if strava_connected else None),
            summary="sleep last night"
            if sleep is not None
            else ("km this week" if strava_connected else ""),
            label="Sleep" if sleep is not None or not strava_connected else "Km · week",
            stats=[
                Stat(sleep_text(sleep), "Sleep"),
                Stat(f"{steps:,}" if steps is not None else "—", "Steps today"),
                Stat(f"{week_km:g} km" if strava_connected else "—", "Week · Strava"),
            ],
            list_title=f"Last {HISTORY_DAYS} days",
            rows=rows,
            action=Link("Open Strava", "https://www.strava.com/dashboard")
            if strava_connected
            else None,
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        days = await self._days(context)
        activities, _, _ = await self._activities(context)
        lines = []
        today = context.now.date()
        for d in days[:2]:
            when = "Last night" if d.day == today else f"Night before {d.day:%A}"
            if d.sleep_minutes is not None:
                lines.append(f"{when}: slept {sleep_text(d.sleep_minutes)}.")
            if d.steps is not None:
                lines.append(f"Steps on {d.day:%A}: {d.steps:,}.")
        lines += [
            f"Strava {a.start.astimezone(context.now.tzinfo):%A}: {a.name} "
            f"({a.sport}, {a.distance_km:g} km, {a.moving_minutes} min)"
            for a in activities
            if a.start >= context.now - timedelta(days=7)
        ]
        return BriefSection("Health", lines) if lines else None
