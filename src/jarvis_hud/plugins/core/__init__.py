from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis_hud.plugins.base import Plugin, Widget


def greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"


class CorePlugin(Plugin):
    """Built-in widgets that need no third-party integration."""

    id = "core"
    name = "Core"
    templates_dir = Path(__file__).parent / "templates"

    def widgets(self) -> list[Widget]:
        return [Widget(id="today", title="Today", template="today.html", load=self._load_today)]

    async def _load_today(self) -> dict[str, Any]:
        now = datetime.now().astimezone()
        return {"now": now, "greeting": greeting(now.hour)}
