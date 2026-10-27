"""Ignoring an event in the calendar's details, and counting it again."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.db import get_db
from myorb.plugins.calendar.models import IgnoredEvent

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
REFRESH = {"HX-Trigger": "myorb:refresh"}


def _checked(key: str) -> str:
    if not 2 < len(key) <= 1100 or "/" not in key:
        raise HTTPException(status_code=400, detail="That is not an event.")
    return key


@router.post("/calendar/ignore")
async def ignore(db: Db, key: str) -> Response:
    await db.execute(
        insert(IgnoredEvent)
        .values(key=_checked(key), ignored_at=datetime.now(UTC))
        .on_conflict_do_nothing()
    )
    await db.commit()
    return Response(status_code=204, headers=REFRESH)


@router.post("/calendar/count")
async def count_again(db: Db, key: str) -> Response:
    await db.execute(delete(IgnoredEvent).where(IgnoredEvent.key == _checked(key)))
    await db.commit()
    return Response(status_code=204, headers=REFRESH)
