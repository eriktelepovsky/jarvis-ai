from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class ActivityEvent(Base):
    """Something that happened, for the activity list on the welcome screen."""

    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # "ok", "fail", "warn" or "info": the mark in front of it.
    tone: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500))


class WidgetState(Base):
    """A widget's number and whether it worked at its last load, to note what changed since.

    Kept across restarts, so a change while the server was down is noted too.
    """

    __tablename__ = "widget_states"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[float | None] = mapped_column(Float())
    failing: Mapped[bool] = mapped_column(Boolean(), default=False)
