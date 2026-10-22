from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from jarvis_hud.db import Base


class HealthDay(Base):
    """One day of Apple Health data, as the iPhone sends it to the webhook."""

    __tablename__ = "health_days"

    day: Mapped[date] = mapped_column(Date(), primary_key=True)
    steps: Mapped[int | None] = mapped_column(Integer())
    # Sleep of the night before this day, in minutes.
    sleep_minutes: Mapped[int | None] = mapped_column(Integer())
    resting_heart_rate: Mapped[float | None] = mapped_column(Float())
    active_energy: Mapped[float | None] = mapped_column(Float())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
