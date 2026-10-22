"""Loading data per connected Google account, shared by the Google-based plugins."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jarvis_hud.connections.google import GoogleError
from jarvis_hud.connections.google_accounts import GoogleAccount
from jarvis_hud.plugins.base import WidgetContext


@dataclass(frozen=True)
class AccountData:
    email: str
    data: Any = None
    updated_at: float | None = None
    error: str | None = None


async def load_accounts(
    context: WidgetContext,
    scope: str,
    cache_name: str,
    fetch: Callable[[GoogleAccount], Awaitable[Any]],
    fresh: float,
    max_age: float,
) -> dict[str, Any]:
    """Fetch data for every connected account through the cache.

    Returns `connected` (whether any Google account is connected) and `accounts`, one
    AccountData each, carrying either data or a message explaining what is wrong.
    """
    accounts = await context.google.list()

    async def load(account: GoogleAccount) -> AccountData:
        if scope not in account.scopes:
            error = "Jarvis has no permission for this. Connect the account again."
            return AccountData(account.email, error=error)
        key = f"google:{account.id}:{cache_name}"
        try:
            cached = await context.cache.get(key, lambda: fetch(account), fresh, max_age)
        except GoogleError as error:
            return AccountData(account.email, error=str(error))
        error = str(cached.error) if isinstance(cached.error, GoogleError) else None
        if cached.error and not error:
            error = "The last update failed."
        return AccountData(account.email, cached.value, cached.fetched_at, error)

    return {
        "connected": bool(accounts),
        "accounts": await asyncio.gather(*(load(account) for account in accounts)),
    }
