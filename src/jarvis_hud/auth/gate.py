from dataclasses import dataclass
from enum import Enum

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_PATHS = frozenset({"/login", "/login/passkey/options", "/login/passkey/verify"})
SETUP_PATH = "/setup"


class Decision(Enum):
    ALLOW = "allow"
    SETUP = "setup"  # no password yet: send to the setup page
    LOGIN = "login"  # not signed in: send to the login page
    REJECT_ORIGIN = "reject_origin"  # cross-site state-changing request (CSRF)


@dataclass(frozen=True)
class AccessRequest:
    path: str
    method: str
    origin: str | None
    expected_origin: str
    password_set: bool
    authenticated: bool


def decide(request: AccessRequest) -> Decision:
    """Decide whether a request may reach the application."""
    if request.path.startswith("/static/"):
        return Decision.ALLOW
    if request.method not in SAFE_METHODS and request.origin != request.expected_origin:
        return Decision.REJECT_ORIGIN
    if not request.password_set:
        return Decision.ALLOW if request.path == SETUP_PATH else Decision.SETUP
    if request.authenticated or request.path in PUBLIC_PATHS:
        return Decision.ALLOW
    return Decision.LOGIN


def safe_next(target: str | None) -> str:
    """Return a local path to redirect to after login, rejecting redirects to other sites."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    return target
