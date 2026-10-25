from abc import ABC
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    import httpx2
    from fastapi import APIRouter
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from myorb.cache import Cache
    from myorb.connections.accounts import Connections
    from myorb.connections.google_accounts import GoogleAccounts
    from myorb.connections.providers import Provider

Tone = Literal["ok", "warn", "fail", "info", "neutral"]


@dataclass(frozen=True)
class WidgetContext:
    """What a widget or panel gets when it loads: the time and the services it may use."""

    now: datetime  # in the user's time zone
    cache: "Cache"
    google: "GoogleAccounts"
    sessionmaker: "async_sessionmaker[AsyncSession]"
    http: "httpx2.AsyncClient"
    connections: "Connections"
    # The panel request's query parameters, e.g. which day a calendar panel shows.
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Stat:
    value: str | int
    label: str
    # Clicking the number shows only the rows tagged with this (Row.tags); again shows all.
    filter: str | None = None


@dataclass(frozen=True)
class Action:
    """A button that POSTs to `url`; the widgets refresh afterwards."""

    label: str
    url: str
    # Form values sent with it, e.g. the ids of the videos to mark as seen.
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Input:
    """A one-line form in a widget's details that POSTs its `text` field to `url`."""

    url: str
    placeholder: str
    label: str


@dataclass(frozen=True)
class Row:
    """One item in a widget's details, e.g. an email or an event."""

    title: str
    # A symbol before the title, e.g. "★" for a starred email.
    mark: str = ""
    subtitle: str = ""
    badge: str = ""
    tone: Tone = "neutral"
    url: str | None = None
    actions: list[Action] = field(default_factory=list)
    # The label of the `url` link. Links within myOrb ("/…") open in the same tab.
    url_label: str = "Open"
    # Open `url` in a new tab even though it is within myOrb (e.g. a redirect to YouTube).
    new_tab: bool = False
    # The whole row opens `url` (e.g. an email), with no button for it.
    open_row: bool = False
    # What the row is, for the numbers that filter the list (Stat.filter), e.g. ("review",).
    tags: tuple[str, ...] = ()
    # Where it belongs, e.g. a Jira workspace or a repository; see WidgetData.group_label.
    group: str = ""
    # A finer group within it, e.g. a Jira project: listed under its group to narrow further.
    subgroup: str = ""
    # What the plugin's Ask actions take to find it again, e.g. a task's id: Claude sees it.
    ref: str = ""


@dataclass(frozen=True)
class WidgetData:
    """What a widget shows: its number around the orb and the details behind it.

    `value` None means there is no data to show (not connected or failed); `message` then
    explains why, and `message_action` offers the fix.
    """

    value: str | int | None
    summary: str = ""
    attention: bool = False
    stats: list[Stat] = field(default_factory=list)
    list_title: str = ""
    rows: list[Row] = field(default_factory=list)
    action: Link | None = None
    input: Input | None = None
    source: str = ""
    updated_at: float | None = None
    message: str | None = None
    message_tone: Tone = "info"
    message_action: Link | None = None
    # False when the service behind the widget is not set up yet: the widget is not shown
    # around the orb until it is (problems with a set-up service are shown instead).
    # Names the number around the orb instead of the widget's label, when what it shows
    # varies, e.g. sleep or, without sleep data, kilometres run this week.
    label: str | None = None
    # Names the rows' groups (Row.group) in the plural, e.g. "workspaces": with more than one
    # group, the list can be narrowed to one of them ("All workspaces", "archivis (64)", …).
    group_label: str | None = None
    # Buttons in the details' footer that POST, e.g. "Mark all as seen".
    actions: list[Action] = field(default_factory=list)
    # More buttons next to `action`, e.g. one per connected account: "Open archivis", "Open swida".
    links: list[Link] = field(default_factory=list)
    connected: bool = True
    # An HTML fragment shown in the details under the numbers, loaded from this URL, e.g. the
    # AI summary of unread emails.
    details_url: str | None = None


@dataclass(frozen=True)
class Widget:
    """A number around the orb on the welcome screen, with details that open on click.

    `label` names the number (e.g. "Unread"), `title` heads its details (e.g. "Email") and
    `icon` is an SVG path in a 24×24 box. The node is large when its data needs
    attention and small when it has nothing to show; `weight` adjusts that a little.
    `refresh` is how often, in seconds, the welcome screen loads it from the service again;
    the node's border fills up until then.
    """

    id: str
    label: str
    title: str
    icon: str
    load: Callable[[WidgetContext], Awaitable[WidgetData]]
    weight: float = 1.0
    refresh: float = 5 * 60


@dataclass(frozen=True)
class Panel:
    """A section rendered from a template: in the side column or under the clock.

    `template` is relative to the plugin's `templates_dir`; `load` returns its context.
    """

    id: str
    title: str
    template: str
    load: Callable[[WidgetContext], Awaitable[dict[str, Any]]]
    placement: Literal["side", "header"] = "side"


@dataclass(frozen=True)
class SettingsLink:
    logo: str
    summary: str
    url: str


@dataclass(frozen=True)
class ActionResult:
    """What an Ask action did, in a sentence for the user and Claude; and how to undo it,
    when it can be (returns what the undo did)."""

    message: str
    undo: Callable[[WidgetContext], Awaitable[str]] | None = None


class ActionError(Exception):
    """The action could not be done. The message is safe to show to the user and Claude."""


@dataclass(frozen=True)
class AskAction:
    """Something Claude may propose in Ask, e.g. adding a task; it runs only after the user
    confirms it.

    `name` is unique within the plugin. `schema` is the JSON schema of its input (an object;
    every property required, no others, as strict tool use wants). `summary` says in a line
    what it will do with that input, for the confirmation (raising ActionError when it cannot
    be done, e.g. no such task); `run` does it. An action that only reads (e.g. an email's
    text) has confirm=False: it runs at once and its message goes to Claude only.
    """

    name: str
    description: str
    schema: dict[str, Any]
    summary: Callable[[WidgetContext, dict[str, Any]], Awaitable[str]]
    run: Callable[[WidgetContext, dict[str, Any]], Awaitable[ActionResult]]
    confirm: bool = True


@dataclass(frozen=True)
class TimelineItem:
    """Something at a time today, for the brief's "Day at a glance", e.g. a meeting."""

    start: datetime
    end: datetime
    title: str


@dataclass(frozen=True)
class BriefSection:
    """Facts a plugin contributes to the AI brief, most important first, as plain text lines."""

    title: str
    lines: list[str]


class Plugin(ABC):  # noqa: B024 (plugins override whichever of the hooks they need)
    """Base class for myOrb plugins.

    Plugins are discovered through the `myorb.plugins` entry point group,
    so built-in and third-party integrations are registered the same way.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    templates_dir: ClassVar[Path | None] = None
    # Order on the welcome screen: plugins with a lower position come first.
    position: ClassVar[int] = 100
    # A settings page of the plugin, listed on the Integrations page (logo, summary, URL).
    settings: ClassVar["SettingsLink | None"] = None
    # Whether "Ask" may give Claude the widgets' rows (e.g. email senders and subjects). When
    # False, Claude only gets what the plugin's brief() says, as in the brief.
    ask_rows: ClassVar[bool] = True

    def widgets(self) -> list[Widget]:
        """Return the numbers this plugin adds around the orb."""
        return []

    def panels(self) -> list[Panel]:
        """Return the sections this plugin adds to the side column or the header."""
        return []

    def connections(self) -> list["Provider"]:
        """Return the connections this plugin is set up with on the Integrations page."""
        return []

    async def connections_changed(self, connections: "Connections") -> None:
        """Called at startup and when one of the plugin's connections is added or removed,
        e.g. to change its widgets() (which must answer without waiting)."""
        return None

    def background(self, context: Callable[[], WidgetContext]) -> Awaitable[None] | None:
        """Return a coroutine myOrb runs in the background while it is up, or None.

        `context()` gives a fresh WidgetContext each time it is called. The coroutine should
        loop, sleep between rounds and handle its own errors; it is cancelled at shutdown.
        """
        return None

    async def timeline(self, context: WidgetContext) -> list[TimelineItem]:
        """Today's items with a start and an end, for the day's timeline in the report."""
        return []

    def router(self) -> "APIRouter | None":
        """Return the plugin's own pages and endpoints, e.g. an OAuth callback or a webhook.

        Every route requires a signed-in user, except paths under /hooks/, which the plugin
        must authenticate itself (e.g. with a secret token in the URL).
        """
        return None

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        """Return facts for the AI brief, or None when the plugin has nothing to add."""
        return None

    def ask_actions(self) -> list[AskAction]:
        """Return what Claude may do for the user in Ask, each confirmed by the user first.
        Offered while the plugin's widgets are set up (rows carry the `ref` they need)."""
        return []


def open_buttons(label: str, places: list[tuple[str, str]]) -> dict[str, Any]:
    """The WidgetData fields that open a service: one button, or one per account when there are
    several ("Open archivis", "Open me@example.com"), so none of them is left out."""
    if not places:
        return {}
    if len(places) == 1:
        return {"action": Link(label, places[0][1])}
    return {"links": [Link(f"Open {name}", url) for name, url in places]}
