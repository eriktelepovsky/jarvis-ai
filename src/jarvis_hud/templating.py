from datetime import UTC, datetime, tzinfo

from jinja2 import Environment


def register_filters(env: Environment, timezone: tzinfo) -> None:
    def localtime(value: datetime, format: str = "%d %b %Y %H:%M") -> str:
        """Format a datetime in the user's time zone. Naive values are treated as UTC."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(timezone).strftime(format)

    def ago(timestamp: float) -> str:
        """How long ago a Unix timestamp was, e.g. "3 min ago"."""
        seconds = max(0, int(datetime.now(UTC).timestamp() - timestamp))
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60} min ago"
        if seconds < 86400:
            return f"{seconds // 3600} h ago"
        return f"{seconds // 86400} d ago"

    env.filters["localtime"] = localtime
    env.filters["ago"] = ago
