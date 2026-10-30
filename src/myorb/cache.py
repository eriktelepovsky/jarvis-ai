import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Set while the user asks for new data (the Refresh button): values are fetched now, not
# taken from the cache. Plugins that keep their own data can check it too (e.g. Uptime).
refreshing: ContextVar[bool] = ContextVar("refreshing", default=False)
# Set while a number's time to refresh has come (its border filled up): stale values are
# fetched now instead of in the background, but values still fresh are kept, so a source
# that must not be asked often (e.g. Bitbucket's rate limit) keeps its own pace.
due: ContextVar[bool] = ContextVar("due", default=False)


@dataclass(frozen=True)
class Cached:
    """A cached value, when it was fetched, and the error of the last refresh, if it failed."""

    value: Any
    fetched_at: float
    error: Exception | None = None


class Cache:
    """In-memory stale-while-revalidate cache for data from integrations.

    Fresh values are returned as they are. Stale values (older than `fresh`, younger than
    `max_age`) are returned immediately while a refresh runs in the background. Concurrent
    requests for the same key share one fetch, and failures are never cached.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        pace: Callable[[str], float | None] | None = None,
    ) -> None:
        self._clock = clock
        # The user's choice of how often a key's service is fetched (paces.Paces.for_key):
        # it replaces how long the plugin keeps the data fresh.
        self._pace = pace
        self._entries: dict[str, Cached] = {}
        # The error of a key's last fetch, while it keeps failing; also when nothing was ever
        # fetched for it, so the integrations page can tell the user (see failures).
        self._failed: dict[str, Exception] = {}
        self._fetches: dict[str, asyncio.Task] = {}

    async def get(
        self, key: str, fetch: Callable[[], Awaitable[Any]], fresh: float, max_age: float
    ) -> Cached:
        if self._pace is not None and (chosen := self._pace(key)):
            fresh, max_age = chosen, max(max_age, 4 * chosen)
        entry = self._entries.get(key)
        age = self._clock() - entry.fetched_at if entry else None
        if refreshing.get() or (due.get() and age is not None and age >= fresh):
            age = None if entry is None else max_age  # fetch now, keep the old value on failure
        if entry is not None and age is not None and age < fresh:
            return entry
        if entry is not None and age is not None and age < max_age:
            self._fetch(key, fetch)
            return entry
        try:
            return await asyncio.shield(self._fetch(key, fetch))
        except Exception as error:
            if entry is not None:
                # Too old to show without a warning, but better than nothing.
                return Cached(entry.value, entry.fetched_at, error)
            raise

    def _fetch(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> asyncio.Task:
        task = self._fetches.get(key)
        if task is None:
            task = asyncio.create_task(self._run(key, fetch))
            # Background refreshes may fail without anyone awaiting them; mark the error as seen.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._fetches[key] = task
        return task

    async def _run(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Cached:
        try:
            value = await fetch()
        except Exception as error:
            logger.warning("Refreshing %s failed", key, exc_info=True)
            self._failed[key] = error
            if previous := self._entries.get(key):
                # Keep showing the last good value, flagged with the error.
                self._entries[key] = Cached(previous.value, previous.fetched_at, error)
            raise
        finally:
            self._fetches.pop(key, None)
        self._failed.pop(key, None)
        entry = Cached(value, self._clock())
        self._entries[key] = entry
        return entry

    def invalidate(self, prefix: str) -> None:
        for key in [key for key in self._entries if key.startswith(prefix)]:
            del self._entries[key]
        for key in [key for key in self._failed if key.startswith(prefix)]:
            del self._failed[key]

    def failures(self, prefix: str) -> list[Exception]:
        """The errors of the keys under `prefix` whose last fetch failed."""
        return [error for key, error in self._failed.items() if key.startswith(prefix)]
