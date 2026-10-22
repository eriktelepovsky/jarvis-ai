import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.auth import store
from jarvis_hud.auth.gate import safe_next
from jarvis_hud.auth.passwords import MIN_PASSWORD_LENGTH, password_problem
from jarvis_hud.auth.sessions import client_id, end_session, is_loopback, start_session
from jarvis_hud.db import get_db
from jarvis_hud.profile import load_profile, save_profile_values, user_name

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


def _render(request: Request, template: str, context: dict, status_code: int = 200) -> Response:
    context = {"min_password_length": MIN_PASSWORD_LENGTH, **context}
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


def _setup_allowed(request: Request, token: str | None) -> bool:
    """Setup is allowed from this machine, or anywhere with the token from the server log."""
    expected = request.app.state.auth.setup_token
    return is_loopback(request) or (token is not None and hmac.compare_digest(token, expected))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, token: str | None = None) -> Response:
    if request.app.state.auth.password_set:
        return RedirectResponse("/", status_code=303)
    allowed = _setup_allowed(request, token)
    return _render(request, "auth/setup.html", {"allowed": allowed, "token": token or ""})


@router.post("/setup")
async def setup(
    request: Request,
    db: Db,
    password: Annotated[str, Form()],
    password_confirmation: Annotated[str, Form()],
    token: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    assistant: Annotated[str, Form()] = "",
) -> Response:
    state = request.app.state.auth
    if state.password_set:
        return RedirectResponse("/", status_code=303)
    client, now = client_id(request), time.time()
    if state.throttle.is_blocked(client, now):
        return _render(request, "auth/setup.html", {"allowed": False, "token": ""}, status_code=429)
    if not _setup_allowed(request, token or None):
        state.throttle.record_failure(client, now)
        return _render(request, "auth/setup.html", {"allowed": False, "token": ""}, status_code=403)
    if problem := password_problem(password, password_confirmation):
        context = {"allowed": True, "token": token, "error": problem}
        return _render(request, "auth/setup.html", context, status_code=400)
    await store.set_password(db, state, password)
    if name.strip() or assistant.strip():
        await save_profile_values(request, db, name=name, assistant=assistant)
    response = RedirectResponse("/", status_code=303)
    start_session(response, request, state)
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Db, next: str | None = None) -> Response:
    if request.state.authenticated:
        return RedirectResponse(safe_next(next), status_code=303)
    has_passkeys = bool(await store.list_passkeys(db))
    return _render(
        request, "auth/login.html", {"next": safe_next(next), "has_passkeys": has_passkeys}
    )


@router.post("/login")
async def login(
    request: Request,
    db: Db,
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
) -> Response:
    state = request.app.state.auth
    client, now = client_id(request), time.time()
    context = {"next": safe_next(next), "has_passkeys": bool(await store.list_passkeys(db))}
    if state.throttle.is_blocked(client, now):
        context["error"] = "Too many failed attempts. Try again in a few minutes."
        return _render(request, "auth/login.html", context, status_code=429)
    if not await store.check_password(state, password):
        state.throttle.record_failure(client, now)
        context["error"] = "Incorrect password."
        return _render(request, "auth/login.html", context, status_code=401)
    state.throttle.reset(client)
    response = RedirectResponse(safe_next(next), status_code=303)
    start_session(response, request, state)
    return response


@router.post("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    end_session(response)
    return response


@router.get("/settings/security", response_class=HTMLResponse)
async def security_page(
    request: Request, db: Db, changed: bool = False, saved: bool = False
) -> Response:
    context = {
        "passkeys": await store.list_passkeys(db),
        "changed": changed,
        "saved": saved,
        "name": await user_name(request, db),
        "assistant_name": (await load_profile(db, request.app.state.secret_box))["assistant"],
    }
    return _render(request, "auth/security.html", context)


@router.post("/settings/security/password")
async def change_password(
    request: Request,
    db: Db,
    current_password: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirmation: Annotated[str, Form()],
) -> Response:
    state = request.app.state.auth
    error = None
    if not await store.check_password(state, current_password):
        error = "The current password is incorrect."
    else:
        error = password_problem(password, password_confirmation)
    if error:
        context = {"passkeys": await store.list_passkeys(db), "error": error}
        return _render(request, "auth/security.html", context, status_code=400)
    await store.set_password(db, state, password)
    # The session key was rotated: every other device is signed out, this one gets a new cookie.
    response = RedirectResponse("/settings/security?changed=true", status_code=303)
    start_session(response, request, state)
    return response


@router.post("/settings/security/passkeys/{passkey_id}/delete")
async def delete_passkey(db: Db, passkey_id: int) -> Response:
    await store.delete_passkey(db, passkey_id)
    return RedirectResponse("/settings/security", status_code=303)
