from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from myorb.plugins.base import (
    Action,
    AskAction,
    BriefSection,
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
from myorb.tasks import store
from myorb.tasks.repeat import label as repeat_label
from myorb.tasks.schedule import due_label, group

ICON = "M4 7l2 2 4-4M4 17l2 2 4-4M14 7h6M14 17h6"
ROWS_SHOWN = 12
# How long a task done in the side panel stays there, fading out (welcome.css), to be brought
# back by its box.
DONE_SHOWN = timedelta(seconds=8)
PLACEHOLDER = "Add a task… e.g. “call the accountant tomorrow 10:00”"


class TasksPlugin(Plugin):
    """The to-do list built into myOrb."""

    id = "tasks"
    name = "myOrb"
    templates_dir = Path(__file__).parent / "templates"
    position = 30

    def widgets(self) -> list[Widget]:
        return [Widget(id="open", label="Tasks", title="Tasks", icon=ICON, load=self._widget)]

    def panels(self) -> list[Panel]:
        return [
            Panel(
                id="today",
                title="Tasks",
                template="today.html",
                load=self._today,
                add=Input("/tasks", PLACEHOLDER, "New task", with_due=True),
            )
        ]

    def ask_actions(self) -> list[AskAction]:
        from myorb.tasks.actions import ask_actions

        return ask_actions()

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
                        subtitle=f"↻ {repeat_label(task)}" if task.repeat else "",
                        badge=due_label(task, now),
                        tone=tone.get(name, "neutral"),
                        url=f"/tasks?edit={task.id}",
                        url_label="Edit",
                        actions=[Action("Done", f"/tasks/{task.id}/done")],
                        ref=f"task {task.id}",
                    )
                )
        rows = rows[:ROWS_SHOWN]
        # Done today, so a task checked off by mistake can be brought back.
        for task in done:
            rows.append(
                Row(
                    title=task.title,
                    badge="✓ Done",
                    tone="ok",
                    actions=[Action("Undo", f"/tasks/{task.id}/undo")],
                    ref=f"task {task.id}",
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
            rows=rows,
            action=Link("All tasks", "/tasks"),
            input=Input("/tasks", PLACEHOLDER, "New task"),
            source="myOrb",
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        async with context.sessionmaker() as db:
            groups = group(await store.open_tasks(db), context.now)

        def named(task) -> str:
            return task.title + (f" (repeats {repeat_label(task)})" if task.repeat else "")

        now = context.now
        lines = [f"Overdue ({due_label(t, now)}): {named(t)}" for t in groups.overdue]
        lines += [f"Due today {due_label(t, now)}: {named(t)}" for t in groups.today]
        lines += [f"Upcoming ({due_label(t, now)}): {named(t)}" for t in groups.upcoming[:5]]
        lines += [f"No due date: {named(t)}" for t in groups.someday[:5]]
        return BriefSection("Tasks", lines or ["No open tasks."])

    async def _today(self, context: WidgetContext) -> dict[str, Any]:
        async with context.sessionmaker() as db:
            groups = group(await store.open_tasks(db), context.now)
            # Done a moment ago: still listed, struck through, to be brought back if by mistake.
            since = context.now - DONE_SHOWN
            done = await store.done_tasks(db, since)
            moved = await store.moved_tasks(db, since)
        # Each where it was before it was done: a task that moved on to its next date by the
        # date it had; one done by its date too. So checking one off moves nothing.
        moved_ids = {task.id for task in moved}
        listed = [
            t for t in [*groups.overdue, *groups.today, *groups.someday] if t.id not in moved_ids
        ]
        before = [*done, *moved]
        dates = {
            task.id: task.last_due if task.id in moved_ids else task.due_date for task in before
        }
        undated = {task.id: task.due_date for task in before}
        try:
            for task in before:
                task.due_date = dates[task.id]
            shown = group([*listed, *before], context.now)
        finally:
            for task in before:
                task.due_date = undated[task.id]
        upcoming_ids = {task.id for task in shown.upcoming}
        tasks = [
            t for t in [*shown.overdue, *shown.today, *shown.someday] if t.id not in upcoming_ids
        ]

        def since_done(task) -> float:
            at = task.done_at or task.moved_at
            return (context.now - at.replace(tzinfo=at.tzinfo or UTC)).total_seconds()

        return {
            "done": {task.id for task in before},
            # How far through its fading each is, so a reload of the panel does not start it anew.
            "faded": {
                task.id: round(min(since_done(task), DONE_SHOWN.total_seconds()), 2)
                for task in before
            },
            # Tasks without a date have no day of their own: they wait here, after today's.
            "tasks": tasks,
            "overdue": {task.id for task in groups.overdue},
            "label": lambda task: (
                (due_label(task, context.now) or "No due date")
                + (f" · ↻ {repeat_label(task)}" if task.repeat else "")
            ),
            "upcoming": len(groups.upcoming),
        }
