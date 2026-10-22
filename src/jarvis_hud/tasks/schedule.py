"""Sorting tasks by when they are due and describing it, relative to now."""

from dataclasses import dataclass, field
from datetime import datetime

from jarvis_hud.tasks.models import Task


@dataclass
class Groups:
    overdue: list[Task] = field(default_factory=list)
    today: list[Task] = field(default_factory=list)
    upcoming: list[Task] = field(default_factory=list)
    someday: list[Task] = field(default_factory=list)

    def all(self) -> list[Task]:
        return [*self.overdue, *self.today, *self.upcoming, *self.someday]


def is_overdue(task: Task, now: datetime) -> bool:
    if task.due_date is None:
        return False
    if task.due_date < now.date():
        return True
    return task.due_date == now.date() and task.due_time is not None and task.due_time < now.time()


def group(tasks: list[Task], now: datetime) -> Groups:
    groups = Groups()
    for task in sorted(
        tasks, key=lambda t: (t.due_date or now.date(), t.due_time or now.time().min, t.created_at)
    ):
        if task.due_date is None:
            groups.someday.append(task)
        elif is_overdue(task, now):
            groups.overdue.append(task)
        elif task.due_date == now.date():
            groups.today.append(task)
        else:
            groups.upcoming.append(task)
    groups.someday.sort(key=lambda t: t.created_at)
    return groups


def due_label(task: Task, now: datetime) -> str:
    """When a task is due, compactly: "10:00", "Tomorrow", "Fri 9:00", "24 Oct", "3d late"."""
    if task.due_date is None:
        return ""
    days = (task.due_date - now.date()).days
    at = f" {task.due_time:%H:%M}" if task.due_time else ""
    if is_overdue(task, now):
        return f"{-days}d late" if days < 0 else f"Since {task.due_time:%H:%M}"
    if days == 0:
        return at.strip() or "Today"
    if days == 1:
        return "Tomorrow" + at
    if days < 7:
        return f"{task.due_date:%a}" + at
    return f"{task.due_date:%-d %b}" + at
