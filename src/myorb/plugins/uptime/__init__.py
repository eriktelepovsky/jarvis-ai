"""Uptime: myOrb checks your websites and services every minute and records outages."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import select

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
from myorb.plugins.uptime.checks import Checker
from myorb.plugins.uptime.models import Incident, Monitor

ICON = "M3 12h4l2-5 4 10 2-5h6"
WINDOW = timedelta(days=30)
TLS_WARNING = timedelta(days=14)
SETUP = Link("Add a monitor", "/integrations/uptime")


def duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    if minutes < 24 * 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes // (24 * 60)} d"


def uptime_percent(monitor: Monitor, incidents: list[Incident], now: datetime) -> float:
    start = max(_utc(monitor.created_at), now - WINDOW)
    window = (now - start).total_seconds()
    if window <= 0:
        return 100.0
    down = sum(
        max(
            0.0,
            (
                min(_utc(i.ended_at) if i.ended_at else now, now) - max(_utc(i.started_at), start)
            ).total_seconds(),
        )
        for i in incidents
    )
    return max(0.0, 100 * (1 - down / window))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class UptimePlugin(Plugin):
    id = "uptime"
    name = "Uptime"
    templates_dir = Path(__file__).parent / "templates"
    position = 42
    settings = SettingsLink(
        "↑", "Checks your websites every minute and records outages.", "/integrations/uptime"
    )

    def __init__(self) -> None:
        self.checker = Checker()

    def router(self) -> APIRouter:
        from myorb.plugins.uptime.routes import router

        return router

    def background(self, context: Callable[[], WidgetContext]) -> Awaitable[None]:
        ctx = context()
        return self.checker.run(ctx.sessionmaker, ctx.http)

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="status",
                label="Incidents",
                title="Uptime",
                icon=ICON,
                load=self._widget,
                weight=1.1,
            )
        ]

    async def _state(
        self, context: WidgetContext
    ) -> tuple[list[Monitor], dict[int, list[Incident]]]:
        since = datetime.now(UTC) - WINDOW
        async with context.sessionmaker() as db:
            monitors = list(await db.scalars(select(Monitor).order_by(Monitor.name)))
            incidents = list(
                await db.scalars(
                    select(Incident).where(
                        (Incident.ended_at.is_(None)) | (Incident.ended_at >= since)
                    )
                )
            )
        by_monitor: dict[int, list[Incident]] = {}
        for incident in incidents:
            by_monitor.setdefault(incident.monitor_id, []).append(incident)
        return monitors, by_monitor

    async def _widget(self, context: WidgetContext) -> WidgetData:
        monitors, incidents = await self._state(context)
        if not monitors:
            return WidgetData(
                value=None,
                source="myOrb",
                message="No websites are monitored yet.",
                connected=False,
                message_action=SETUP,
            )
        now = datetime.now(UTC)
        down = [m for m in monitors if m.up is False]
        expiring = [
            m for m in monitors if m.tls_expires_at and _utc(m.tls_expires_at) - now < TLS_WARNING
        ]
        rows = []
        grouped = any(m.group_name for m in monitors)
        for m in sorted(monitors, key=lambda m: (m.up is not False, m.name.lower())):
            open_incident = next((i for i in incidents.get(m.id, []) if i.ended_at is None), None)
            percent = uptime_percent(m, incidents.get(m.id, []), now)
            details = [m.url.split("://", 1)[-1], f"{percent:.2f}% · 30 days"]
            if m.response_ms is not None and m.up:
                details.insert(1, f"{m.response_ms} ms")
            if m.up is False:
                since = (
                    f" · {duration(now - _utc(open_incident.started_at))}" if open_incident else ""
                )
                badge, tone = f"✕ Down{since}", "fail"
                details.insert(0, m.error or "")
            elif m in expiring:
                badge, tone = f"▲ TLS {max(0, (_utc(m.tls_expires_at) - now).days)} d", "warn"
            elif m.up:
                badge, tone = "✓ Up", "ok"
            else:
                badge, tone = "◌ Checking", "info"
            rows.append(
                Row(
                    m.name,
                    " · ".join(d for d in details if d),
                    badge,
                    tone,
                    m.url,
                    # The user's own groups; "No group" only once some monitors have one.
                    group=m.group_name or ("No group" if grouped else ""),
                )
            )
        checked = [m.checked_at for m in monitors if m.checked_at]
        last_30 = sum(len(v) for v in incidents.values())
        average = sum(uptime_percent(m, incidents.get(m.id, []), now) for m in monitors) / len(
            monitors
        )
        return WidgetData(
            value=len(down),
            summary=f"down · {len(monitors) - len(down)} of {len(monitors)} up"
            + (f" · {len(expiring)} TLS expiring" if expiring else ""),
            attention=bool(down or expiring),
            stats=[
                Stat(f"{len(monitors) - len(down)}/{len(monitors)}", "Up"),
                Stat(last_30, "Incidents · 30 d"),
                Stat(f"{average:.2f}%", "30-day uptime"),
            ],
            list_title="Monitors",
            group_label="groups",
            rows=rows,
            action=Link("Manage monitors", "/integrations/uptime"),
            source="myOrb checks every minute",
            updated_at=min(_utc(c) for c in checked).timestamp() if checked else None,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        monitors, incidents = await self._state(context)
        if not monitors:
            return None
        now = datetime.now(UTC)
        lines = []
        for m in monitors:
            open_incident = next((i for i in incidents.get(m.id, []) if i.ended_at is None), None)
            if m.up is False:
                since = (
                    f" for {duration(now - _utc(open_incident.started_at))}"
                    if open_incident
                    else ""
                )
                lines.append(f"{m.name} ({m.url}) is DOWN{since}: {m.error}")
            recent = [
                i
                for i in incidents.get(m.id, [])
                if i.ended_at and now - _utc(i.ended_at) < timedelta(days=1)
            ]
            for i in recent:
                lines.append(
                    f"{m.name} was down for {duration(_utc(i.ended_at) - _utc(i.started_at))} "
                    "in the last 24 hours."
                )
            if m.tls_expires_at and _utc(m.tls_expires_at) - now < TLS_WARNING:
                lines.append(
                    f"{m.name}: TLS certificate expires in "
                    f"{(_utc(m.tls_expires_at) - now).days} days."
                )
        return BriefSection("Uptime", lines or [f"All {len(monitors)} monitored websites are up."])
