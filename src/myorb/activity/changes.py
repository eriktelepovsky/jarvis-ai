"""Activity from the widgets themselves: a source that stops or starts working, new items."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.activity.models import WidgetState
from myorb.activity.store import event
from myorb.overview.snapshots import numeric


class WidgetWatch:
    """Compares each widget with its last load, kept in the database, and notes what changed.

    A widget seen for the first time is only remembered, so nothing is noted for it.
    """

    async def note(
        self, sessionmaker: async_sessionmaker[AsyncSession], items: list[dict[str, Any]]
    ) -> None:
        async with sessionmaker() as db:
            for item in items:
                data, widget = item["data"], item["widget"]
                value = numeric(data.value)
                failing = data.message_tone == "fail" or (data.value is None and bool(data.message))
                state = await db.get(WidgetState, item["key"])
                if state is None:
                    db.add(WidgetState(key=item["key"], value=value, failing=failing))
                    continue
                label = data.label or widget.label
                if failing and not state.failing:
                    db.add(event(f"{widget.title}: {data.message}", "fail"))
                elif state.failing and not failing:
                    db.add(event(f"{widget.title} works again", "ok"))
                elif value is not None and state.value is not None and value > state.value:
                    new = f"{value - state.value:g}"
                    db.add(event(f"{widget.title} · {new} new · {label.lower()}", "info"))
                state.value, state.failing = value, failing
            await db.commit()
