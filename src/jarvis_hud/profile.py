"""The user's name, used in the greeting on the welcome screen."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.connections import store
from jarvis_hud.db import get_db

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

PROVIDER = "profile"
MAX_NAME = 40


async def user_name(request: Request, db: AsyncSession) -> str:
    data = await store.get_provider_settings(db, request.app.state.secret_box, PROVIDER)
    return (data or {}).get("name", "")


async def save_name(request: Request, db: AsyncSession, name: str) -> None:
    name = " ".join(name.split())[:MAX_NAME]
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, {"name": name})


@router.post("/settings/profile")
async def save_profile(request: Request, db: Db, name: Annotated[str, Form()] = "") -> Response:
    await save_name(request, db, name)
    return RedirectResponse("/settings/security?saved=true", status_code=303)
