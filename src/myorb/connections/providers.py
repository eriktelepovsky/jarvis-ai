"""Connections set up with a form (API token, URL, place name) instead of OAuth.

A plugin describes each connection it needs as a `Provider`: the form fields and how to verify
them. myOrb renders the form with the plugin's instructions, verifies the values, stores them
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
    type: Literal["text", "password", "url", "email", "number"] = "text"
    placeholder: str = ""
    help: str = ""
    # An optional field may be left empty; the provider then uses its default.
    required: bool = True
    # A secret the user may have generated here (e.g. a token they also set in their own app):
    # the form offers Generate and Copy buttons next to it.
    generate: bool = False

    @property
    def secret(self) -> bool:
        return self.type == "password"


class ProviderError(Exception):
    """The values do not work. The message is safe to show to the user."""


class Rejected(ProviderError):
    """The service refused the credentials (HTTP 401 or 403): the user has to fix them."""


class RateLimited(ProviderError):
    """The service asks to be asked less often (HTTP 429). Nothing is wrong with the
    connection: myOrb asks again later, and there is nothing for the user to fix."""


@dataclass(frozen=True)
class Choice:
    """A part of an account the user can leave out, e.g. a workspace they no longer use."""

    id: str
    name: str


@dataclass(frozen=True)
class Verified:
    """A working connection: its name in the list, what to store, and what works."""

    account: str
    credentials: dict[str, Any]
    capabilities: list[dict[str, Any]] = field(default_factory=list)


class Provider(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    # Up to three characters shown as the logo in the list of integrations, unless it has an
    # icon: the name of a brand icon in static/icons/brands (e.g. "github").
    logo: ClassVar[str]
    icon: ClassVar[str | None] = None
    summary: ClassVar[str]
    fields: ClassVar[list[Field]]
    # Template with setup instructions, relative to the plugin's templates.
    instructions: ClassVar[str | None] = None
    # Whether several accounts can be connected (e.g. Jira workspaces).
    multiple: ClassVar[bool] = True
    # What the account's parts are called when the user can choose among them (e.g.
    # "Workspaces"); see choices().
    choices_label: ClassVar[str | None] = None
    # What one connection is called on Integrations ("+ Add account", "+ Add source").
    account_noun: ClassVar[str] = "account"
    # The hosts its API answers on, for the requests counted on Integrations; see hosts().
    api_hosts: ClassVar[tuple[str, ...]] = ()
    # How often its data is fetched, when it keeps a pace of its own (seconds); otherwise
    # its plugin's widgets decide.
    refresh: ClassVar[float | None] = None
    # The shortest pace the user may choose for it (seconds), e.g. for a strict rate limit.
    min_refresh: ClassVar[float] = 60

    def hosts(self, credentials: dict[str, Any]) -> set[str]:
        """The hosts an account's requests go to (e.g. its own Jira site)."""
        return set(self.api_hosts)

    @abstractmethod
    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        """Check the form values with the service. Raises ProviderError when they do not work."""

    async def choices(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[Choice]:
        """The account's parts the user can leave out. The ids left out are kept in the
        credentials as HIDDEN; parts added later in the service are shown until left out too."""
        return []


# The credentials' key with the ids of the parts the user left out (see Provider.choices).
HIDDEN = "hidden"


def hidden(credentials: dict[str, Any]) -> set[str]:
    return set(credentials.get(HIDDEN) or [])


@dataclass(frozen=True)
class ConnectedAccount:
    id: int
    account: str
    credentials: dict[str, Any]


def status_error(response: httpx2.Response, service: str) -> ProviderError:
    """A readable error for a failed request to a service."""
    if response.status_code == 401:
        return Rejected(
            f"{service} rejected the credentials: they are wrong, expired or revoked. "
            "Enter new ones."
        )
    if response.status_code == 403:
        return Rejected(
            f"{service} rejected the credentials: they lack a permission this needs. "
            "Check their permissions."
        )
    if response.status_code == 404:
        return ProviderError(f"{service} did not find it. Check the address.")
    if response.status_code == 429:
        return RateLimited(
            f"{service} limits how often myOrb may ask, and asked it to wait. "
            "myOrb asks again later; there is nothing to fix."
        )
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
    # Jira answers a search with wrong or expired credentials as if asked by no one: 200 and
    # nothing found. Only this header says the sign-in failed.
    if "AUTHENTICATED_FAILED" in response.headers.get("x-seraph-loginreason", ""):
        raise status_error(httpx2.Response(401), service)
    if response.status_code == 204 or not response.content:
        return None  # e.g. a change that answers "204 No Content"
    try:
        return response.json()
    except ValueError as error:
        raise ProviderError(f"{service} returned an unexpected answer.") from error
