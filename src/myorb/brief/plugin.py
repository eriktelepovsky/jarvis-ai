import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from myorb.brief.models import Brief
from myorb.brief.service import PROVIDER
from myorb.plugins.base import Panel, Plugin, WidgetContext

TIME_TAG = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def completed(brief: Brief, now: datetime) -> set[int]:
    """The priorities that are over: those at a time (tagged "13:00") whose event has ended,
    by the calendar the brief kept, or whose time has passed when it has no such event."""
    ends: dict[str, datetime] = {}
    for item in brief.content.get("timeline", []):
        try:
            start = datetime.fromisoformat(item["start"]).astimezone(now.tzinfo)
            end = datetime.fromisoformat(item["end"]).astimezone(now.tzinfo)
        except (KeyError, ValueError):
            continue
        key = f"{start:%H:%M}"
        ends[key] = max(end, ends.get(key, end))
    done = set()
    for index, item in enumerate(brief.content.get("priorities", [])):
        match = TIME_TAG.match(str(item.get("tag", "")).strip())
        if not match:
            continue
        key = f"{int(match[1]):02d}:{match[2]}"
        at = now.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
        if now >= ends.get(key, at):
            done.add(index)
    return done


class BriefPlugin(Plugin):
    """What needs the user today, from the brief Claude wrote, in the side column."""

    id = "brief"
    name = "Daily brief"
    templates_dir = Path(__file__).parent / "templates"
    # After the schedule and tasks, before the weather.
    position = 40

    def panels(self) -> list[Panel]:
        return [
            Panel(
                id="priorities",
                title="Priorities",
                template="priorities.html",
                load=self._load,
                toggle="completed",
            )
        ]

    async def _load(self, context: WidgetContext) -> dict[str, Any]:
        async with context.sessionmaker() as db:
            brief = await db.scalar(select(Brief).order_by(Brief.created_at.desc()).limit(1))
        # Yesterday's priorities are stale by morning: only today's brief is shown. SQLite
        # returns the time without its zone, in UTC.
        today = brief is not None and (
            brief.created_at.replace(tzinfo=brief.created_at.tzinfo or UTC)
            .astimezone(context.now.tzinfo)
            .date()
            == context.now.date()
        )
        return {
            "configured": await context.connections.settings(PROVIDER) is not None,
            "brief": brief if today else None,
            "done": completed(brief, context.now) if today else set(),
        }
