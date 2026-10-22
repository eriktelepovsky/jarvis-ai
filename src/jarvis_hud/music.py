"""Background music on the welcome screen: the built-in ambient synth or a YouTube player."""

import re
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.connections import store
from jarvis_hud.db import get_db

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

PROVIDER = "music"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


@dataclass(frozen=True)
class Music:
    source: str = "ambient"  # "ambient" or "youtube"
    url: str = ""
    video: str | None = None
    playlist: str | None = None


def parse_youtube(url: str) -> tuple[str | None, str | None]:
    """Return (video id, playlist id) from a YouTube link, or raise ValueError."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or parts.netloc.lower() not in YOUTUBE_HOSTS:
        raise ValueError("Enter a link to a YouTube video, playlist or live stream.")
    query = parse_qs(parts.query)
    video = query.get("v", [None])[0]
    playlist = query.get("list", [None])[0]
    path = [p for p in parts.path.split("/") if p]
    if parts.netloc.lower() == "youtu.be" and path:
        video = path[0]
    elif len(path) == 2 and path[0] in ("live", "shorts", "embed"):
        video = path[1]
    if video and not VIDEO_ID.match(video):
        video = None
    if playlist and not PLAYLIST_ID.match(playlist):
        playlist = None
    if not video and not playlist:
        raise ValueError("This link has no video or playlist in it.")
    return video, playlist


async def load_music(request: Request, db: AsyncSession) -> Music:
    data = await store.get_provider_settings(db, request.app.state.secret_box, PROVIDER)
    return Music(**data) if data else Music()


@router.get("/music/config")
async def music_config(request: Request, db: Db) -> dict[str, str | None]:
    """Where the music comes from, read by the media dock when the music starts."""
    music = await load_music(request, db)
    return {"source": music.source, "video": music.video, "playlist": music.playlist}


@router.get("/integrations/music", response_class=HTMLResponse)
async def music_page(request: Request, db: Db) -> Response:
    music = await load_music(request, db)
    return request.app.state.templates.TemplateResponse(
        request, "music.html", {"music": music, "source": music.source, "url": music.url}
    )


@router.post("/integrations/music")
async def save_music(
    request: Request,
    db: Db,
    source: Annotated[str, Form()] = "ambient",
    url: Annotated[str, Form()] = "",
) -> Response:
    music = Music()
    if source == "youtube":
        try:
            video, playlist = parse_youtube(url)
        except ValueError as error:
            return request.app.state.templates.TemplateResponse(
                request,
                "music.html",
                {
                    "music": await load_music(request, db),
                    "source": source,
                    "url": url,
                    "error": str(error),
                },
                status_code=400,
            )
        music = Music("youtube", url.strip(), video, playlist)
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, music.__dict__)
    return RedirectResponse("/integrations?status=music-saved", status_code=303)
