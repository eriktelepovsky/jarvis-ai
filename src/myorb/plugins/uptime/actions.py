"""What Claude may do with uptime monitoring in Ask: add a website to watch, once confirmed."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.uptime.models import Monitor

if TYPE_CHECKING:
    from myorb.plugins.uptime import UptimePlugin


def _checked(data: dict[str, Any]) -> tuple[str, str, str]:
    url = data["url"].strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ActionError("Give the full address, e.g. https://example.com.")
    name = " ".join(data["name"].split())[:100] or parts.netloc
    return name, url[:500], " ".join(data["group"].split())[:60]


async def _summary(context: WidgetContext, data: dict[str, Any]) -> str:
    name, url, group = _checked(data)
    return f"Watch {name} ({url})" + (f" in {group}" if group else "")


def ask_actions(plugin: "UptimePlugin") -> list[AskAction]:
    async def add(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        name, url, group = _checked(data)
        async with context.sessionmaker() as db:
            monitor = Monitor(name=name, url=url, group_name=group, created_at=datetime.now(UTC))
            db.add(monitor)
            await db.commit()
            # Checked right away, as when added on the settings page.
            await plugin.checker.check(db, context.http, monitor)
            await db.commit()
            monitor_id, up = monitor.id, monitor.up

        async def undo(context: WidgetContext) -> str:
            async with context.sessionmaker() as db:
                if found := await db.get(Monitor, monitor_id):
                    await db.delete(found)
                    await db.commit()
            return f"Stopped watching {name}."

        state = "it is up" if up else "it is down right now" if up is False else "checked soon"
        return ActionResult(f"Watching {name} ({url}); {state}.", undo)

    return [
        AskAction(
            "add_monitor",
            "Start watching a website or service: myOrb checks it every minute and says when "
            "it goes down. group is the user's own grouping (a project, a customer) or empty.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string", "description": "The full address, https://…"},
                    "group": {"type": "string"},
                },
                "required": ["name", "url", "group"],
                "additionalProperties": False,
            },
            _summary,
            add,
        )
    ]
