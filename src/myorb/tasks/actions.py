"""What Claude may do with the task list in Ask, each after the user confirms it."""

from datetime import date, time
from typing import Any

from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.tasks import store
from myorb.tasks.models import Task
from myorb.tasks.schedule import due_label

TASK_ID = {"type": "integer", "description": "The task's id, from its ref (task 12 → 12)."}


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


async def _task(context: WidgetContext, task_id: int) -> Task:
    async with context.sessionmaker() as db:
        task = await store.get_task(db, task_id)
    if task is None:
        raise ActionError(f"There is no task {task_id}.")
    return task


async def _titled(context: WidgetContext, data: dict[str, Any], what: str) -> str:
    """The confirmation for an action on a task, naming it: "Mark “Call mom” as done"."""
    task = await _task(context, data["task_id"])
    return what.replace("{task}", f"“{task.title}”")


async def _about(text: str) -> str:
    return text


def _when(task: Task, context: WidgetContext) -> str:
    label = due_label(task, context.now)
    return f" ({label})" if label else ""


async def _add(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    async with context.sessionmaker() as db:
        task = await store.add_task(db, data["text"], context.now.date())
    task_id, title = task.id, task.title

    async def undo(context: WidgetContext) -> str:
        async with context.sessionmaker() as db:
            if found := await store.get_task(db, task_id):
                await store.delete_task(db, found)
        return f"Removed the task “{title}”."

    return ActionResult(f"Added the task “{title}”{_when(task, context)}.", undo)


async def _set_done(context: WidgetContext, task_id: int, done: bool) -> Task:
    async with context.sessionmaker() as db:
        task = await store.get_task(db, task_id)
        if task is None:
            raise ActionError(f"There is no task {task_id}.")
        await store.set_done(db, task, done)
        return task


async def _complete(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    async with context.sessionmaker() as db:
        task = await store.get_task(db, data["task_id"])
        if task is None:
            raise ActionError(f"There is no task {data['task_id']}.")
        # One that repeats moves on to its next due date.
        following = await store.complete(db, task, context.now.date())
    task_id, title = task.id, task.title

    async def undo(context: WidgetContext) -> str:
        async with context.sessionmaker() as db:
            if found := await store.get_task(db, task_id):
                await store.reopen(db, found)
        return f"Brought back “{title}”."

    then = f" It repeats: next due {following:%A %-d %B}." if following else ""
    return ActionResult(f"Done: “{title}”.{then}", undo)


def _due(data: dict[str, Any]) -> tuple[date | None, time | None]:
    try:
        due_date = date.fromisoformat(data["due_date"]) if data["due_date"] else None
        due_time = time.fromisoformat(data["due_time"]) if data["due_time"] and due_date else None
    except ValueError:
        raise ActionError("Give the date as YYYY-MM-DD and the time as HH:MM.") from None
    return due_date, due_time


async def _update(
    context: WidgetContext,
    task_id: int,
    title: str | None,
    due: tuple[date | None, time | None] | None,
) -> tuple[Task, str, date | None, time | None]:
    """Change a task; returns it and what it was before (title, due date, time)."""
    async with context.sessionmaker() as db:
        task = await store.get_task(db, task_id)
        if task is None:
            raise ActionError(f"There is no task {task_id}.")
        before = (task.title, task.due_date, task.due_time)
        due_date, due_time = due if due is not None else (task.due_date, task.due_time)
        await store.update_task(db, task, title or task.title, due_date, due_time)
        return task, *before


async def _reschedule(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    task, _, old_date, old_time = await _update(context, data["task_id"], None, _due(data))

    async def undo(context: WidgetContext) -> str:
        await _update(context, task.id, None, (old_date, old_time))
        return f"“{task.title}” is due as before."

    when = _when(task, context) or " (no date)"
    return ActionResult(f"“{task.title}” is now due{when}.", undo)


async def _rename(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    task, old_title, _, _ = await _update(context, data["task_id"], data["title"], None)

    async def undo(context: WidgetContext) -> str:
        await _update(context, task.id, old_title, None)
        return f"Renamed back to “{old_title}”."

    return ActionResult(f"Renamed “{old_title}” to “{task.title}”.", undo)


def ask_actions() -> list[AskAction]:
    return [
        AskAction(
            "add_task",
            "Add a task to the user's task list in myOrb. Write it as the user would type it, "
            "with the due date and time in plain words if any, e.g. "
            '"call the accountant tomorrow 10:00" or "send the invoice 24.10.". A task that '
            'repeats says how, e.g. "take out the bins every Monday", "pay the rent every '
            'month on the 1st", "water the plants every 3 days", "standup every weekday 9:00".',
            _schema(text={"type": "string", "description": "The task, in plain language."}),
            lambda c, d: _about(f"Add the task “{d['text']}”"),
            _add,
        ),
        AskAction(
            "complete_task",
            "Mark one of the user's myOrb tasks as done.",
            _schema(task_id=TASK_ID),
            lambda c, d: _titled(c, d, "Mark {task} as done"),
            _complete,
        ),
        AskAction(
            "reschedule_task",
            "Change when one of the user's myOrb tasks is due, or remove its date "
            "(empty due_date). due_time is optional (empty for the whole day).",
            _schema(
                task_id=TASK_ID,
                due_date={"type": "string", "description": "YYYY-MM-DD, or empty for no date."},
                due_time={"type": "string", "description": "HH:MM, or empty."},
            ),
            lambda c, d: _titled(
                c,
                d,
                "Make {task} due "
                + (
                    f"{d['due_date']} {d['due_time']}".strip()
                    if d["due_date"]
                    else "without a date"
                ),
            ),
            _reschedule,
        ),
        AskAction(
            "rename_task",
            "Change the title of one of the user's myOrb tasks.",
            _schema(task_id=TASK_ID, title={"type": "string"}),
            lambda c, d: _titled(c, d, f"Rename {{task}} to “{d['title']}”"),
            _rename,
        ),
    ]
