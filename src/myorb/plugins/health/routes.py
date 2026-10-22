import hmac
import secrets
import time
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.auth.sessions import expected_origin, is_secure
from myorb.auth.tokens import sign, unsign
from myorb.connections import store
from myorb.connections.providers import ProviderError
from myorb.db import get_db
from myorb.plugins.health import strava
from myorb.plugins.health.models import HealthDay

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

PAGE = "/integrations/health"
CALLBACK = f"{PAGE}/strava/callback"
COOKIE = "myorb_strava_oauth"
PURPOSE = "strava-oauth"
APPLE = "apple_health"
MESSAGES = {
    "client": "The Strava application was saved. Now connect your Strava account.",
    "connected": "Strava is connected.",
    "denied": "Access was not granted in Strava.",
    "scope": "myOrb needs permission to read your activities. Connect again and allow it.",
    "removed": "Strava was disconnected.",
    "token": "A new webhook address was created. Update it in your Shortcut.",
}
# Problems passed in the query string; only these fixed texts are shown.
ERRORS = {
    "client": "Enter the Client ID (a number) and the Client Secret.",
    "expired": "The Strava sign-in expired or did not start here. Try again.",
    "exchange": "Strava did not accept the sign-in. Check the application's Client ID and Secret.",
}


async def strava_client(request: Request, db: AsyncSession) -> dict[str, str] | None:
    return await store.get_provider_settings(db, request.app.state.secret_box, strava.PROVIDER)


async def apple_token(request: Request, db: AsyncSession) -> str | None:
    data = await store.get_provider_settings(db, request.app.state.secret_box, APPLE)
    return data["token"] if data else None


def _page(request: Request, context: dict[str, Any], status_code: int = 200) -> Response:
    return request.app.state.templates.TemplateResponse(
        request, "health/settings.html", context, status_code=status_code
    )


@router.get(PAGE, response_class=HTMLResponse)
async def settings_page(
    request: Request, db: Db, status: str | None = None, error: str | None = None
) -> Response:
    token = await apple_token(request, db)
    last = await db.scalar(select(HealthDay).order_by(HealthDay.updated_at.desc()).limit(1))
    return _page(
        request,
        {
            "client": await strava_client(request, db),
            "connections": await store.list_connections(db, strava.PROVIDER),
            "callback_domain": expected_origin(request).split("://", 1)[1].split(":")[0],
            "webhook": f"{expected_origin(request)}/hooks/health/{token}" if token else None,
            "last": last,
            "message": MESSAGES.get(status or ""),
            "error": ERRORS.get(error or ""),
        },
    )


@router.post(f"{PAGE}/strava/client")
async def save_client(
    request: Request,
    db: Db,
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()] = "",
) -> Response:
    existing = await strava_client(request, db)
    secret = client_secret.strip() or (existing or {}).get("client_secret", "")
    if not client_id.strip().isdigit() or not secret:
        return RedirectResponse(f"{PAGE}?error=client", 303)
    await store.save_provider_settings(
        db,
        request.app.state.secret_box,
        strava.PROVIDER,
        {"client_id": client_id.strip(), "client_secret": secret},
    )
    return RedirectResponse(f"{PAGE}?status=client", 303)


@router.post(f"{PAGE}/strava/connect")
async def connect(request: Request, db: Db) -> Response:
    client = await strava_client(request, db)
    if client is None:
        return RedirectResponse(PAGE, 303)
    state = secrets.token_urlsafe(32)
    cookie = sign(request.app.state.auth.session_key, PURPOSE, {"state": state}, time.time())
    url = strava.authorization_url(client["client_id"], expected_origin(request) + CALLBACK, state)
    response = RedirectResponse(url, 303)
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=600,
        path=CALLBACK,
        httponly=True,
        samesite="lax",
        secure=is_secure(request),
    )
    return response


@router.get(CALLBACK)
async def callback(
    request: Request,
    db: Db,
    state: str = "",
    code: str | None = None,
    scope: str = "",
    error: str | None = None,
) -> Response:
    cookie = request.cookies.get(COOKIE)
    flow = cookie and unsign(request.app.state.auth.session_key, cookie, PURPOSE, 600, time.time())
    client = await strava_client(request, db)
    if not flow or not hmac.compare_digest(state, flow["state"]) or client is None:
        response = RedirectResponse(f"{PAGE}?error=expired", 303)
    elif error or not code:
        response = RedirectResponse(f"{PAGE}?status=denied", 303)
    elif "activity:read" not in scope:
        response = RedirectResponse(f"{PAGE}?status=scope", 303)
    else:
        try:
            tokens = await strava.exchange_code(request.app.state.http, client, code)
        except ProviderError:
            response = RedirectResponse(f"{PAGE}?error=exchange", 303)
        else:
            athlete = tokens.get("athlete") or {}
            name = (
                " ".join(p for p in (athlete.get("firstname"), athlete.get("lastname")) if p)
                or "Strava"
            )
            credentials = {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "expires_at": tokens.get("expires_at", 0),
            }
            connection = await store.save_connection(
                db, request.app.state.secret_box, strava.PROVIDER, name, credentials, scope
            )
            request.app.state.cache.invalidate(f"strava:{connection.id}:")
            response = RedirectResponse(f"{PAGE}?status=connected", 303)
    response.delete_cookie(COOKIE, path=CALLBACK)
    return response


@router.post(f"{PAGE}/strava/{{connection_id}}/disconnect")
async def disconnect(request: Request, db: Db, connection_id: int) -> Response:
    connection = await store.get_connection(db, strava.PROVIDER, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    credentials = request.app.state.secret_box.decrypt(connection.credentials)
    await strava.deauthorize(request.app.state.http, credentials["access_token"])
    await store.delete_connection(db, connection)
    request.app.state.cache.invalidate(f"strava:{connection_id}:")
    return RedirectResponse(f"{PAGE}?status=removed", 303)


@router.post(f"{PAGE}/apple/token")
async def new_token(request: Request, db: Db) -> Response:
    await store.save_provider_settings(
        db, request.app.state.secret_box, APPLE, {"token": secrets.token_urlsafe(32)}
    )
    return RedirectResponse(f"{PAGE}?status=token", 303)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and value >= 0 else None


def parse_days(body: Any) -> list[dict[str, Any]]:
    """Days from the webhook body: one object, a list, or {"days": [...]}. Raises ValueError."""
    items = body.get("days", [body]) if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise ValueError("Send an object with a date, or a list of them.")
    days = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each day must be an object.")
        try:
            day = date.fromisoformat(str(item.get("date", ""))[:10])
        except ValueError as error:
            raise ValueError("Each day needs a date as YYYY-MM-DD.") from error
        sleep = _number(item.get("sleep_minutes"))
        if sleep is None and (hours := _number(item.get("sleep_hours"))) is not None:
            sleep = hours * 60
        steps = _number(item.get("steps"))
        days.append(
            {
                "day": day,
                "steps": round(steps) if steps is not None else None,
                "sleep_minutes": round(sleep) if sleep is not None else None,
                "resting_heart_rate": _number(item.get("resting_heart_rate")),
                "active_energy": _number(item.get("active_energy")),
            }
        )
    return days


@router.post("/hooks/health/{token}")
async def webhook(request: Request, db: Db, token: str) -> Response:
    expected = await apple_token(request, db)
    if not expected or not hmac.compare_digest(token, expected):
        return JSONResponse({"ok": False, "error": "Unknown webhook address."}, status_code=404)
    try:
        days = parse_days(await request.json())
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    for values in days:
        row = await db.get(HealthDay, values["day"]) or HealthDay(day=values["day"])
        for key, value in values.items():
            # Fields the iPhone did not send keep their earlier value.
            if key != "day" and value is not None:
                setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        db.add(row)
    await db.commit()
    request.app.state.cache.invalidate("health:")
    return JSONResponse({"ok": True, "days": len(days)})
