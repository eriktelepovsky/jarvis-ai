from datetime import datetime

from sqlalchemy import DateTime, Integer, String
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
