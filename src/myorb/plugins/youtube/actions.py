"""What Claude may do with the videos in Ask: mark them as seen, after the user confirms."""

from typing import Any

from sqlalchemy import delete

from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.youtube.models import SeenVideo
from myorb.plugins.youtube.routes import VIDEO_ID, mark_seen


def _ids(data: dict[str, Any]) -> list[str]:
    ids = [i for i in dict.fromkeys(data["video_ids"]) if VIDEO_ID.match(i)]
    if not ids:
        raise ActionError("Name the videos by their ref (video …).")
    return ids


async def _summary(context: WidgetContext, data: dict[str, Any]) -> str:
    count = len(_ids(data))
    return f"Mark {count} video{'s' if count != 1 else ''} as seen"


async def _mark(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    ids = _ids(data)
    async with context.sessionmaker() as db:
        await mark_seen(db, ids)

    async def undo(context: WidgetContext) -> str:
        async with context.sessionmaker() as db:
            await db.execute(delete(SeenVideo).where(SeenVideo.video_id.in_(ids)))
            await db.commit()
        return f"{len(ids)} video{'s' if len(ids) != 1 else ''} not seen again."

    return ActionResult(f"Marked {len(ids)} video{'s' if len(ids) != 1 else ''} as seen.", undo)


def ask_actions() -> list[AskAction]:
    return [
        AskAction(
            "mark_videos_seen",
            "Mark YouTube videos from the user's subscriptions as seen, so they no longer count "
            "as new. Pass the ids from their refs (video abc123 → abc123).",
            {
                "type": "object",
                "properties": {"video_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["video_ids"],
                "additionalProperties": False,
            },
            _summary,
            _mark,
        )
    ]
