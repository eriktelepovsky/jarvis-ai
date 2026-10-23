from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


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


class ClaudeUsage(Base):
    """One Claude response: its tokens, and what they cost in USD when it arrived."""

    __tablename__ = "claude_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer())
    cache_write_5m: Mapped[int] = mapped_column(Integer())
    cache_write_1h: Mapped[int] = mapped_column(Integer())
    cache_read: Mapped[int] = mapped_column(Integer())
    output_tokens: Mapped[int] = mapped_column(Integer())
    # Priced on arrival, so a later price change does not rewrite what was spent.
    cost_usd: Mapped[float] = mapped_column(Float())
