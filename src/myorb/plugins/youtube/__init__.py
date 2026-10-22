"""New videos from the channels the user subscribes to on YouTube.

The subscriptions come from the YouTube Data API (through the Google connection, a few quota
units a day); the videos come from each channel's public RSS feed, which costs no quota.
"""

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import httpx2
from fastapi import APIRouter
from sqlalchemy import select

from myorb.connections.google import GoogleError
from myorb.connections.google_accounts import GoogleAccount, GoogleAccounts
from myorb.plugins.base import (
    Action,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from myorb.plugins.google_data import account_status, load_accounts
from myorb.plugins.youtube.models import SeenVideo

API = "https://www.googleapis.com/youtube/v3"
API_NAME = "YouTube Data API v3"
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
FEED = "https://www.youtube.com/feeds/videos.xml"
MAX_CHANNELS = 200
PARALLEL_FEEDS = 10
ICON = "M3 5h18v14H3zM10 9l5 3-5 3Z"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


async def subscriptions(google: GoogleAccounts, account: GoogleAccount) -> list[dict[str, str]]:
    channels: list[dict[str, str]] = []
    page_token = None
    while len(channels) < MAX_CHANNELS:
        params: dict[str, Any] = {"mine": "true", "part": "snippet", "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = await google.get_json(account, f"{API}/subscriptions", API_NAME, params)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            channel_id = snippet.get("resourceId", {}).get("channelId")
            if channel_id:
                channels.append({"id": channel_id, "title": snippet.get("title", "")})
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return channels


def parse_feed(xml: bytes, channel_title: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    videos = []
    for entry in root.findall("atom:entry", NS):
        link = entry.find("atom:link", NS)
        published = entry.findtext("atom:published", default="", namespaces=NS)
        try:
            when = datetime.fromisoformat(published)
        except ValueError:
            continue
        videos.append(
            {
                "id": entry.findtext("yt:videoId", default="", namespaces=NS),
                "title": entry.findtext("atom:title", default="", namespaces=NS),
                "url": link.get("href") if link is not None else "",
                "published": when,
                "channel": channel_title,
            }
        )
    return videos


async def latest_videos(http: httpx2.AsyncClient, channels: list[dict[str, str]]) -> list[dict]:
    limit = asyncio.Semaphore(PARALLEL_FEEDS)

    async def feed(channel: dict[str, str]) -> list[dict[str, Any]]:
        async with limit:
            try:
                response = await http.get(FEED, params={"channel_id": channel["id"]})
                if response.status_code != 200:
                    return []
                return parse_feed(response.content, channel["title"])
            except (httpx2.HTTPError, ET.ParseError):
                # One unavailable channel feed leaves the others unaffected.
                return []

    feeds = await asyncio.gather(*(feed(channel) for channel in channels))
    return sorted(
        (v for videos in feeds for v in videos), key=lambda v: v["published"], reverse=True
    )


def age(published: datetime, now: datetime) -> str:
    hours = int((now - published).total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


class YouTubePlugin(Plugin):
    """New videos from YouTube subscriptions."""

    id = "youtube"
    name = "YouTube"
    position = 60

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="videos",
                label="Videos",
                title="New videos",
                icon=ICON,
                load=self._widget,
                weight=0.85,
                refresh=30 * 60,
            )
        ]

    async def _widget(self, context: WidgetContext) -> WidgetData:
        async def fetch(account: GoogleAccount) -> dict[str, Any]:
            channels = await context.cache.get(
                f"google:{account.id}:youtube-subscriptions",
                lambda: subscriptions(context.google, account),
                fresh=12 * 60 * 60,
                max_age=3 * 24 * 60 * 60,
            )
            if channels.error and not isinstance(channels.error, GoogleError):
                raise GoogleError("YouTube subscriptions could not be loaded.")
            videos = await latest_videos(context.http, channels.value)
            return {"channels": len(channels.value), "videos": videos}

        result = await load_accounts(
            context, SCOPE, "youtube-videos", fetch, fresh=30 * 60, max_age=6 * 60 * 60
        )
        accounts = result["accounts"]
        status = account_status(accounts, "YouTube")
        loaded = [a for a in accounts if a.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        now = context.now
        seen: set[str] = set()
        videos = []
        for video in sorted(
            (v for a in loaded for v in a.data["videos"]),
            key=lambda v: v["published"],
            reverse=True,
        ):
            if video["id"] not in seen:
                seen.add(video["id"])
                videos.append(video)
        week = [v for v in videos if now - v["published"] < timedelta(days=7)]
        async with context.sessionmaker() as db:
            watched = set(await db.scalars(select(SeenVideo.video_id)))
        # The orb counts what is new and not seen yet; YouTube does not tell what was watched,
        # so a video counts as seen once opened from myOrb or marked as seen.
        new = [
            v for v in week if v["id"] not in watched and now - v["published"] < timedelta(days=1)
        ]
        unseen = [v for v in week if v["id"] not in watched]
        rows = []
        # Not seen first, newest first; then the seen ones.
        for v in sorted(week, key=lambda v: v["id"] in watched):
            is_seen = v["id"] in watched
            fresh = not is_seen and now - v["published"] < timedelta(days=1)
            rows.append(
                Row(
                    v["title"],
                    f"{v['channel']} · {age(v['published'], now)}",
                    badge="✓ Seen" if is_seen else ("New" if fresh else ""),
                    tone="neutral" if is_seen else "info",
                    url=f"/youtube/watch/{v['id']}",
                    new_tab=True,
                    actions=[] if is_seen else [Action("Seen", f"/youtube/seen/{v['id']}")],
                    tags=("seen",) if is_seen else ("unseen", "new") if fresh else ("unseen",),
                    group=v["channel"],
                )
            )
        return WidgetData(
            value=len(new),
            summary="new in the last 24 hours, not seen yet",
            stats=[
                Stat(len(new), "New · 24 h", filter="new"),
                Stat(len(unseen), "Not seen · week", filter="unseen"),
                Stat(len(week) - len(unseen), "Seen", filter="seen"),
            ],
            list_title="This week from your subscriptions",
            group_label="channels",
            rows=rows,
            actions=[
                Action(
                    "Mark all as seen", "/youtube/seen", {"ids": " ".join(v["id"] for v in unseen)}
                )
            ]
            if unseen
            else [],
            action=Link("Open subscriptions", "https://www.youtube.com/feed/subscriptions"),
            **status,
        )

    def router(self) -> APIRouter:
        from myorb.plugins.youtube.routes import router

        return router
