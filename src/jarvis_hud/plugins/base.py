from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from jarvis_hud.cache import Cache
    from jarvis_hud.connections.google_accounts import GoogleAccounts


@dataclass(frozen=True)
class WidgetContext:
    """What a widget gets when it loads: the current time and the services it may use."""

    now: datetime  # in the user's time zone
    cache: "Cache"
    google: "GoogleAccounts"


@dataclass(frozen=True)
class Widget:
    """A dashboard tile provided by a plugin.

    `template` is relative to the plugin's `templates_dir` and renders the card body. `load`
    returns the template context and is called each time the widget is rendered. An optional
    `card` entry in the context describes the card around the body: `state` ("error", "stale"
    or "empty"), `updated_at` (Unix time of the data), `source` (footer label) and `action`
    (footer link: `{"label": ..., "url": ...}`).
    """

    id: str
    title: str
    template: str
    load: Callable[[WidgetContext], Awaitable[dict[str, Any]]]
    icon: str = "widget"
    # Width in columns of the 12-column dashboard grid (3, 4, 6, 8 or 12) and height in rows.
    width: int = 4
    rows: int = 1
    # The main card of the dashboard, highlighted with corner ticks.
    hero: bool = False


class Plugin(ABC):
    """Base class for Jarvis plugins.

    Plugins are discovered through the `jarvis_hud.plugins` entry point group,
    so built-in and third-party integrations are registered the same way.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    templates_dir: ClassVar[Path | None] = None
    # Dashboard order: plugins with a lower position come first.
    position: ClassVar[int] = 100

    @abstractmethod
    def widgets(self) -> list[Widget]:
        """Return the widgets this plugin adds to the dashboard."""
