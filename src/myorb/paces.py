"""How often each service's data is fetched, when the user chose it on Integrations.

A choice applies both to the cache (how long a service's data counts as fresh) and to the
welcome screen (when a number's border fills up and it is fetched again). Services without a
choice keep the pace their plugin set. Custom sources choose theirs per source instead.
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.connections import store
from myorb.crypto import SecretBox

if TYPE_CHECKING:
    from myorb.plugins import Plugin, Widget

SETTINGS = "paces"
GOOGLE = "google"
# The Google plugins share one choice; it covers their data, not e.g. the list of YouTube
# subscriptions, which is read twice a day for the API's quota.
GOOGLE_PLUGINS = ("gmail", "calendar", "youtube")
GOOGLE_DATA = ("gmail", "calendar", "youtube-videos")
# What can be chosen, in seconds.
OPTIONS = (60, 2 * 60, 3 * 60, 5 * 60, 10 * 60, 15 * 60, 30 * 60, 60 * 60)
# Services that cannot choose (they are paced per source).
FIXED = ("custom",)


class Paces:
    def __init__(self) -> None:
        self._chosen: dict[str, float] = {}

    async def load(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        async with sessionmaker() as db:
            data = await store.get_provider_settings(db, box, SETTINGS) or {}
        self._chosen = {k: float(v) for k, v in data.items() if v in OPTIONS}

    async def choose(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        box: SecretBox,
        service: str,
        seconds: float | None,
    ) -> None:
        """Set a service's pace, or go back to its default (None)."""
        if seconds is None:
            self._chosen.pop(service, None)
        else:
            self._chosen[service] = seconds
        async with sessionmaker() as db:
            await store.save_provider_settings(db, box, SETTINGS, dict(self._chosen))

    def chosen(self, service: str) -> float | None:
        return self._chosen.get(service)

    def for_key(self, key: str) -> float | None:
        """The chosen pace for a cache key ("jira:3:issues", "google:1:gmail:2026-10-23")."""
        parts = key.split(":")
        if parts[0] == GOOGLE:
            return (
                self._chosen.get(GOOGLE)
                if len(parts) > 2 and parts[2].startswith(GOOGLE_DATA)
                else None
            )
        return self._chosen.get(parts[0])

    def services(self, plugin: "Plugin") -> list[str]:
        if plugin.id in GOOGLE_PLUGINS:
            return [GOOGLE]
        return [p.id for p in plugin.connections() if p.id not in FIXED]

    def widget_refresh(self, plugin: "Plugin", widget: "Widget") -> float:
        """How often a number is fetched again: the shortest pace of its services, chosen or
        their own, else what the widget says."""
        if plugin.id in GOOGLE_PLUGINS:
            return self._chosen.get(GOOGLE) or widget.refresh
        paces = [
            self._chosen.get(p.id) or p.refresh or widget.refresh
            for p in plugin.connections()
            if p.id not in FIXED
        ]
        return min(paces) if paces else widget.refresh
