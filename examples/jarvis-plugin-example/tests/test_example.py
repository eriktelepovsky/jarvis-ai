import asyncio

import httpx2
import pytest
from jarvis_plugin_example import WalletApi, balances

from jarvis_hud.connections.providers import ProviderError

ACCOUNTS = {"accounts": [{"name": "Business", "balance": 18420.5, "currency": "EUR"}]}


def transport(request: httpx2.Request) -> httpx2.Response:
    if request.headers.get("authorization") != "Bearer good":
        return httpx2.Response(401)
    return httpx2.Response(200, json=ACCOUNTS)


def run(coroutine):
    return asyncio.run(coroutine)


def test_balances():
    async def call():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as http:
            return await balances(http, {"url": "https://wallet.example.com/api", "token": "good"})

    assert run(call()) == ACCOUNTS["accounts"]


def test_verify_rejects_a_wrong_token():
    async def call():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as http:
            await WalletApi().verify(
                http, {"url": "https://wallet.example.com/api", "token": "bad"}
            )

    with pytest.raises(ProviderError, match="rejected the credentials"):
        run(call())
