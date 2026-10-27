from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.activity.store import record
from myorb.db import get_db
from myorb.tasks import repeat as repeats
from myorb.tasks import store
from myorb.tasks.models import Task
from myorb.tasks.schedule import due_label, group

router = APIRouter()

REPEAT_CHOICES = repeats.CHOICES

repeat_label = repeats.label

Db = Annotated[AsyncSession, Depends(get_db)]

# Tells the welcome screen to reload its widgets and panels.
REFRESH = {"HX-Trigger": "myorb:refresh"}


def _now(request: Request) -> datetime:
    return datetime.now(request.app.state.timezone)


def _done(request: Request) -> Response:
    """After a change: the welcome screen's HTMX requests refresh in place, plain forms go
    back to the list.

    A form on the tasks page is boosted (hx-boost on the body): an empty 204 would swap
    nothing yet leave /tasks/<id> in the address bar, which only takes POST, so reloading
    it showed "Method Not Allowed". HX-Location loads the list instead, without a full page
    load, so music keeps playing.
    """
    if request.headers.get("HX-Boosted"):
        return Response(status_code=204, headers={"HX-Location": "/tasks"})
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers=REFRESH)
    return RedirectResponse("/tasks", status_code=303)


async def _task_or_404(db: AsyncSession, task_id: int) -> Task:
    task = await store.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, db: Db, edit: int | None = None) -> Response:
    now = _now(request)
    groups = group(await store.open_tasks(db), now)
    sections = [
        ("Overdue", groups.overdue, "fail"),
        ("Today", groups.today, "info"),
        ("Upcoming", groups.upcoming, "neutral"),
        ("No date", groups.someday, "neutral"),
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/index.html",
        {
            "sections": [section for section in sections if section[1]],
            "done": await store.done_tasks(db, store.recently_done_since(now)),
            "label": lambda task: due_label(task, now),
            "repeat_label": repeat_label,
            "repeat_choices": REPEAT_CHOICES,
            "edit": edit,
        },
    )


@router.post("/tasks")
async def add(
    request: Request,
    db: Db,
    text: Annotated[str, Form()],
    due_date: Annotated[str, Form()] = "",
    due_time: Annotated[str, Form()] = "",
    repeat: Annotated[str, Form()] = "",
) -> Response:
    """A task written in plain language; a date, time or repeat chosen in the form's own
    fields (the Add dialog) wins over what the text says."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Enter a task")
    today = _now(request).date()
    task = await store.add_task(db, text, today)
    if due_date or due_time or (repeat and repeat != "none"):
        try:
            parsed_date = date.fromisoformat(due_date) if due_date else task.due_date
            parsed_time = time.fromisoformat(due_time) if due_time else task.due_time
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid date or time") from error
        if repeat and repeat != "none" and not repeats.is_rule(repeat):
            raise HTTPException(status_code=400, detail="Invalid repeat")
        if parsed_time and not parsed_date:
            parsed_date = today
        rule = repeat if repeat and repeat != "none" else ""
        await store.update_task(db, task, task.title, parsed_date, parsed_time, rule, today)
    await record(request.app.state.sessionmaker, f"Task added: {task.title}", "info", "/tasks")
    return _done(request)


@router.post("/command", response_class=HTMLResponse)
async def command(request: Request, db: Db, text: Annotated[str, Form()]) -> Response:
    """The command line under the orb. For now it adds tasks; later it will also ask Claude."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Enter a task")
    task = await store.add_task(db, text, _now(request).date())
    await record(request.app.state.sessionmaker, f"Task added: {task.title}", "info", "/tasks")
    return request.app.state.templates.TemplateResponse(
        request,
        "welcome/command_result.html",
        {"task": task, "due": due_label(task, _now(request)), "repeat": repeats.label(task)},
        headers=REFRESH,
    )


@router.post("/tasks/{task_id}/done")
async def mark_done(request: Request, db: Db, task_id: int) -> Response:
    task = await _task_or_404(db, task_id)
    following = await store.complete(db, task, _now(request).date())
    then = f" · next {following:%a %-d %b}" if following else ""
    await record(request.app.state.sessionmaker, f"Done: {task.title}{then}", "ok", "/tasks")
    return _done(request)


@router.post("/tasks/{task_id}/undo")
async def mark_open(request: Request, db: Db, task_id: int) -> Response:
    await store.reopen(db, await _task_or_404(db, task_id))
    return _done(request)


@router.post("/tasks/{task_id}")
async def edit(
    request: Request,
    db: Db,
    task_id: int,
    title: Annotated[str, Form()],
    due_date: Annotated[str, Form()] = "",
    due_time: Annotated[str, Form()] = "",
    repeat: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        parsed_date = date.fromisoformat(due_date) if due_date else None
        parsed_time = time.fromisoformat(due_time) if due_time else None
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid date or time") from error
    if repeat and repeat != "none" and not repeats.is_rule(repeat):
        raise HTTPException(status_code=400, detail="Invalid repeat")
    task = await _task_or_404(db, task_id)
    # Not sent (an older form): as it was. "none": no longer repeats.
    if not repeat or repeat == task.repeat:
        rule = ""
    else:
        rule = None if repeat == "none" else repeat
    await store.update_task(db, task, title, parsed_date, parsed_time, rule, _now(request).date())
    return _done(request)


@router.post("/tasks/{task_id}/delete")
async def delete(request: Request, db: Db, task_id: int) -> Response:
    await store.delete_task(db, await _task_or_404(db, task_id))
    return _done(request)
