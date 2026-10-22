from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.tasks.models import Task
from jarvis_hud.tasks.parse import parse_task

MAX_TITLE = 500


async def add_task(db: AsyncSession, text: str, today: date) -> Task:
    """Add a task written in plain language; its due date and time are recognized."""
    parsed = parse_task(text.strip(), today)
    task = Task(
        title=(parsed.title or text.strip())[:MAX_TITLE],
        due_date=parsed.due_date,
        due_time=parsed.due_time,
        created_at=datetime.now(UTC),
    )
    db.add(task)
    await db.commit()
    return task


async def open_tasks(db: AsyncSession) -> list[Task]:
    query = select(Task).where(Task.done_at.is_(None)).order_by(Task.created_at)
    return list(await db.scalars(query))


async def done_tasks(db: AsyncSession, since: datetime) -> list[Task]:
    query = select(Task).where(Task.done_at >= since).order_by(Task.done_at.desc())
    return list(await db.scalars(query))


async def get_task(db: AsyncSession, task_id: int) -> Task | None:
    return await db.get(Task, task_id)


async def set_done(db: AsyncSession, task: Task, done: bool) -> None:
    task.done_at = datetime.now(UTC) if done else None
    await db.commit()


async def update_task(
    db: AsyncSession, task: Task, title: str, due_date: date | None, due_time: time | None
) -> None:
    task.title = title.strip()[:MAX_TITLE] or task.title
    task.due_date = due_date
    task.due_time = due_time if due_date else None
    await db.commit()


async def delete_task(db: AsyncSession, task: Task) -> None:
    await db.delete(task)
    await db.commit()


def recently_done_since(now: datetime) -> datetime:
    """Done tasks stay visible for a week."""
    return now - timedelta(days=7)
