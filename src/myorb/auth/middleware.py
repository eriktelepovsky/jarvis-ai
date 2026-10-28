import logging
from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlsplit

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from myorb.auth.gate import AccessRequest, Decision, decide
from myorb.auth.sessions import expected_origin, is_authenticated

logger = logging.getLogger(__name__)

GITHUB = "https://github.com/eriktelepovsky/myorb"


def _redirect(request: Request, url: str) -> Response:
    if request.headers.get("HX-Request"):
        # HTMX would swap the redirected page into a widget; ask it to navigate instead.
        return Response(status_code=401, headers={"HX-Redirect": url})
    if request.method not in ("GET", "HEAD"):
        return PlainTextResponse("Authentication required.", status_code=401)
    return RedirectResponse(url, status_code=303)


def _return_to(request: Request) -> str:
    """Where to go after signing in: the page the user is on.

    For a part of a page that HTMX loads (a panel refreshing every minute, a widget), that
    is the page in the address bar (HX-Current-URL), not the part itself, which would show
    as a bare fragment after signing in. A boosted link or form is a page of its own.
    """
    url = request.url
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        current = urlsplit(request.headers.get("HX-Current-URL", ""))
        if not current.path:
            return "/"
        return current.path + (f"?{current.query}" if current.query else "")
    return url.path + (f"?{url.query}" if url.query else "")


async def auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    try:
        state = request.app.state.auth
        public_demo = request.app.state.settings.public_demo
        authenticated = public_demo or (state.password_set and is_authenticated(request, state))
        decision = decide(
            AccessRequest(
                path=request.url.path,
                method=request.method,
                origin=request.headers.get("Origin"),
                expected_origin=expected_origin(request),
                password_set=state.password_set,
                authenticated=authenticated,
                public_demo=public_demo,
            )
        )
    except Exception:
        # Fail closed: if access cannot be decided, nothing is served.
        logger.exception("Access check failed")
        return PlainTextResponse("myOrb is temporarily unavailable.", status_code=503)

    request.state.authenticated = authenticated
    match decision:
        case Decision.ALLOW:
            return await call_next(request)
        case Decision.SETUP:
            return _redirect(request, "/setup")
        case Decision.LOGIN:
            return _redirect(request, f"/login?next={quote(_return_to(request))}")
        case Decision.REJECT_ORIGIN:
            return PlainTextResponse("Cross-site request rejected.", status_code=403)
        case Decision.HOME:
            return _redirect(request, "/")
        case Decision.READ_ONLY:
            # app.js shows the demo's note instead of this page (X-Myorb-Demo).
            return PlainTextResponse(
                f"This is the public demo, so nothing can be changed here. "
                f"To connect your own services, run myOrb yourself: {GITHUB}",
                status_code=403,
                headers={"X-Myorb-Demo": "read-only"},
            )
