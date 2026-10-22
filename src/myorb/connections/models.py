from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class ProviderSettings(Base):
    """Per-provider configuration, e.g. the user's own Google OAuth client. Encrypted."""

    __tablename__ = "provider_settings"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Connection(Base):
    """A connected account at a provider, e.g. one Google account."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("provider", "account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    account: Mapped[str] = mapped_column(String(255))
    # Encrypted JSON with tokens or keys; never sent to the browser.
    credentials: Mapped[bytes] = mapped_column(LargeBinary())
    scopes: Mapped[str] = mapped_column(String())
    # Result of the last connection test: one entry per capability.
    capabilities: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON())
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
