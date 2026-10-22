"""Connections set up with a form (API token, URL, place name) instead of OAuth.

A plugin describes each connection it needs as a `Provider`: the form fields and how to verify
them. Jarvis renders the form with the plugin's instructions, verifies the values, stores them
encrypted and tests the connection again on request.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

import httpx2


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: Literal["text", "password", "url", "email"] = "text"
    placeholder: str = ""
    help: str = ""

    @property
    def secret(self) -> bool:
        return self.type == "password"


class ProviderError(Exception):
    """The values do not work. The message is safe to show to the user."""


@dataclass(frozen=True)
class Verified:
    """A working connection: its name in the list, what to store, and what works."""

    account: str
    credentials: dict[str, Any]
    capabilities: list[dict[str, Any]] = field(default_factory=list)


class Provider(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    # Up to three characters shown as the logo in the list of integrations.
    logo: ClassVar[str]
    summary: ClassVar[str]
    fields: ClassVar[list[Field]]
    # Template with setup instructions, relative to the plugin's templates.
    instructions: ClassVar[str | None] = None
    # Whether several accounts can be connected (e.g. Jira workspaces).
    multiple: ClassVar[bool] = True

    @abstractmethod
    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        """Check the form values with the service. Raises ProviderError when they do not work."""


@dataclass(frozen=True)
class ConnectedAccount:
    id: int
    account: str
    credentials: dict[str, Any]


def status_error(response: httpx2.Response, service: str) -> ProviderError:
    """A readable error for a failed request to a service."""
    if response.status_code in (401, 403):
        return ProviderError(
            f"{service} rejected the credentials. Check them and their permissions."
        )
    if response.status_code == 404:
        return ProviderError(f"{service} did not find it. Check the address.")
    if response.status_code == 429:
        return ProviderError(f"{service} is rate limiting requests. Try again later.")
    return ProviderError(f"{service} returned an error (HTTP {response.status_code}).")


async def request_json(
    http: httpx2.AsyncClient, method: str, url: str, service: str, **kwargs: Any
) -> Any:
    """Call a service and return its JSON, or raise ProviderError with a readable message."""
    try:
        response = await http.request(method, url, **kwargs)
    except httpx2.HTTPError as error:
        raise ProviderError(f"{service} could not be reached.") from error
    if response.status_code >= 400:
        raise status_error(response, service)
    try:
        return response.json()
    except ValueError as error:
        raise ProviderError(f"{service} returned an unexpected answer.") from error
