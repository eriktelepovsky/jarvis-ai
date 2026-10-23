"""The activity log: what happened lately, e.g. a website went down or the brief was written."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.activity.models import ActivityEvent

KEEP = timedelta(days=7)
TONES = {"ok", "fail", "warn", "info"}


async def record(
    sessionmaker: async_sessionmaker[AsyncSession],
    text: str,
    tone: str = "info",
    url: str | None = None,
) -> None:
    """Add an event; plugins call it too. Older events than a week are dropped."""
    now = datetime.now(UTC)
    async with sessionmaker() as db:
        db.add(
            ActivityEvent(
                at=now,
                tone=tone if tone in TONES else "info",
                text=" ".join(text.split())[:300],
                url=url,
            )
        )
        await db.execute(delete(ActivityEvent).where(ActivityEvent.at < now - KEEP))
        await db.commit()


def event(text: str, tone: str = "info", url: str | None = None) -> ActivityEvent:
    """An event to add within a session that is already writing, e.g. the uptime checks."""
    return ActivityEvent(
        at=datetime.now(UTC),
        tone=tone if tone in TONES else "info",
        text=" ".join(text.split())[:300],
        url=url,
    )


async def recent(db: AsyncSession, since: datetime | None, limit: int = 40) -> list[ActivityEvent]:
    """The newest events first: those since a time, or the latest `limit` of the log."""
    query = select(ActivityEvent).order_by(ActivityEvent.at.desc(), ActivityEvent.id.desc())
    if since is not None:
        # Stored in UTC without the zone (SQLite): compare in UTC.
        query = query.where(ActivityEvent.at >= since.astimezone(UTC))
    return list(await db.scalars(query.limit(limit)))
