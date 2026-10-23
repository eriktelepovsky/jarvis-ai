"""myOrb: personal AI-powered daily briefing dashboard."""

from importlib.metadata import PackageNotFoundError, version

# The version lives only in pyproject.toml; this reads it from the installed package.
try:
    __version__ = version("myorb")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
