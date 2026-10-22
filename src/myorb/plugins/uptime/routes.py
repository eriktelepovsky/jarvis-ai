from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.db import get_db
from myorb.plugins.uptime.models import Monitor

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
PAGE = "/integrations/uptime"


async def _page(
    request: Request, db: AsyncSession, error: str | None = None, values: dict | None = None
) -> Response:
    monitors = list(await db.scalars(select(Monitor).order_by(Monitor.group_name, Monitor.name)))
    groups = sorted({m.group_name for m in monitors if m.group_name})
    return request.app.state.templates.TemplateResponse(
        request,
        "uptime/settings.html",
        {"monitors": monitors, "groups": groups, "error": error, "values": values or {}},
        status_code=400 if error else 200,
    )


@router.get(PAGE, response_class=HTMLResponse)
async def page(request: Request, db: Db) -> Response:
    return await _page(request, db)


@router.post(PAGE)
async def add(
    request: Request,
    db: Db,
    name: Annotated[str, Form()],
    url: Annotated[str, Form()],
    group: Annotated[str, Form()] = "",
) -> Response:
    name, url, group = name.strip(), url.strip(), _group(group)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return await _page(
            request,
            db,
            "Enter the full address, e.g. https://example.com.",
            {"name": name, "url": url, "group": group},
        )
    monitor = Monitor(
        name=name[:100] or parts.netloc,
        url=url[:500],
        group_name=group,
        created_at=datetime.now(UTC),
    )
    db.add(monitor)
    await db.commit()
    # Check right away, so the result is there when the user returns to the welcome screen.
    plugin = request.app.state.plugins.get("uptime")
    if plugin is not None:
        await plugin.checker.check(db, request.app.state.http, monitor)
        await db.commit()
    return RedirectResponse(PAGE, 303)


def _group(name: str) -> str:
    return " ".join(name.split())[:60]


@router.post(f"{PAGE}/{{monitor_id}}/group")
async def set_group(db: Db, monitor_id: int, group: Annotated[str, Form()] = "") -> Response:
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    monitor.group_name = _group(group)
    await db.commit()
    return RedirectResponse(PAGE, 303)


@router.post(f"{PAGE}/{{monitor_id}}/delete")
async def delete(db: Db, monitor_id: int) -> Response:
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    await db.delete(monitor)
    await db.commit()
    return RedirectResponse(PAGE, 303)
