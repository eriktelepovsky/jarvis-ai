"""The user's name, used in the greeting, and the assistant's name, chosen by the user.

The assistant can be called anything ("J.A.R.V.I.S.", "Iris", "HAL 9000"): it is shown in the
middle of the orb, in the header and window titles, and Claude writes the brief as it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.connections import store
from myorb.crypto import SecretBox
from myorb.db import get_db

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

PROVIDER = "profile"
MAX_NAME = 40
DEFAULT_ASSISTANT = "Orb"


def _clean(name: str) -> str:
    return " ".join(name.split())[:MAX_NAME]


async def load_profile(db: AsyncSession, box: SecretBox) -> dict[str, str]:
    data = await store.get_provider_settings(db, box, PROVIDER) or {}
    return {"name": data.get("name", ""), "assistant": data.get("assistant") or DEFAULT_ASSISTANT}


async def user_name(request: Request, db: AsyncSession) -> str:
    return (await load_profile(db, request.app.state.secret_box))["name"]


async def save_profile_values(
    request: Request, db: AsyncSession, name: str | None = None, assistant: str | None = None
) -> None:
    """Save the names given (None keeps the current one); the app shows the new assistant name."""
    profile = await load_profile(db, request.app.state.secret_box)
    if name is not None:
        profile["name"] = _clean(name)
    if assistant is not None:
        profile["assistant"] = _clean(assistant) or DEFAULT_ASSISTANT
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, profile)
    request.app.state.assistant = profile["assistant"]


@router.post("/settings/profile")
async def save_profile(
    request: Request,
    db: Db,
    name: Annotated[str, Form()] = "",
) -> Response:
    # Read directly: an emptied field means the default name, a missing one keeps the current.
    assistant = (await request.form()).get("assistant")
    await save_profile_values(
        request, db, name=name, assistant=str(assistant) if assistant is not None else None
    )
    return RedirectResponse("/settings/security?saved=true", status_code=303)
