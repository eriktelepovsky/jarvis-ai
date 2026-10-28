from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.connections import store
from myorb.crypto import SecretBox
from myorb.db import get_db
from myorb.plugins.work import PAGE, PROVIDER

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


async def load_merged(db: AsyncSession, box: SecretBox) -> bool:
    settings = await store.get_provider_settings(db, box, PROVIDER)
    return bool((settings or {}).get("merged"))


@router.get(PAGE, response_class=HTMLResponse)
async def page(request: Request) -> Response:
    work = request.app.state.plugins["work"]
    return request.app.state.templates.TemplateResponse(
        request,
        "work/settings.html",
        {"merged": work.merged, "names": [plugin.name for plugin, _ in work.parts]},
    )


@router.post(PAGE)
async def save(request: Request, db: Db, merged: Annotated[str, Form()] = "") -> Response:
    work = request.app.state.plugins["work"]
    work.merged = merged == "on"
    await store.save_provider_settings(
        db, request.app.state.secret_box, PROVIDER, {"merged": work.merged}
    )
    return RedirectResponse(PAGE + "?saved=1", status_code=303)
