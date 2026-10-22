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

    `template` is relative to the plugin's `templates_dir`. `load` returns the
    template context and is called each time the widget is rendered.
    """

    id: str
    title: str
    template: str
    load: Callable[[WidgetContext], Awaitable[dict[str, Any]]]


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
