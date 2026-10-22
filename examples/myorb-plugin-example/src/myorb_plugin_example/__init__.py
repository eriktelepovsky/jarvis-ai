"""Example myOrb plugin: account balances from your own JSON API.

It expects GET <url>/balances with "Authorization: Bearer <token>" to return:

    {"accounts": [{"name": "Business", "balance": 18420.5, "currency": "EUR"}, ...]}

Replace the API call and the data shape with your own service; the rest shows each part of the
plugin API: a connection set up with a form, a number around the orb with details, a side
panel and facts for the AI brief.
"""

from pathlib import Path
from typing import Any

import httpx2

from myorb.connections.providers import (
    ConnectedAccount,
    Field,
    Provider,
    ProviderError,
    Verified,
    request_json,
)
from myorb.plugins import (
    BriefSection,
    Link,
    Panel,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from myorb.plugins.connection_data import connected_status, load_connected

ICON = "M3 6h18v13H3zM3 10h18M16 15h2"


class WalletApi(Provider):
    """The connection: which fields the user fills in and how myOrb checks them."""

    id = "example_wallet"
    name = "Wallet (example)"
    logo = "€"
    summary = "Account balances from your own wallet API."
    instructions = "connect.html"
    multiple = False
    fields = [
        Field("url", "API address", "url", "https://wallet.example.com/api"),
        Field("token", "API token", "password"),
    ]

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        url = values["url"].rstrip("/")
        if not url.startswith("https://"):
            raise ProviderError("Use an https:// address.")
        credentials = {"url": url, "token": values["token"]}
        await balances(http, credentials)  # raises ProviderError with a readable message
        return Verified(account=url.removeprefix("https://"), credentials=credentials)


async def balances(http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[dict[str, Any]]:
    data = await request_json(
        http,
        "GET",
        f"{credentials['url']}/balances",
        "The wallet",
        headers={"Authorization": f"Bearer {credentials['token']}"},
    )
    return data.get("accounts", [])


def money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


class WalletPlugin(Plugin):
    id = "example_wallet"
    name = "Wallet"
    templates_dir = Path(__file__).parent / "templates"
    position = 70

    def __init__(self) -> None:
        self.api = WalletApi()

    def connections(self) -> list[Provider]:
        return [self.api]

    def widgets(self) -> list[Widget]:
        return [
            Widget(id="balance", label="Balance", title="Accounts", icon=ICON, load=self._widget)
        ]

    def panels(self) -> list[Panel]:
        return [Panel(id="balances", title="Accounts", template="balances.html", load=self._panel)]

    async def _accounts(self, context: WidgetContext) -> list:
        async def fetch(provider: Provider, account: ConnectedAccount) -> list[dict[str, Any]]:
            return await balances(context.http, account.credentials)

        # Cached: fresh for 15 minutes, shown with a warning for up to 6 hours if the API fails.
        return await load_connected(context, [self.api], "balances", fetch, 15 * 60, 6 * 60 * 60)

    async def _widget(self, context: WidgetContext) -> WidgetData:
        results = await self._accounts(context)
        status = connected_status(
            results, "Wallet", Link("Connect the wallet", "/integrations/example_wallet/connect")
        )
        accounts = [a for r in results if r.data for a in r.data]
        if not any(r.data is not None for r in results):
            return WidgetData(value=None, **status)
        currency = accounts[0]["currency"] if accounts else "EUR"
        total = sum(a["balance"] for a in accounts if a["currency"] == currency)
        return WidgetData(
            value=f"{total / 1000:.1f}k" if total >= 10_000 else round(total),
            summary=f"{currency} across {len(accounts)} accounts",
            attention=any(a["balance"] < 0 for a in accounts),
            stats=[Stat(money(total, currency), "Total"), Stat(len(accounts), "Accounts")],
            list_title="Accounts",
            rows=[
                Row(
                    a["name"],
                    badge=money(a["balance"], a["currency"]),
                    tone="fail" if a["balance"] < 0 else "neutral",
                )
                for a in accounts
            ],
            **status,
        )

    async def _panel(self, context: WidgetContext) -> dict[str, Any]:
        results = await self._accounts(context)
        return {"accounts": [a for r in results if r.data for a in r.data], "money": money}

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        results = await self._accounts(context)
        accounts = [a for r in results if r.data for a in r.data]
        if not accounts:
            return None
        return BriefSection(
            "Accounts", [f"{a['name']}: {money(a['balance'], a['currency'])}" for a in accounts]
        )
