import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.connections import google, store
from myorb.crypto import SecretBox

# Renew access tokens a minute before Google expires them.
EXPIRY_MARGIN = 60


@dataclass(frozen=True)
class GoogleAccount:
    id: int
    email: str
    scopes: frozenset[str]


class GoogleAccounts:
    """Connected Google accounts and authenticated requests to Google APIs for plugins."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        box: SecretBox,
        http: httpx2.AsyncClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._box = box
        self._http = http
        self._clock = clock
        self._tokens: dict[int, tuple[str, float]] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def list(self) -> list[GoogleAccount]:
        async with self._sessionmaker() as db:
            connections = await store.list_connections(db, google.PROVIDER)
        return [GoogleAccount(c.id, c.account, frozenset(c.scopes.split())) for c in connections]

    def forget(self, account_id: int) -> None:
        """Drop the cached access token, e.g. after the connection changed."""
        self._tokens.pop(account_id, None)

    async def _access_token(self, account: GoogleAccount) -> str:
        async with self._locks[account.id]:
            cached = self._tokens.get(account.id)
            if cached and cached[1] > self._clock():
                return cached[0]
            async with self._sessionmaker() as db:
                client = await store.get_provider_settings(db, self._box, google.PROVIDER)
                connection = await store.get_connection(db, google.PROVIDER, account.id)
            if client is None or connection is None:
                raise google.GoogleError("The Google connection was removed.")
            refresh_token = self._box.decrypt(connection.credentials)["refresh_token"]
            token, lifetime = await google.refresh_access_token(
                self._http, google.OAuthClient(**client), refresh_token
            )
            self._tokens[account.id] = (token, self._clock() + lifetime - EXPIRY_MARGIN)
            return token

    async def get_json(
        self, account: GoogleAccount, url: str, api_name: str, params: dict | None = None
    ) -> Any:
        """GET a Google API endpoint as the account. Raises GoogleError with a readable message."""
        return await self.request_json(account, "GET", url, api_name, params=params)

    async def request_json(
        self,
        account: GoogleAccount,
        method: str,
        url: str,
        api_name: str,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        """Call a Google API endpoint as the account. Raises GoogleError with a readable
        message."""
        for attempt in range(2):
            token = await self._access_token(account)
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx2.HTTPError as error:
                raise google.GoogleError("Google could not be reached.") from error
            if response.status_code == 401 and attempt == 0:
                # The token was revoked or expired early; get a new one and retry once.
                self.forget(account.id)
                continue
            if response.status_code == 200:
                return response.json()
            if google.is_api_disabled(response):
                raise google.GoogleError(f"The {api_name} is not enabled in your Google project.")
            if response.status_code == 403:
                raise google.GoogleError(f"myOrb has no permission to use the {api_name}.")
            raise google.GoogleError(f"Google returned an error (HTTP {response.status_code}).")
        raise google.GoogleError("Google did not accept the access token.")
