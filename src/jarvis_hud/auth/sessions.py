import ipaddress
import time
from urllib.parse import urlsplit

from fastapi import Request, Response

from jarvis_hud.auth.state import AuthState
from jarvis_hud.auth.tokens import sign, unsign

SESSION_COOKIE = "jarvis_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
SESSION_PURPOSE = "session"


def expected_origin(request: Request) -> str:
    """The origin Jarvis is served from: JARVIS_PUBLIC_URL, or the URL of the request."""
    public_url = request.app.state.settings.public_url
    if public_url:
        parts = urlsplit(public_url)
        return f"{parts.scheme}://{parts.netloc}"
    return f"{request.url.scheme}://{request.url.netloc}"


def relying_party_id(request: Request) -> str:
    """Passkeys are bound to the host name Jarvis is served from."""
    return urlsplit(expected_origin(request)).hostname or ""


def is_secure(request: Request) -> bool:
    return expected_origin(request).startswith("https://")


def is_loopback(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_authenticated(request: Request, state: AuthState) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or state.session_key is None:
        return False
    data = unsign(state.session_key, token, SESSION_PURPOSE, SESSION_MAX_AGE, time.time())
    return data is not None


def start_session(response: Response, request: Request, state: AuthState) -> None:
    assert state.session_key is not None
    token = sign(state.session_key, SESSION_PURPOSE, {}, time.time())
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=is_secure(request),
    )


def end_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
