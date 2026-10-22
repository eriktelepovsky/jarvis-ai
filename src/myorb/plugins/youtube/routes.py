"""Marking videos as seen, and opening a video (which marks it seen)."""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.db import get_db
from myorb.plugins.youtube.models import SeenVideo

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
# Seen videos are kept this long; older ones are no longer listed anyway.
KEEP = timedelta(days=30)
REFRESH = {"HX-Trigger": "myorb:refresh"}


async def mark_seen(db: AsyncSession, video_ids: list[str]) -> None:
    now = datetime.now(UTC)
    for video_id in video_ids:
        if VIDEO_ID.match(video_id):
            await db.execute(
                insert(SeenVideo).values(video_id=video_id, seen_at=now).on_conflict_do_nothing()
            )
    await db.execute(delete(SeenVideo).where(SeenVideo.seen_at < now - KEEP))
    await db.commit()


@router.get("/youtube/watch/{video_id}")
async def watch(db: Db, video_id: str) -> Response:
    """Opens the video on YouTube and counts it as seen."""
    if not VIDEO_ID.match(video_id):
        raise HTTPException(status_code=404)
    await mark_seen(db, [video_id])
    return RedirectResponse(f"https://www.youtube.com/watch?v={video_id}", status_code=303)


@router.post("/youtube/seen/{video_id}")
async def seen(db: Db, video_id: str) -> Response:
    await mark_seen(db, [video_id])
    return Response(status_code=204, headers=REFRESH)


@router.post("/youtube/seen")
async def seen_all(db: Db, ids: Annotated[str, Form()] = "") -> Response:
    """Mark all as seen: the videos listed when the details were loaded."""
    await mark_seen(db, ids.split()[:500])
    return Response(status_code=204, headers=REFRESH)
