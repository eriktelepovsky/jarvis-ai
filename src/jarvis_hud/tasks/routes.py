from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.db import get_db
from jarvis_hud.tasks import store
from jarvis_hud.tasks.models import Task
from jarvis_hud.tasks.schedule import due_label, group

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

# Tells the welcome screen to reload its widgets and panels.
REFRESH = {"HX-Trigger": "jarvis:refresh"}


def _now(request: Request) -> datetime:
    return datetime.now(request.app.state.timezone)


def _done(request: Request) -> Response:
    """After a change: HTMX requests refresh in place, plain forms go back to the list."""
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
            "edit": edit,
        },
    )


@router.post("/tasks")
async def add(request: Request, db: Db, text: Annotated[str, Form()]) -> Response:
    if not text.strip():
        raise HTTPException(status_code=400, detail="Enter a task")
    await store.add_task(db, text, _now(request).date())
    return _done(request)


@router.post("/tasks/{task_id}/done")
async def mark_done(request: Request, db: Db, task_id: int) -> Response:
    await store.set_done(db, await _task_or_404(db, task_id), True)
    return _done(request)


@router.post("/tasks/{task_id}/undo")
async def mark_open(request: Request, db: Db, task_id: int) -> Response:
    await store.set_done(db, await _task_or_404(db, task_id), False)
    return _done(request)


@router.post("/tasks/{task_id}")
async def edit(
    request: Request,
    db: Db,
    task_id: int,
    title: Annotated[str, Form()],
    due_date: Annotated[str, Form()] = "",
    due_time: Annotated[str, Form()] = "",
) -> Response:
    try:
        parsed_date = date.fromisoformat(due_date) if due_date else None
        parsed_time = time.fromisoformat(due_time) if due_time else None
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid date or time") from error
    await store.update_task(db, await _task_or_404(db, task_id), title, parsed_date, parsed_time)
    return _done(request)


@router.post("/tasks/{task_id}/delete")
async def delete(request: Request, db: Db, task_id: int) -> Response:
    await store.delete_task(db, await _task_or_404(db, task_id))
    return _done(request)
