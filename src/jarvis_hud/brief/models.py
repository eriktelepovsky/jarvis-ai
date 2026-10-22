from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from jarvis_hud.db import Base


class Brief(Base):
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # "morning", "midday" or "evening", from the time it was written.
    kind: Mapped[str] = mapped_column(String(20))
    # {"headline": str, "summary": [str], "priorities": [{tag, tone, title, detail}]}
    content: Mapped[dict[str, Any]] = mapped_column(JSON())
    sources: Mapped[list[str]] = mapped_column(JSON())
    model: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(10))
