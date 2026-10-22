import os
from datetime import tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCALTIME = Path("/etc/localtime")


def resolve_timezone(name: str | None) -> tzinfo:
    """The user's time zone: MYORB_TIMEZONE, else TZ, else the system's, else UTC."""
    for candidate in (name, os.environ.get("TZ"), _system_timezone_name()):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def _system_timezone_name() -> str | None:
    """Read the zone name from the /etc/localtime symlink (Linux and macOS)."""
    try:
        target = str(LOCALTIME.resolve())
    except OSError:
        return None
    _, marker, name = target.partition("zoneinfo/")
    return name if marker else None
