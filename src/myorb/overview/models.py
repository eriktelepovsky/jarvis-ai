from datetime import date

from sqlalchemy import Date, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class WidgetSnapshot(Base):
    """A widget's number on a day (the last one seen), to show the change since yesterday."""

    __tablename__ = "widget_snapshots"

    day: Mapped[date] = mapped_column(Date(), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[float] = mapped_column(Float())
