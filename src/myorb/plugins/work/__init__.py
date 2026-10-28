"""Work: Jira and Asana as one number around the orb, when the user chooses so.

The two plugins load as always; this one shows their rows together, grouped by the service and
then by workspace, while their own numbers step aside (`hides`). Their brief sections and Ask's
rows stay as they are.
"""

from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter

from myorb.plugins.base import Plugin, SettingsLink, Stat, Widget, WidgetContext, WidgetData

ICON = "M4 8h16v11H4zM9 8V5h6v3M4 13h16"
PROVIDER = "work"
PAGE = "/integrations/work"


class WorkPlugin(Plugin):
    id = "work"
    name = "Work"
    templates_dir = Path(__file__).parent / "templates"
    settings = SettingsLink(
        "◫", "Jira and Asana as one number around the orb, or each on its own.", PAGE
    )
    ask_rows = False

    def __init__(self, parts: list[tuple[Plugin, Widget]]) -> None:
        self.parts = parts  # (plugin, widget) of each service, in the order they are listed
        self.position = min(plugin.position for plugin, _ in parts)
        self.merged = False

    def hides(self, plugin_id: str, widget_id: str) -> bool:
        return self.merged and any(p.id == plugin_id and w.id == widget_id for p, w in self.parts)

    def router(self) -> APIRouter:
        from myorb.plugins.work.routes import router

        return router

    def widgets(self) -> list[Widget]:
        if not self.merged:
            return []
        return [
            Widget(
                id="assigned",
                label="Work",
                title="Work · assigned to you",
                icon=ICON,
                load=self._widget,
                refresh=min(widget.refresh for _, widget in self.parts),
            )
        ]

    async def _widget(self, context: WidgetContext) -> WidgetData:
        loaded = [(plugin, await widget.load(context)) for plugin, widget in self.parts]
        return merge([(plugin.name, data) for plugin, data in loaded if data.connected])


def merge(parts: list[tuple[str, WidgetData]]) -> WidgetData:
    """The services' numbers as one: rows grouped by service, then by its workspace; what is
    overdue first, the backlog last."""
    if not parts:
        return WidgetData(value=None, connected=False)
    shown = [(name, data) for name, data in parts if data.value is not None]
    rows = [
        replace(row, group=name, subgroup=row.group) for name, data in shown for row in data.rows
    ]
    rows.sort(key=lambda row: ("overdue" not in row.tags, "backlog" in row.tags))
    tagged = lambda tag: sum(tag in row.tags for row in rows)  # noqa: E731
    overdue = tagged("overdue")
    value = sum(data.value for _, data in shown if isinstance(data.value, int))
    problem = next((data for _, data in parts if data.message), None)
    links = [
        link for name, data in shown for link in ([data.action] if data.action else []) + data.links
    ]
    updated = [data.updated_at for _, data in shown if data.updated_at]
    return WidgetData(
        value=value if shown else None,
        summary="assigned to you" + (f" · {overdue} overdue" if overdue else ""),
        attention=any(data.attention for _, data in shown),
        stats=[
            Stat(overdue, "Overdue", filter="overdue"),
            Stat(tagged("today"), "Due today", filter="today"),
            Stat(tagged("progress"), "In progress", filter="progress"),
        ],
        list_title="Assigned to you",
        group_label="services",
        rows=rows,
        links=links,
        source=" and ".join(name for name, _ in parts),
        updated_at=min(updated) if updated else None,
        message=problem.message if problem else None,
        message_tone=problem.message_tone if problem else "info",
        message_action=problem.message_action if problem else None,
    )
