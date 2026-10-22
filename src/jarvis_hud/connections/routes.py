import hmac
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.auth.sessions import expected_origin, is_secure
from jarvis_hud.auth.tokens import sign, unsign
from jarvis_hud.connections import google, store
from jarvis_hud.connections.models import Connection
from jarvis_hud.db import get_db

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

CALLBACK_PATH = "/integrations/google/callback"
OAUTH_COOKIE = "jarvis_google_oauth"
OAUTH_PURPOSE = "google-oauth"
OAUTH_MAX_AGE = 10 * 60

# Outcomes passed in the query string after a redirect; only these fixed texts are shown.
MESSAGES = {
    "saved": "The OAuth client was saved. Now connect your Google account.",
    "connected": "The Google account was connected.",
    "tested": "The connection was tested.",
    "disconnected": "The Google account was disconnected.",
    "denied": "Access was not granted in Google.",
}


def redirect_uri(request: Request) -> str:
    return expected_origin(request) + CALLBACK_PATH


async def oauth_client(request: Request, db: AsyncSession) -> google.OAuthClient | None:
    data = await store.get_provider_settings(db, request.app.state.secret_box, google.PROVIDER)
    return google.OAuthClient(**data) if data else None


def _render(request: Request, template: str, context: dict[str, Any], status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


async def _integrations_page(
    request: Request,
    db: AsyncSession,
    message: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    context = {
        "client": await oauth_client(request, db),
        "connections": await store.list_connections(db, google.PROVIDER),
        "message": message,
        "error": error,
    }
    return _render(request, "integrations/index.html", context, status_code)


def forget_cached(request: Request, connection_id: int) -> None:
    """Drop cached tokens and data of a connection that changed or was removed."""
    request.app.state.google.forget(connection_id)
    request.app.state.cache.invalidate(f"google:{connection_id}:")


async def test_connection(request: Request, db: AsyncSession, connection: Connection) -> None:
    """Check that the account is still accessible and which capabilities work."""
    http = request.app.state.http
    account = {"id": "account", "label": "Access to the account"}
    client = await oauth_client(request, db)
    try:
        if client is None:
            raise google.GoogleError("The OAuth client is missing. Set it up again.")
        credentials = request.app.state.secret_box.decrypt(connection.credentials)
        access_token, _ = await google.refresh_access_token(
            http, client, credentials["refresh_token"]
        )
        granted = set(connection.scopes.split())
        capabilities = [
            {**account, "ok": True},
            *await google.check_capabilities(http, access_token, granted),
        ]
    except google.GoogleError as error:
        capabilities = [{**account, "ok": False, "detail": str(error)}]
    await store.save_check(db, connection, capabilities)


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request, db: Db, status: str | None = None) -> Response:
    return await _integrations_page(request, db, message=MESSAGES.get(status or ""))


@router.get("/integrations/google/setup", response_class=HTMLResponse)
async def google_setup_page(request: Request, db: Db) -> Response:
    context = {"client": await oauth_client(request, db), "redirect_uri": redirect_uri(request)}
    return _render(request, "integrations/google_setup.html", context)


@router.post("/integrations/google/setup")
async def google_setup(
    request: Request,
    db: Db,
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()] = "",
) -> Response:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    existing = await oauth_client(request, db)
    if not client_secret and existing and existing.client_id == client_id:
        client_secret = existing.client_secret
    error = None
    if not google.is_valid_client_id(client_id):
        error = "The client ID should end with .apps.googleusercontent.com."
    elif not client_secret:
        error = "Enter the client secret."
    if error:
        context = {
            "client": existing,
            "redirect_uri": redirect_uri(request),
            "client_id": client_id,
            "error": error,
        }
        return _render(request, "integrations/google_setup.html", context, status_code=400)
    await store.save_provider_settings(
        db,
        request.app.state.secret_box,
        google.PROVIDER,
        {"client_id": client_id, "client_secret": client_secret},
    )
    return RedirectResponse("/integrations?status=saved", status_code=303)


@router.post("/integrations/google/connect")
async def google_connect(request: Request, db: Db) -> Response:
    client = await oauth_client(request, db)
    if client is None:
        return RedirectResponse("/integrations/google/setup", status_code=303)
    state = secrets.token_urlsafe(32)
    verifier, challenge = google.pkce_pair()
    session_key = request.app.state.auth.session_key
    token = sign(session_key, OAUTH_PURPOSE, {"state": state, "verifier": verifier}, time.time())
    response = RedirectResponse(
        google.authorization_url(client, redirect_uri(request), state, challenge), status_code=303
    )
    response.set_cookie(
        OAUTH_COOKIE,
        token,
        max_age=OAUTH_MAX_AGE,
        path=CALLBACK_PATH,
        httponly=True,
        # Lax: the cookie must come along when Google redirects the browser back.
        samesite="lax",
        secure=is_secure(request),
    )
    return response


@router.get(CALLBACK_PATH, response_class=HTMLResponse)
async def google_callback(
    request: Request,
    db: Db,
    state: str = "",
    code: str | None = None,
    error: str | None = None,
) -> Response:
    cookie = request.cookies.get(OAUTH_COOKIE)
    session_key = request.app.state.auth.session_key
    flow = cookie and unsign(session_key, cookie, OAUTH_PURPOSE, OAUTH_MAX_AGE, time.time())
    if not flow or not hmac.compare_digest(state, flow["state"]):
        response = await _integrations_page(
            request,
            db,
            error="The sign-in with Google expired or did not start here. Try connecting again.",
            status_code=400,
        )
    elif error or not code:
        # Google's error parameter is not echoed into the page.
        response = RedirectResponse("/integrations?status=denied", status_code=303)
    else:
        response = await _finish_connect(request, db, code, flow["verifier"])
    response.delete_cookie(OAUTH_COOKIE, path=CALLBACK_PATH)
    return response


async def _finish_connect(request: Request, db: AsyncSession, code: str, verifier: str) -> Response:
    http = request.app.state.http
    client = await oauth_client(request, db)
    try:
        if client is None:
            raise google.GoogleError("The OAuth client is missing. Set it up again.")
        tokens = await google.exchange_code(http, client, code, redirect_uri(request), verifier)
        if "refresh_token" not in tokens:
            raise google.GoogleError("Google did not grant offline access. Try connecting again.")
        email = await google.fetch_email(http, tokens["access_token"])
    except google.GoogleError as error:
        return await _integrations_page(request, db, error=str(error), status_code=400)
    connection = await store.save_connection(
        db,
        request.app.state.secret_box,
        google.PROVIDER,
        email,
        {"refresh_token": tokens["refresh_token"]},
        tokens.get("scope", ""),
    )
    forget_cached(request, connection.id)
    await test_connection(request, db, connection)
    return RedirectResponse("/integrations?status=connected", status_code=303)


async def _connection_or_404(db: AsyncSession, connection_id: int) -> Connection:
    connection = await store.get_connection(db, google.PROVIDER, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


@router.post("/integrations/google/{connection_id}/test")
async def google_test(request: Request, db: Db, connection_id: int) -> Response:
    connection = await _connection_or_404(db, connection_id)
    await test_connection(request, db, connection)
    return RedirectResponse("/integrations?status=tested", status_code=303)


@router.post("/integrations/google/{connection_id}/disconnect")
async def google_disconnect(request: Request, db: Db, connection_id: int) -> Response:
    connection = await _connection_or_404(db, connection_id)
    credentials = request.app.state.secret_box.decrypt(connection.credentials)
    await google.revoke(request.app.state.http, credentials["refresh_token"])
    forget_cached(request, connection.id)
    await store.delete_connection(db, connection)
    return RedirectResponse("/integrations?status=disconnected", status_code=303)
