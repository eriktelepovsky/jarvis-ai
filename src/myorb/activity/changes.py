"""Activity from the widgets themselves: a source that stops or starts working, new items."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.activity.store import record
from myorb.overview.snapshots import numeric


class WidgetWatch:
    """Remembers each widget's last number and state, to note what changed since.

    Kept in memory: after a restart the first load only remembers, so nothing is noted twice.
    """

    def __init__(self) -> None:
        self._last: dict[str, tuple[float | None, bool]] = {}

    async def note(
        self, sessionmaker: async_sessionmaker[AsyncSession], items: list[dict[str, Any]]
    ) -> None:
        for item in items:
            data, widget = item["data"], item["widget"]
            value = numeric(data.value)
            failing = data.message_tone == "fail" or (data.value is None and bool(data.message))
            previous = self._last.get(item["key"])
            self._last[item["key"]] = (value, failing)
            if previous is None:
                continue
            last_value, was_failing = previous
            label = data.label or widget.label
            if failing and not was_failing:
                await record(sessionmaker, f"{widget.title}: {data.message}", "fail")
            elif was_failing and not failing:
                await record(sessionmaker, f"{widget.title} works again", "ok")
            elif value is not None and last_value is not None and value > last_value:
                new = f"{value - last_value:g}"
                await record(sessionmaker, f"{widget.title} · {new} new · {label.lower()}", "info")
