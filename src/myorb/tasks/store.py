from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.tasks import repeat as repeats
from myorb.tasks.models import Task
from myorb.tasks.parse import parse_task

MAX_TITLE = 500


async def add_task(db: AsyncSession, text: str, today: date) -> Task:
    """Add a task written in plain language; its due date and time are recognized."""
    parsed = parse_task(text.strip(), today)
    task = Task(
        title=(parsed.title or text.strip())[:MAX_TITLE],
        due_date=parsed.due_date,
        due_time=parsed.due_time,
        repeat=parsed.repeat,
        created_at=datetime.now(UTC),
    )
    db.add(task)
    await db.commit()
    return task


async def open_tasks(db: AsyncSession) -> list[Task]:
    query = select(Task).where(Task.done_at.is_(None)).order_by(Task.created_at)
    return list(await db.scalars(query))


async def done_tasks(db: AsyncSession, since: datetime) -> list[Task]:
    # Times are stored in UTC without the zone (SQLite): compare in UTC too, or "since local
    # midnight" misses the first hours of the day.
    query = select(Task).where(Task.done_at >= since.astimezone(UTC)).order_by(Task.done_at.desc())
    return list(await db.scalars(query))


async def moved_tasks(db: AsyncSession, since: datetime) -> list[Task]:
    """Repeating tasks done (moved on to their next date) since then."""
    query = select(Task).where(
        Task.done_at.is_(None), Task.moved_at >= since.astimezone(UTC), Task.last_due.is_not(None)
    )
    return list(await db.scalars(query.order_by(Task.moved_at.desc())))


async def get_task(db: AsyncSession, task_id: int) -> Task | None:
    return await db.get(Task, task_id)


async def set_done(db: AsyncSession, task: Task, done: bool) -> None:
    task.done_at = datetime.now(UTC) if done else None
    await db.commit()


async def complete(db: AsyncSession, task: Task, today: date) -> date | None:
    """Mark a task done. One that repeats moves on to its next due date instead, which is
    returned (its last one is kept, so undo can bring it back)."""
    if task.repeat and task.due_date:
        task.last_due = task.due_date
        task.due_date = repeats.next_due(task.repeat, task.due_date, today)
        task.moved_at = datetime.now(UTC)
        await db.commit()
        return task.due_date
    await set_done(db, task, True)
    return None


async def reopen(db: AsyncSession, task: Task) -> None:
    """Undo marking a task done: one that repeats goes back to the due date it moved on from."""
    if task.done_at is None and task.repeat and task.last_due:
        task.due_date, task.last_due, task.moved_at = task.last_due, None, None
        await db.commit()
        return
    await set_done(db, task, False)


async def update_task(
    db: AsyncSession,
    task: Task,
    title: str,
    due_date: date | None,
    due_time: time | None,
    repeat: str | None = "",
    today: date | None = None,
) -> None:
    """Change a task. `repeat` "" keeps how it repeats, None stops it, a rule sets it (a task
    that repeats needs a due date: today, if it has none)."""
    task.title = title.strip()[:MAX_TITLE] or task.title
    if repeat != "":
        task.repeat = repeat
        task.last_due = None
        if repeat and due_date is None:
            due_date = today or date.today()
        if repeat and repeat.startswith("month:") and due_date:
            # Monthly on the day it is due.
            every = repeat.split(":")[1]
            task.repeat = f"month:{every}:{due_date.day}"
    task.due_date = due_date
    task.due_time = due_time if due_date else None
    await db.commit()


async def delete_task(db: AsyncSession, task: Task) -> None:
    await db.delete(task)
    await db.commit()


def recently_done_since(now: datetime) -> datetime:
    """Done tasks stay visible for a week."""
    return now - timedelta(days=7)
