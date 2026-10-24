from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select

from myorb.brief.models import Brief
from myorb.brief.service import PROVIDER
from myorb.plugins.base import Panel, Plugin, WidgetContext


class BriefPlugin(Plugin):
    """What needs the user today, from the brief Claude wrote, in the side column."""

    id = "brief"
    name = "Daily brief"
    templates_dir = Path(__file__).parent / "templates"
    # After the schedule and tasks, before the weather.
    position = 40

    def panels(self) -> list[Panel]:
        return [
            Panel(id="priorities", title="Priorities", template="priorities.html", load=self._load)
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
        }
