"""The versions of myOrb with what each changed, from its releases on GitHub: opened from the
version under the welcome screen.

A release does not change once it is made, so the notes are read once for each version myOrb
runs (at its start, after an upgrade) and kept in the data directory; read again only when
reading them failed.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from myorb import __version__

logger = logging.getLogger(__name__)

router = APIRouter()

RELEASES = "https://api.github.com/repos/eriktelepovsky/myorb/releases"
RELEASES_PAGE = "https://github.com/eriktelepovsky/myorb/releases"
SHOWN = 30
# "* Title by @someone in https://github.com/…/pull/146"
CHANGE = re.compile(r"^\*\s+(?P<title>.+?)(?:\s+by\s+@\S+)?(?:\s+in\s+(?P<url>https://\S+))?\s*$")
# A change that only says the version moved on says nothing.
ONLY_VERSION = re.compile(r"^version\s+\d+(\.\d+)*$", re.I)


def changes(body: str) -> list[dict[str, str]]:
    """A release's changes, from the notes GitHub writes: each pull request's title and link."""
    found = []
    for line in (body or "").splitlines():
        match = CHANGE.match(line.strip())
        if match and not ONLY_VERSION.match(match["title"].strip()):
            found.append({"title": match["title"].strip(), "url": match["url"] or ""})
    return found


def _parsed(data: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "version": str(r.get("tag_name", "")).removeprefix("v"),
            "published": r.get("published_at") or "",
            "url": r.get("html_url", ""),
            "changes": changes(r.get("body", "")),
        }
        for r in data
        if isinstance(r, dict) and not r.get("draft")
    ]


def _file(app: FastAPI) -> Path:
    return app.state.settings.data_dir / "releases.json"


def _stored(app: FastAPI) -> dict[str, Any] | None:
    try:
        return json.loads(_file(app).read_text())
    except (OSError, ValueError):
        return None


async def fetch(app: FastAPI) -> list[dict[str, Any]] | None:
    """The release notes, kept for the running version; read from GitHub when there are none
    for it yet. None when they were never read."""
    stored = _stored(app)
    if stored and stored.get("version") == __version__:
        return stored["releases"]
    try:
        response = await app.state.http.get(
            RELEASES,
            params={"per_page": SHOWN},
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        found = _parsed(response.json())
    except (httpx2.HTTPError, ValueError) as error:
        logger.warning("Reading the release notes from GitHub failed: %s", error)
        return stored["releases"] if stored else None
    try:
        _file(app).write_text(json.dumps({"version": __version__, "releases": found}))
    except OSError:
        logger.exception("Keeping the release notes failed")
    return found


def _dated(release: dict[str, Any]) -> dict[str, Any]:
    published = release.get("published")
    date = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None
    return {**release, "date": date}


@router.get("/releases", response_class=HTMLResponse)
async def release_notes(request: Request) -> Response:
    found = await fetch(request.app)
    return request.app.state.templates.TemplateResponse(
        request,
        "releases.html",
        {
            "releases": [_dated(r) for r in found] if found is not None else None,
            "running": __version__,
            "releases_page": RELEASES_PAGE,
        },
    )
