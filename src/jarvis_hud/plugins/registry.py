import logging
from importlib.metadata import entry_points

from jarvis_hud.plugins.base import Plugin

ENTRY_POINT_GROUP = "jarvis_hud.plugins"

logger = logging.getLogger(__name__)


def discover_plugins() -> dict[str, Plugin]:
    """Load all installed plugins, keyed by plugin id.

    A plugin that fails to load is logged and skipped, so one broken
    integration does not take down the whole dashboard.
    """
    plugins: dict[str, Plugin] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            plugin = entry_point.load()()
        except Exception:
            logger.exception("Failed to load plugin %r", entry_point.name)
            continue
        if plugin.id in plugins:
            logger.error("Duplicate plugin id %r from %r, skipping", plugin.id, entry_point.value)
            continue
        plugins[plugin.id] = plugin
    return plugins
