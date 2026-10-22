from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from jarvis_hud.db import Base


class AuthSettings(Base):
    """The owner's password. Jarvis has a single user, so this table has at most one row."""

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    password_hash: Mapped[str] = mapped_column(String())
    # Signs session cookies; replaced on every password change to sign out all devices.
    session_key: Mapped[bytes] = mapped_column(LargeBinary())
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Passkey(Base):
    __tablename__ = "passkeys"

    id: Mapped[int] = mapped_column(primary_key=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary(), unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary())
    sign_count: Mapped[int]
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
