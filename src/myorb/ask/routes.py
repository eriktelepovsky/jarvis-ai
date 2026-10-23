"""POST /ask: a question about the dashboard, answered by Claude as a stream of JSON lines."""

import json
import logging
import secrets
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from myorb.ask.chat import answer, clean_history
from myorb.ask.context import dashboard
from myorb.brief.writer import BriefError
from myorb.profile import load_profile
from myorb.web.routes import ordered_plugins, widget_context

logger = logging.getLogger(__name__)

router = APIRouter()

# Each conversation keeps the dashboard as it was at its first question, for an hour.
KEEP = 60 * 60
MAX_CONVERSATIONS = 50


def _line(**data: str) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode()


async def _dashboard(request: Request, conversation: str | None) -> tuple[str, str]:
    """The conversation's id and its dashboard: the same one, or a new one built now."""
    store: dict[str, tuple[float, str]] = request.app.state.ask_conversations
    now = time.monotonic()
    for key in [k for k, (at, _) in store.items() if now - at > KEEP]:
        del store[key]
    if conversation and conversation in store:
        return conversation, store[conversation][1]
    text = await dashboard(widget_context(request), ordered_plugins(request))
    while len(store) >= MAX_CONVERSATIONS:
        del store[min(store, key=lambda k: store[k][0])]
    conversation = secrets.token_urlsafe(12)
    store[conversation] = (now, text)
    return conversation, text


@router.post("/ask")
async def ask(request: Request) -> StreamingResponse:
    """Lines of JSON: {"conversation": id}, then {"text": …} pieces, or {"error": …}."""
    try:
        body = await request.json()
    except ValueError:
        body = {}

    async def lines() -> AsyncIterator[bytes]:
        try:
            history = clean_history(body.get("messages") if isinstance(body, dict) else None)
            service = request.app.state.brief
            settings = await service.settings()
            if settings is None:
                raise BriefError("Connect Claude in Integrations to ask questions.")
            conversation, text = await _dashboard(request, body.get("conversation"))
            yield _line(conversation=conversation)
            async with request.app.state.sessionmaker() as db:
                assistant = (await load_profile(db, request.app.state.secret_box))["assistant"]
            client = service.client(settings.api_key, settings.workspace_id)
            async for piece in answer(client, settings.model, text, history, assistant):
                yield _line(text=piece)
        except BriefError as error:
            yield _line(error=str(error))
        except Exception:
            logger.exception("Ask failed")
            yield _line(error="Something went wrong. Try again.")

    return StreamingResponse(lines(), media_type="application/x-ndjson")
