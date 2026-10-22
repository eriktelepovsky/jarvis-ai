"""Loading data per connected account of form-based providers, through the cache."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jarvis_hud.connections.providers import ConnectedAccount, Provider, ProviderError
from jarvis_hud.plugins.base import Link, WidgetContext

CHECK = Link("Check the connection", "/integrations")


@dataclass(frozen=True)
class AccountResult:
    provider: Provider
    account: str
    data: Any = None
    updated_at: float | None = None
    error: str | None = None


async def load_connected(
    context: WidgetContext,
    providers: list[Provider],
    cache_name: str,
    fetch: Callable[[Provider, ConnectedAccount], Awaitable[Any]],
    fresh: float,
    max_age: float,
) -> list[AccountResult]:
    """Fetch data for every connected account of the given providers."""

    async def load(provider: Provider, account: ConnectedAccount) -> AccountResult:
        key = f"{provider.id}:{account.id}:{cache_name}"
        try:
            cached = await context.cache.get(key, lambda: fetch(provider, account), fresh, max_age)
        except ProviderError as error:
            return AccountResult(provider, account.account, error=str(error))
        error = str(cached.error) if isinstance(cached.error, ProviderError) else None
        if cached.error and not error:
            error = "The last update failed."
        return AccountResult(provider, account.account, cached.value, cached.fetched_at, error)

    pairs = [(p, a) for p in providers for a in await context.connections.list(p.id)]
    return list(await asyncio.gather(*(load(p, a) for p, a in pairs)))


def connected_status(results: list[AccountResult], source: str, setup: Link) -> dict[str, Any]:
    """WidgetData fields describing the accounts' state: source, age and any problem."""
    if not results:
        return {
            "source": source,
            "message": "Nothing is connected yet.",
            "message_action": setup,
            "connected": False,
        }
    times = [r.updated_at for r in results if r.data is not None and r.updated_at]
    status: dict[str, Any] = {"source": source, "updated_at": min(times) if times else None}
    if problem := next((r for r in results if r.error), None):
        status["message"] = f"{problem.provider.name} · {problem.account}: {problem.error}"
        status["message_tone"] = "warn" if problem.data is not None else "fail"
        status["message_action"] = CHECK
    return status
