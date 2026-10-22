from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class Monitor(Base):
    """A website or service myOrb checks every minute, with the result of the last check."""

    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    up: Mapped[bool | None] = mapped_column(Boolean())
    status_code: Mapped[int | None] = mapped_column(Integer())
    response_ms: Mapped[int | None] = mapped_column(Integer())
    error: Mapped[str | None] = mapped_column(String(300))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tls_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Incident(Base):
    """A period when a monitor was down."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))
