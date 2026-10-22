"""The widgets' numbers per day, so the report can show what changed since yesterday."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.overview.models import WidgetSnapshot


async def record(db: AsyncSession, day: date, values: dict[str, float]) -> None:
    """Keep today's numbers; a later load of the same day replaces them."""
    for key, value in values.items():
        statement = insert(WidgetSnapshot).values(day=day, key=key, value=value)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["day", "key"], set_={"value": statement.excluded.value}
            )
        )
    await db.commit()


async def changes(db: AsyncSession, day: date, values: dict[str, float]) -> dict[str, float]:
    """How each number differs from yesterday's; numbers yesterday did not have are left out."""
    rows = await db.scalars(
        select(WidgetSnapshot).where(WidgetSnapshot.day == day - timedelta(days=1))
    )
    yesterday = {row.key: row.value for row in rows}
    return {key: value - yesterday[key] for key, value in values.items() if key in yesterday}


def numeric(value: object) -> float | None:
    """A widget's value as a number, or None for text like "7h12" or no data."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def change_label(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "±0"
    number = f"{abs(delta):g}"
    return f"▲ +{number}" if delta > 0 else f"▼ −{number}"
