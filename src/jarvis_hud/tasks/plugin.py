from pathlib import Path
from typing import Any

from jarvis_hud.plugins.base import (
    Action,
    Input,
    Link,
    Panel,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from jarvis_hud.tasks import store
from jarvis_hud.tasks.schedule import due_label, group

ICON = "M4 7l2 2 4-4M4 17l2 2 4-4M14 7h6M14 17h6"
ROWS_SHOWN = 12
PLACEHOLDER = "Add a task… e.g. “call the accountant tomorrow 10:00”"


class TasksPlugin(Plugin):
    """The to-do list built into Jarvis."""

    id = "tasks"
    name = "Jarvis"
    templates_dir = Path(__file__).parent / "templates"
    position = 30

    def widgets(self) -> list[Widget]:
        return [Widget(id="open", label="To-do", title="To-do", icon=ICON, load=self._widget)]

    def panels(self) -> list[Panel]:
        return [Panel(id="today", title="To-do", template="today.html", load=self._today)]

    async def _widget(self, context: WidgetContext) -> WidgetData:
        now = context.now
        async with context.sessionmaker() as db:
            tasks = await store.open_tasks(db)
            done = await store.done_tasks(
                db, now.replace(hour=0, minute=0, second=0, microsecond=0)
            )
        groups = group(tasks, now)
        overdue = len(groups.overdue)
        tone = {"overdue": "fail", "today": "info"}
        rows = []
        for name in ("overdue", "today", "upcoming", "someday"):
            for task in getattr(groups, name):
                rows.append(
                    Row(
                        title=task.title,
                        badge=due_label(task, now),
                        tone=tone.get(name, "neutral"),
                        actions=[Action("Done", f"/tasks/{task.id}/done")],
                    )
                )
        return WidgetData(
            value=len(tasks),
            summary="open" + (f" · {overdue} overdue" if overdue else ""),
            attention=overdue > 0,
            stats=[
                Stat(len(tasks), "Open"),
                Stat(overdue, "Overdue"),
                Stat(len(done), "Done today"),
            ],
            list_title="Open tasks",
            rows=rows[:ROWS_SHOWN],
            action=Link("All tasks", "/tasks"),
            input=Input("/tasks", PLACEHOLDER, "New task"),
            source="Jarvis",
        )

    async def _today(self, context: WidgetContext) -> dict[str, Any]:
        async with context.sessionmaker() as db:
            groups = group(await store.open_tasks(db), context.now)
        return {
            "tasks": [*groups.overdue, *groups.today],
            "overdue": {task.id for task in groups.overdue},
            "label": lambda task: due_label(task, context.now),
            "upcoming": len(groups.upcoming) + len(groups.someday),
        }
