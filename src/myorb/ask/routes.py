"""Ask: questions about the dashboard and actions Claude proposes, as streams of JSON lines.

POST /ask         a question; Claude answers and may propose actions
POST /ask/decide  the user confirms or cancels the proposed actions; Claude goes on
POST /ask/undo    undoes a done action
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from myorb.activity.store import record
from myorb.ask.chat import ask_question, clean_history, decide, turn
from myorb.ask.context import dashboard
from myorb.ask.conversation import Conversation, offered
from myorb.brief.writer import BriefError
from myorb.profile import load_profile
from myorb.web.routes import ordered_plugins, widget_context

logger = logging.getLogger(__name__)

router = APIRouter()


def _line(**data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode()


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _client(request: Request) -> tuple[anthropic.AsyncAnthropic, str]:
    """Claude's client and the model chosen for the brief."""
    service = request.app.state.brief
    settings = await service.settings()
    if settings is None:
        raise BriefError("Connect Claude in Integrations to ask questions.")
    return service.client(settings.api_key, settings.workspace_id), settings.model


async def _start(request: Request, history: list[dict[str, str]]) -> Conversation:
    """A new conversation: the dashboard now, the actions of the services set up, and the
    earlier turns the browser kept (e.g. after a restart) as plain text."""
    text, set_up = await dashboard(widget_context(request), ordered_plugins(request))
    actions, tools = offered(set_up)
    async with request.app.state.sessionmaker() as db:
        assistant = (await load_profile(db, request.app.state.secret_box))["assistant"]
    conversation = Conversation(dashboard=text, actions=actions, tools=tools, assistant=assistant)
    conversation.messages = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]
    request.app.state.ask_conversations.add(conversation)
    return conversation


def _stream(lines: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(lines, media_type="application/x-ndjson")


async def _guarded(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    try:
        async for event in events:
            yield _line(**event)
    except BriefError as error:
        yield _line(error=str(error))
    except Exception:
        logger.exception("Ask failed")
        yield _line(error="Something went wrong. Try again.")


@router.post("/ask")
async def ask(request: Request) -> StreamingResponse:
    """Lines of JSON: {"conversation": id}, {"text": …} pieces, {"proposal": …} for actions
    to confirm, or {"error": …}."""
    body = await _body(request)

    async def events() -> AsyncIterator[dict[str, Any]]:
        history = clean_history(body.get("messages"))
        # The question as typed (the history merges turns that do not alternate).
        question = str(body["messages"][-1]["content"])
        client, model = await _client(request)
        conversation = request.app.state.ask_conversations.get(body.get("conversation"))
        if conversation is None:
            conversation = await _start(request, history)
        yield {"conversation": conversation.id}
        ask_question(conversation, question)
        async for event in turn(client, model, conversation, widget_context(request)):
            yield event

    return _stream(_guarded(events()))


@router.post("/ask/decide")
async def decide_actions(request: Request) -> StreamingResponse:
    """The user's answer to the proposed actions: {"conversation": id, "approved": [ids]}.
    Lines of {"done": …} for each, then Claude goes on as for /ask."""
    body = await _body(request)

    async def events() -> AsyncIterator[dict[str, Any]]:
        conversation = request.app.state.ask_conversations.get(body.get("conversation"))
        if conversation is None or not conversation.pending:
            raise BriefError("These actions are no longer waiting. Ask again.")
        client, model = await _client(request)
        approved = {str(i) for i in body.get("approved") or [] if isinstance(i, str)}
        async for event in decide(widget_context(request), conversation, approved):
            yield event
        yield {"refresh": True}
        async for event in turn(client, model, conversation, widget_context(request)):
            yield event

    return _stream(_guarded(events()))


@router.post("/ask/undo")
async def undo(request: Request) -> JSONResponse:
    body = await _body(request)
    conversation = request.app.state.ask_conversations.get(body.get("conversation"))
    undo_id = body.get("id")
    action = conversation.undo.pop(undo_id, None) if conversation and undo_id else None
    if action is None:
        return JSONResponse({"error": "This can no longer be undone."}, status_code=404)
    context = widget_context(request)
    try:
        message = await action(context)
    except Exception:
        logger.exception("Undoing an Ask action failed")
        return JSONResponse({"error": "Undoing it failed."}, status_code=500)
    await record(context.sessionmaker, f"Undone: {message}", "info")
    # Claude hears of it with the next question.
    conversation.replies.append({"type": "text", "text": f"(The user undid an action: {message})"})
    return JSONResponse({"message": message})
