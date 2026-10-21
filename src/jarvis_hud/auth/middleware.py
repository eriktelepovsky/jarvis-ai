import logging
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from jarvis_hud.auth.gate import AccessRequest, Decision, decide
from jarvis_hud.auth.sessions import expected_origin, is_authenticated

logger = logging.getLogger(__name__)


def _redirect(request: Request, url: str) -> Response:
    if request.headers.get("HX-Request"):
        # HTMX would swap the redirected page into a widget; ask it to navigate instead.
        return Response(status_code=401, headers={"HX-Redirect": url})
    if request.method != "GET":
        return PlainTextResponse("Authentication required.", status_code=401)
    return RedirectResponse(url, status_code=303)


async def auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    try:
        state = request.app.state.auth
        authenticated = state.password_set and is_authenticated(request, state)
        decision = decide(
            AccessRequest(
                path=request.url.path,
                method=request.method,
                origin=request.headers.get("Origin"),
                expected_origin=expected_origin(request),
                password_set=state.password_set,
                authenticated=authenticated,
            )
        )
    except Exception:
        # Fail closed: if access cannot be decided, nothing is served.
        logger.exception("Access check failed")
        return PlainTextResponse("Jarvis is temporarily unavailable.", status_code=503)

    request.state.authenticated = authenticated
    match decision:
        case Decision.ALLOW:
            return await call_next(request)
        case Decision.SETUP:
            return _redirect(request, "/setup")
        case Decision.LOGIN:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return _redirect(request, f"/login?next={quote(target)}")
        case Decision.REJECT_ORIGIN:
            return PlainTextResponse("Cross-site request rejected.", status_code=403)
