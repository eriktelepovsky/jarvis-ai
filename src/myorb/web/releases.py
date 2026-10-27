"""The versions of myOrb with what each changed, from its releases on GitHub: opened from the
version under the welcome screen."""

import re
import time
from datetime import datetime
from typing import Any

import httpx2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from myorb import __version__

router = APIRouter()

RELEASES = "https://api.github.com/repos/eriktelepovsky/myorb/releases"
RELEASES_PAGE = "https://github.com/eriktelepovsky/myorb/releases"
SHOWN = 30
# GitHub lets 60 unauthenticated requests an hour: read at most once an hour.
KEEP = 60 * 60
# "* Title by @someone in https://github.com/…/pull/146"
CHANGE = re.compile(r"^\*\s+(?P<title>.+?)(?:\s+by\s+@\S+)?(?:\s+in\s+(?P<url>https://\S+))?\s*$")
# A change that only says the version moved on says nothing.
ONLY_VERSION = re.compile(r"^version\s+\d+(\.\d+)*$", re.I)

_cache: dict[str, Any] = {"at": 0.0, "releases": None}


def changes(body: str) -> list[dict[str, str]]:
    """A release's changes, from the notes GitHub writes: each pull request's title and link."""
    found = []
    for line in (body or "").splitlines():
        match = CHANGE.match(line.strip())
        if match and not ONLY_VERSION.match(match["title"].strip()):
            found.append({"title": match["title"].strip(), "url": match["url"] or ""})
    return found


async def releases(http: httpx2.AsyncClient) -> list[dict[str, Any]] | None:
    """The latest releases, newest first; None when GitHub cannot be read (kept an hour)."""
    if _cache["releases"] is not None and time.time() - _cache["at"] < KEEP:
        return _cache["releases"]
    try:
        response = await http.get(
            RELEASES,
            params={"per_page": SHOWN},
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx2.HTTPError, ValueError):
        return _cache["releases"]
    found = [
        {
            "version": str(r.get("tag_name", "")).removeprefix("v"),
            "date": datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
            if r.get("published_at")
            else None,
            "url": r.get("html_url", ""),
            "changes": changes(r.get("body", "")),
        }
        for r in data
        if isinstance(r, dict) and not r.get("draft")
    ]
    _cache.update(at=time.time(), releases=found)
    return found


@router.get("/releases", response_class=HTMLResponse)
async def release_notes(request: Request) -> Response:
    return request.app.state.templates.TemplateResponse(
        request,
        "releases.html",
        {
            "releases": await releases(request.app.state.http),
            "running": __version__,
            "releases_page": RELEASES_PAGE,
        },
    )
