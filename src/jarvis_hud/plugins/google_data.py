"""Loading data per connected Google account, shared by the Google-based plugins."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jarvis_hud.connections.google import GoogleError
from jarvis_hud.connections.google_accounts import GoogleAccount
from jarvis_hud.plugins.base import Link, WidgetContext


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


INTEGRATIONS = Link("Check the connection", "/integrations")


def account_status(accounts: list[AccountData], source: str) -> dict[str, Any]:
    """WidgetData fields describing the accounts' state: source, age and any problem."""
    if not accounts:
        return {
            "source": source,
            "message": "Google is not connected.",
            "message_action": Link("Connect Google", "/integrations"),
        }
    loaded = [account for account in accounts if account.data is not None]
    times = [account.updated_at for account in loaded if account.updated_at]
    status: dict[str, Any] = {"source": source, "updated_at": min(times) if times else None}
    if problem := next((account for account in accounts if account.error), None):
        prefix = f"{problem.email}: " if len(accounts) > 1 else ""
        status["message"] = prefix + problem.error
        status["message_tone"] = "warn" if problem.data is not None else "fail"
        status["message_action"] = INTEGRATIONS
    return status
