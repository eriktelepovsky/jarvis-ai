"""Custom sources: the user's own apps (e.g. invoicing, a wallet) shown around the orb.

Each source is a URL that answers with JSON in myOrb's format (schema.py), read with a token.
Sources that share a circle name are shown together as one circle, e.g. invoices and the
wallet as "Finance": its number adds up what needs the user in each.
"""

import json
import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx2

from myorb.connections.providers import (
    ConnectedAccount,
    Field,
    Provider,
    ProviderError,
    Verified,
    status_error,
)
from myorb.plugins.base import (
    BriefSection,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from myorb.plugins.custom.schema import SourceData, parse

if TYPE_CHECKING:
    from myorb.connections.accounts import Connections

PROVIDER = "custom"
ICON = "M4 6h16M4 12h16M4 18h10"
DEFAULT_REFRESH = 15  # minutes
MAX_BYTES = 2_000_000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
CHECK = Link("Check the source", "/integrations")


def source_url(value: str) -> str:
    """HTTPS, or plain HTTP only on this machine (an app in development)."""
    parts = urlsplit(value.strip())
    host = parts.hostname or ""
    local = host in LOCAL_HOSTS or host.endswith((".localhost", ".test"))
    if not parts.netloc or not (parts.scheme == "https" or (parts.scheme == "http" and local)):
        raise ProviderError("Enter the source's address, starting with https://.")
    return value.strip()


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source"


def refresh_minutes(value: Any) -> int:
    try:
        minutes = int(value or DEFAULT_REFRESH)
    except ValueError:
        raise ProviderError("Enter how often to load it, in minutes.") from None
    if not 1 <= minutes <= 24 * 60:
        raise ProviderError("Load it every 1 to 1440 minutes.")
    return minutes


class CustomSource(Provider):
    id = PROVIDER
    name = "Custom source"
    logo = "{ }"
    summary = "Your own app, e.g. invoicing or a wallet, answering with JSON in myOrb's format."
    instructions = "custom.html"
    account_noun = "source"

    def hosts(self, credentials: dict[str, Any]) -> set[str]:
        return {urlsplit(credentials["url"]).hostname or ""}

    def refresh_every(self, credentials: dict[str, Any]) -> float:
        return 60 * refresh_minutes(credentials.get("refresh"))

    fields = [
        Field("name", "Name", placeholder="e.g. Invoices"),
        Field("url", "Address", "url", "https://invoices.example.com/myorb/"),
        Field(
            "token",
            "Token",
            "password",
            help=(
                "A long random secret that your app checks, so only myOrb can read it. "
                "Generate one, copy it and set the same in your app (e.g. MYORB_TOKEN); "
                "myOrb sends it as “Authorization: Bearer <token>”."
            ),
            generate=True,
        ),
        Field(
            "circle",
            "Circle",
            placeholder="e.g. Finance",
            help="Sources with the same circle are shown together. Empty: its own circle.",
            required=False,
        ),
        Field(
            "refresh",
            "Load every (minutes)",
            "number",
            str(DEFAULT_REFRESH),
            required=False,
        ),
    ]

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        name = " ".join(values["name"].split())[:40]
        credentials = {
            "name": name,
            "url": source_url(values["url"]),
            "token": values["token"],
            "circle": " ".join((values.get("circle") or "").split())[:40] or name,
            "refresh": refresh_minutes(values.get("refresh")),
        }
        data = await self.fetch(http, credentials)
        capability = {"id": "data", "label": f"Read the data ({data.title})", "ok": True}
        return Verified(name, credentials, [capability])

    async def fetch(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> SourceData:
        headers = {"Authorization": f"Bearer {credentials['token']}", "Accept": "application/json"}
        try:
            response = await http.get(credentials["url"], headers=headers)
        except httpx2.HTTPError as error:
            raise ProviderError("The source could not be reached.") from error
        if response.status_code >= 400:
            raise status_error(response, "The source")
        if len(response.content) > MAX_BYTES:
            raise ProviderError("The answer is too large (over 2 MB).")
        try:
            payload = json.loads(response.content)
        except ValueError:
            raise ProviderError("The source did not answer with JSON.") from None
        return parse(payload)


@dataclass(frozen=True)
class Circle:
    name: str
    refresh: float  # seconds: the shortest of its sources


@dataclass(frozen=True)
class Loaded:
    account: ConnectedAccount
    data: SourceData | None
    updated_at: float | None
    error: str | None


def numeric(value: int | float | str) -> float:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


class CustomPlugin(Plugin):
    """The user's own sources, one circle around the orb per circle name."""

    id = "custom"
    name = "Custom sources"
    templates_dir = Path(__file__).parent / "templates"
    position = 60
    # A source decides what Claude may know (its "brief" sentences); its rows stay out of Ask.
    ask_rows = False

    def __init__(self) -> None:
        self.provider = CustomSource()
        self._circles: list[Circle] = []

    def connections(self) -> list[Provider]:
        return [self.provider]

    async def connections_changed(self, connections: "Connections") -> None:
        circles: dict[str, float] = {}
        for account in await connections.list(PROVIDER):
            name = account.credentials.get("circle") or account.account
            refresh = 60 * refresh_minutes(account.credentials.get("refresh"))
            circles[name] = min(circles.get(name, refresh), refresh)
        self._circles = [Circle(name, refresh) for name, refresh in sorted(circles.items())]

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id=slug(circle.name),
                label=circle.name,
                title=circle.name,
                icon=ICON,
                load=partial(self._widget, circle.name),
                refresh=circle.refresh,
            )
            for circle in self._circles
        ]

    async def _load(self, context: WidgetContext, circle: str | None = None) -> list[Loaded]:
        """Every source (of one circle), from the cache: fresh for its own interval."""
        results = []
        for account in await context.connections.list(PROVIDER):
            credentials = account.credentials
            if circle is not None and (credentials.get("circle") or account.account) != circle:
                continue
            fresh = 60 * refresh_minutes(credentials.get("refresh"))

            async def fetch(credentials: dict[str, Any] = credentials) -> SourceData:
                return await self.provider.fetch(context.http, credentials)

            key = f"{PROVIDER}:{account.id}:data"
            try:
                cached = await context.cache.get(key, fetch, fresh, max(3600, 4 * fresh))
            except ProviderError as error:
                results.append(Loaded(account, None, None, str(error)))
                continue
            error = None
            if cached.error:
                error = (
                    str(cached.error)
                    if isinstance(cached.error, ProviderError)
                    else ("The last update failed.")
                )
            results.append(Loaded(account, cached.value, cached.fetched_at, error))
        return results

    async def _widget(self, circle: str, context: WidgetContext) -> WidgetData:
        results = await self._load(context, circle)
        if not results:
            return WidgetData(value=None, connected=False)
        loaded = [r for r in results if r.data is not None]
        status: dict[str, Any] = {"source": " · ".join(r.account.account for r in results)}
        if problem := next((r for r in results if r.error), None):
            status |= {
                "message": f"{problem.account.account}: {problem.error}",
                "message_tone": "warn" if loaded else "fail",
                "message_action": CHECK,
            }
        if not loaded:
            return WidgetData(value=None, **status)
        times = [r.updated_at for r in loaded if r.updated_at]
        status["updated_at"] = min(times) if times else None
        sources = [r.data for r in loaded]
        links = [Link(d.link.label, d.link.url) for d in sources if d.link]
        if len(sources) == 1:
            return self._one(sources[0], links, status)
        return self._together(circle, sources, links, status)

    @staticmethod
    def _rows(data: SourceData, prefix: str = "", group: str | None = None) -> list[Row]:
        return [
            Row(
                row.title,
                row.subtitle,
                row.badge,
                row.tone,
                row.url,
                tags=tuple(prefix + tag for tag in row.tags),
                group=group if group is not None else row.group,
                subgroup=row.group if group is not None else "",
            )
            for row in data.rows
        ]

    def _one(self, data: SourceData, links: list[Link], status: dict[str, Any]) -> WidgetData:
        value = data.attention_count if data.attention_count is not None else data.value
        # Under the number stays the circle's name (e.g. Finance), so it says what it is about;
        # what the number counts (the source's label, e.g. "pending") leads its summary.
        label = (data.label or "").lower()
        summary = (
            data.summary
            if not label or data.summary.startswith(label)
            else f"{label} · {data.summary}"
        )
        return WidgetData(
            value=value,
            summary=summary.strip(" ·"),
            attention=data.attention,
            stats=[Stat(s.value, s.label, s.filter) for s in data.stats],
            list_title=data.list_title,
            group_label=data.group_label,
            rows=self._rows(data),
            action=links[0] if links else None,
            **status,
        )

    def _together(
        self, circle: str, sources: list[SourceData], links: list[Link], status: dict[str, Any]
    ) -> WidgetData:
        """Several sources as one circle: each source is a group in the details, its own
        groups (e.g. clients) under it; their filters stay apart (prefixed per source)."""
        stats, rows = [], []
        for index, data in enumerate(sources):
            prefix = f"s{index}-"
            stats += [Stat(s.value, s.label, s.filter and prefix + s.filter) for s in data.stats]
            rows += self._rows(data, prefix, group=data.title)
        return WidgetData(
            value=sum(
                d.attention_count if d.attention_count is not None else numeric(d.value)
                for d in sources
            ),
            summary=" · ".join(
                f"{d.title}: {d.summary}" if d.summary else d.title for d in sources
            ),
            attention=any(d.attention for d in sources),
            stats=stats,
            list_title=circle,
            group_label="sources",
            rows=rows,
            **({"action": links[0]} if len(links) == 1 else {"links": links}),
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        # Each sentence links to the source's page in the app (its "link"), for Ask.
        found = [
            (line, result.data.link.url if result.data.link else "")
            for result in await self._load(context)
            if result.data is not None
            for line in result.data.brief
        ]
        if not found:
            return None
        return BriefSection(
            "Your own sources", [line for line, _ in found], [url for _, url in found]
        )
