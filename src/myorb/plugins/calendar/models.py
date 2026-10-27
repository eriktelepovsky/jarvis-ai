from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class IgnoredEvent(Base):
    """An event the user ignores (a repeating one with all its days), e.g. someone else's in a
    shared calendar: shown grey, and left out of the day's numbers, free time and the brief."""

    __tablename__ = "ignored_events"

    # The calendar and the event (or the series it repeats in); see event_key.
    key: Mapped[str] = mapped_column(String(1100), primary_key=True)
    ignored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
