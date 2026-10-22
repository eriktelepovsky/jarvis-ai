import secrets
from dataclasses import dataclass, field

from myorb.auth.throttle import LoginThrottle


@dataclass
class AuthState:
    """In-memory copy of the authentication settings, kept in sync with the database."""

    password_hash: str | None = None
    session_key: bytes | None = None
    # One-time secret that allows finishing setup from a non-loopback address.
    setup_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    throttle: LoginThrottle = field(default_factory=LoginThrottle)

    @property
    def password_set(self) -> bool:
        return self.password_hash is not None and self.session_key is not None
