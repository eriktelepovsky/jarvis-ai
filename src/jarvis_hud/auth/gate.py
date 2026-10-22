from dataclasses import dataclass
from enum import Enum

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_PATHS = frozenset({"/login", "/login/passkey/options", "/login/passkey/verify"})
SETUP_PATH = "/setup"
# Open to everyone, also before setup: the liveness check for Docker and uptime monitors, and
# the files that make Jarvis installable as an app. None of them reveals any data.
ALWAYS_PUBLIC = frozenset({"/healthz", "/manifest.webmanifest", "/sw.js"})
# Webhooks of plugins: called by other services and devices without a session or an Origin
# header, and authenticated by the plugin itself with a secret token.
HOOKS_PREFIX = "/hooks/"


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
    if request.path.startswith("/static/") or request.path in ALWAYS_PUBLIC:
        return Decision.ALLOW
    if request.path.startswith(HOOKS_PREFIX):
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
