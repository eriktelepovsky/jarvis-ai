"""The user's name, used in the greeting, and the assistant's name and color, chosen by the user.

The assistant can be called anything ("J.A.R.V.I.S.", "Iris", "HAL 9000"): it is shown in the
middle of the orb, in the header and window titles, and Claude writes the brief as it. Its color
tints the orb on the welcome screen.
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

# The orb's colors: a label, the hue rotation from the design's cyan, and a swatch.
COLORS = {
    "cyan": ("Cyan", 0, "#4fd1e8"),
    "blue": ("Blue", 32, "#5b9cff"),
    "violet": ("Violet", 75, "#9b7bff"),
    "magenta": ("Magenta", 120, "#e26bf0"),
    "red": ("Red", 165, "#ff6b7d"),
    "amber": ("Amber", -150, "#f5b04a"),
    "green": ("Green", -55, "#5fe08f"),
}
DEFAULT_COLOR = "cyan"


def _clean(name: str) -> str:
    return " ".join(name.split())[:MAX_NAME]


async def load_profile(db: AsyncSession, box: SecretBox) -> dict[str, str]:
    data = await store.get_provider_settings(db, box, PROVIDER) or {}
    color = data.get("color") if data.get("color") in COLORS else DEFAULT_COLOR
    return {
        "name": data.get("name", ""),
        "assistant": data.get("assistant") or DEFAULT_ASSISTANT,
        "color": color,
    }


def orb_hue(color: str) -> int:
    return COLORS.get(color, COLORS[DEFAULT_COLOR])[1]


async def user_name(request: Request, db: AsyncSession) -> str:
    return (await load_profile(db, request.app.state.secret_box))["name"]


async def save_profile_values(
    request: Request,
    db: AsyncSession,
    name: str | None = None,
    assistant: str | None = None,
    color: str | None = None,
) -> None:
    """Save what is given (None keeps the current); the app shows the new name and color."""
    profile = await load_profile(db, request.app.state.secret_box)
    if name is not None:
        profile["name"] = _clean(name)
    if assistant is not None:
        profile["assistant"] = _clean(assistant) or DEFAULT_ASSISTANT
    if color in COLORS:
        profile["color"] = color
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, profile)
    request.app.state.assistant = profile["assistant"]
    request.app.state.orb_color = profile["color"]


@router.post("/settings/profile")
async def save_profile(
    request: Request,
    db: Db,
    name: Annotated[str, Form()] = "",
) -> Response:
    # Read directly: an emptied field means the default name, a missing one keeps the current.
    form = await request.form()
    assistant, color = form.get("assistant"), form.get("color")
    await save_profile_values(
        request,
        db,
        name=name,
        assistant=str(assistant) if assistant is not None else None,
        color=str(color) if color is not None else None,
    )
    return RedirectResponse("/settings/security?saved=true", status_code=303)
