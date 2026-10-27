from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    due_date: Mapped[date | None] = mapped_column(Date())
    due_time: Mapped[time | None] = mapped_column(Time())
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How it repeats (see repeat.py); done, it moves on to its next due date instead.
    repeat: Mapped[str | None] = mapped_column(String(40))
    # The due date before it last moved on, so marking it done can be undone.
    last_due: Mapped[date | None] = mapped_column(Date())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
