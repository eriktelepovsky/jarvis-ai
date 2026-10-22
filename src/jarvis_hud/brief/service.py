import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as clock

import anthropic
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_hud.brief.models import Brief
from jarvis_hud.brief.writer import DEFAULT_MODEL, BriefError, api_error, write_brief
from jarvis_hud.connections import store
from jarvis_hud.crypto import SecretBox
from jarvis_hud.plugins import BriefSection, Plugin, WidgetContext

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"
DEFAULT_TIME = "06:30"
# After a failed attempt, the scheduler waits this long before trying again.
RETRY_AFTER = 30 * 60
KICKERS = {"morning": "Morning report", "midday": "Midday update", "evening": "Evening wrap-up"}


def kind_for(hour: int) -> str:
    if 4 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "midday"
    return "evening"


@dataclass(frozen=True)
class ClaudeSettings:
    api_key: str
    language: str = "en"
    brief_time: str = DEFAULT_TIME
    model: str = DEFAULT_MODEL
    # Needed for API keys that are not scoped to a workspace (organization-wide keys).
    workspace_id: str = ""

    @property
    def brief_clock(self) -> clock:
        return clock.fromisoformat(self.brief_time)


class BriefService:
    """Writes the brief on schedule or on request, one at a time, and remembers the last error."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        box: SecretBox,
        plugins: Callable[[], list[Plugin]],
        context: Callable[[], WidgetContext],
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._box = box
        self._plugins = plugins
        self._context = context
        self._transport = transport
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_attempt: float | None = None
        self.error: str | None = None

    @property
    def generating(self) -> bool:
        return self._lock.locked()

    def client(self, api_key: str, workspace_id: str = "") -> anthropic.AsyncAnthropic:
        http_client = (
            anthropic.DefaultAsyncHttpxClient(transport=self._transport)
            if self._transport is not None
            else None
        )
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        return anthropic.AsyncAnthropic(
            api_key=api_key, http_client=http_client, default_headers=headers
        )

    async def settings(self) -> ClaudeSettings | None:
        async with self._sessionmaker() as db:
            data = await store.get_provider_settings(db, self._box, PROVIDER)
        return ClaudeSettings(**data) if data else None

    async def save_settings(self, settings: ClaudeSettings) -> None:
        async with self._sessionmaker() as db:
            await store.save_provider_settings(db, self._box, PROVIDER, settings.__dict__)
        self.error = None

    async def remove_settings(self) -> None:
        async with self._sessionmaker() as db:
            await store.delete_provider_settings(db, PROVIDER)
        self.error = None

    async def check_key(
        self, api_key: str, model: str = DEFAULT_MODEL, workspace_id: str = ""
    ) -> str | None:
        """Return why the API key does not work, or None if it does. Costs no tokens."""
        try:
            await self.client(api_key, workspace_id).models.retrieve(model)
        except anthropic.AuthenticationError:
            return "Claude rejected this API key."
        except anthropic.PermissionDeniedError:
            return "This API key has no access to the chosen model."
        except anthropic.APIStatusError as error:
            logger.warning("Checking the Claude API key failed: %s", error)
            return api_error(error)
        except anthropic.APIConnectionError:
            return "Claude could not be reached."
        return None

    async def latest(self) -> Brief | None:
        async with self._sessionmaker() as db:
            return await db.scalar(select(Brief).order_by(Brief.created_at.desc()).limit(1))

    async def _sections(self, context: WidgetContext) -> list[BriefSection]:
        async def collect(plugin: Plugin) -> BriefSection | None:
            try:
                return await plugin.brief(context)
            except Exception:
                # A failing source is left out; the brief is written from the others.
                logger.exception("Brief facts from %s failed", plugin.id)
                return None

        results = await asyncio.gather(*(collect(p) for p in self._plugins()))
        return [section for section in results if section and section.lines]

    async def generate(self) -> Brief:
        """Write a new brief now. Raises BriefError with a message for the user."""
        async with self._lock:
            self._last_attempt = self._clock()
            settings = await self.settings()
            if settings is None:
                raise BriefError("Connect Claude in Integrations first.")
            context = self._context()
            sections = await self._sections(context)
            try:
                written = await write_brief(
                    self.client(settings.api_key, settings.workspace_id),
                    sections,
                    context.now,
                    settings.language,
                    settings.model,
                )
            except BriefError as error:
                self.error = str(error)
                raise
            brief = Brief(
                created_at=datetime.now(UTC),
                kind=kind_for(context.now.hour),
                content=written.content,
                sources=[section.title for section in sections],
                model=written.model,
                language=settings.language,
            )
            async with self._sessionmaker() as db:
                db.add(brief)
                await db.commit()
            self.error = None
            return brief

    async def due(self) -> bool:
        """Whether today's scheduled brief should be written now."""
        settings = await self.settings()
        if settings is None or self.generating:
            return False
        now = self._context().now
        if now.time() < settings.brief_clock:
            return False
        if self._last_attempt and self._clock() - self._last_attempt < RETRY_AFTER:
            return False
        latest = await self.latest()
        return latest is None or latest.created_at.astimezone(now.tzinfo).date() < now.date()

    async def run_scheduler(self, interval: float = 60) -> None:
        while True:
            try:
                if await self.due():
                    await self.generate()
            except BriefError as error:
                logger.warning("Scheduled brief failed: %s", error)
            except Exception:
                logger.exception("Scheduled brief failed")
            await asyncio.sleep(interval)
