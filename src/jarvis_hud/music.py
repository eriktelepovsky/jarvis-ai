"""Background music on the welcome screen: the built-in ambient synth or a YouTube player."""

import html
import re
from dataclasses import asdict, dataclass, replace
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.connections import google, store
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
    title: str = ""
    # Music picked in Jarvis lately, newest first, to pick again without searching.
    recent: tuple[dict[str, str], ...] = ()


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
    if not data:
        return Music()
    return Music(**{**data, "recent": tuple(data.get("recent", ()))})


@router.get("/music/config")
async def music_config(request: Request, db: Db) -> dict[str, str | None]:
    """Where the music comes from, read by the media dock when the music starts."""
    music = await load_music(request, db)
    return {
        "source": music.source,
        "video": music.video,
        "playlist": music.playlist,
        "title": music.title,
    }


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
    previous = await load_music(request, db)
    await save_music_settings(request, db, replace(music, recent=previous.recent))
    return RedirectResponse("/integrations?status=music-saved", status_code=303)


async def save_music_settings(request: Request, db: AsyncSession, music: Music) -> None:
    data = {**asdict(music), "recent": list(music.recent)}
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, data)


# Finding music on YouTube from the media dock, with the connected Google account

API = "https://www.googleapis.com/youtube/v3"
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
RECENT = 8
NO_ACCOUNT = "Connect a Google account with YouTube access on Integrations to search."


async def _youtube_account(request: Request) -> Any:
    accounts = await request.app.state.google.list()
    return next((a for a in accounts if SCOPE in a.scopes), None)


def _item(kind: str, item_id: str, snippet: dict[str, Any]) -> dict[str, str]:
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
    live = snippet.get("liveBroadcastContent") == "live"
    return {
        "kind": "live" if kind == "video" and live else kind,
        "id": item_id,
        # The search API returns titles HTML-escaped ("Rock &amp; Roll").
        "title": html.unescape(snippet.get("title", "")),
        "channel": html.unescape(snippet.get("channelTitle", "")),
        "thumbnail": thumbnail,
    }


async def _youtube(request: Request, path: str, params: dict[str, Any]) -> Any:
    account = await _youtube_account(request)
    if account is None:
        raise LookupError(NO_ACCOUNT)
    return await request.app.state.google.get_json(
        account, f"{API}/{path}", "YouTube Data API", params
    )


@router.get("/music/search")
async def search(request: Request, q: str) -> Response:
    """Videos, live streams and playlists for a search (each search uses YouTube API quota)."""
    q = q.strip()[:100]
    if not q:
        return JSONResponse({"results": []})
    params = {"part": "snippet", "q": q, "type": "video,playlist", "maxResults": 15}
    try:
        data = await _youtube(request, "search", params)
    except LookupError as error:
        return JSONResponse({"error": str(error)}, status_code=409)
    except google.GoogleError as error:
        return JSONResponse({"error": str(error)}, status_code=502)
    results = []
    for item in data.get("items", []):
        ids = item.get("id", {})
        kind = "playlist" if ids.get("playlistId") else "video"
        item_id = ids.get("playlistId") or ids.get("videoId")
        if item_id:
            results.append(_item(kind, item_id, item.get("snippet", {})))
    return JSONResponse({"results": results})


@router.get("/music/library")
async def library(request: Request, db: Db) -> Response:
    """The user's own YouTube playlists and the music picked lately."""
    music = await load_music(request, db)
    playlists: list[dict[str, str]] = []
    error = None

    async def fetch() -> list[dict[str, str]]:
        params = {"part": "snippet", "mine": "true", "maxResults": 25}
        data = await _youtube(request, "playlists", params)
        return [_item("playlist", i["id"], i.get("snippet", {})) for i in data.get("items", [])]

    try:
        cached = await request.app.state.cache.get(
            "music:playlists", fetch, fresh=10 * 60, max_age=24 * 60 * 60
        )
        playlists = cached.value
    except LookupError as problem:
        error = str(problem)
    except google.GoogleError as problem:
        error = str(problem)
    return JSONResponse({"playlists": playlists, "recent": list(music.recent), "error": error})


@router.post("/music/play")
async def play(
    request: Request,
    db: Db,
    kind: Annotated[str, Form()],
    id: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
    channel: Annotated[str, Form()] = "",
    thumbnail: Annotated[str, Form()] = "",
) -> Response:
    """Play something found in Jarvis; it becomes the music and joins the recent picks."""
    playlist = kind == "playlist"
    if not (PLAYLIST_ID if playlist else VIDEO_ID).match(id):
        return JSONResponse({"error": "Unknown video or playlist."}, status_code=400)
    url = (
        f"https://www.youtube.com/playlist?list={id}"
        if playlist
        else f"https://www.youtube.com/watch?v={id}"
    )
    if not thumbnail.startswith("https://i.ytimg.com/"):
        thumbnail = ""
    picked = {
        "kind": kind if kind in ("video", "live", "playlist") else "video",
        "id": id,
        "title": title[:200],
        "channel": channel[:100],
        "thumbnail": thumbnail,
    }
    previous = await load_music(request, db)
    recent = (picked, *(r for r in previous.recent if r.get("id") != id))[:RECENT]
    music = Music(
        "youtube",
        url,
        None if playlist else id,
        id if playlist else None,
        title[:200],
        recent,
    )
    await save_music_settings(request, db, music)
    return JSONResponse(
        {
            "source": "youtube",
            "video": music.video,
            "playlist": music.playlist,
            "title": music.title,
        }
    )
