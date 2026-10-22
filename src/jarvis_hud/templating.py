from datetime import UTC, datetime, tzinfo

from jinja2 import Environment

from jarvis_hud.brief.writer import model_name


def register_filters(env: Environment, timezone: tzinfo) -> None:
    def localtime(value: datetime, format: str = "%d %b %Y %H:%M") -> str:
        """Format a datetime in the user's time zone. Naive values are treated as UTC."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(timezone).strftime(format)

    def age(timestamp: float) -> str:
        """How old a Unix timestamp is, compactly: "now", "3m", "2h", "4d"."""
        seconds = max(0, int(datetime.now(UTC).timestamp() - timestamp))
        if seconds < 60:
            return "now"
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"

    def ago(timestamp: float) -> str:
        """How long ago a Unix timestamp was: "now", "3m ago"."""
        value = age(timestamp)
        return value if value == "now" else f"{value} ago"

    env.filters["localtime"] = localtime
    env.globals["model_name"] = model_name
    env.filters["age"] = age
    env.filters["ago"] = ago
