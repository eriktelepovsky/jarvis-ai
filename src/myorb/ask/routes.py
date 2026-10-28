"""Ask: questions about the dashboard and actions Claude proposes, as streams of JSON lines.

POST /ask         a question; Claude answers and may propose actions
POST /ask/warm    Claude reads the dashboard while the user types the first question
POST /ask/decide  the user confirms or cancels the proposed actions; Claude goes on
POST /ask/undo    undoes a done action
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import anthropic
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from myorb import demo
from myorb.activity.store import record
from myorb.ask.chat import ask_question, clean_history, decide, turn, warm
from myorb.ask.context import dashboard
from myorb.ask.conversation import Conversation, offered
from myorb.brief.writer import BriefError
from myorb.cache import refreshing
from myorb.plugins import ActionResult, AskAction, Plugin, WidgetContext
from myorb.profile import load_profile
from myorb.web.routes import ordered_plugins, widget_context

logger = logging.getLogger(__name__)

router = APIRouter()

# A link in an answer: [AR-16](L3fa2c1); see context.link_id.
LINK = re.compile(r"\]\((L[0-9a-f]{6})\)")


def _line(**data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode()


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _client(request: Request) -> tuple[Any, str]:
    """The AI's client (Claude's, or one answering as it, see brief/compatible.py) and the model
    chosen for the brief."""
    service = request.app.state.brief
    settings = await service.settings()
    if settings is None:
        raise BriefError("Connect an AI in Integrations to ask questions.")
    return service.client(settings), settings.model


def _refresh_action(plugins: list[Plugin], links: dict[str, str]) -> AskAction:
    """Loading every source again, for "refresh the data": Claude reads the new dashboard,
    its links added to the conversation's."""

    async def run(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        token = refreshing.set(True)
        try:
            text, _ = await dashboard(context, plugins, links)
        finally:
            refreshing.reset(token)
        return ActionResult(f"Every source was loaded again. The dashboard now:\n\n{text}")

    async def summary(context: WidgetContext, data: dict[str, Any]) -> str:
        return "Load new data from every source."

    return AskAction(
        name="refresh",
        description=(
            "Load new data from every source now, when the user asks to refresh or update "
            "the data. The dashboard above is from when the conversation began; this returns "
            "it as it is now, to answer from."
        ),
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        summary=summary,
        run=run,
        confirm=False,
        refresh=True,
        working="Loading new data from every source…",
    )


async def _claude_spend(request: Request, now: datetime) -> str:
    """What myOrb itself spent on Claude (the brief, Ask, replies), as Integrations shows it:
    counted from its own requests, not the organization's bill."""
    service, http = request.app.state.brief, request.app.state.http
    month_days = now.day
    today, unit = await service.spent_eur(http, 1)
    month, _ = await service.spent_eur(http, month_days)
    last_30, _ = await service.spent_eur(http, 30)
    return (
        "## myOrb's use of Claude\n"
        "What myOrb itself spent on Claude API requests (briefs, Ask, replies, summaries), "
        "counted from its own requests; not the organization's whole bill.\n"
        f"- Last 24 hours: {unit}{today:.2f}\n"
        f"- This month (last {month_days} days): {unit}{month:.2f}\n"
        f"- Last 30 days: {unit}{last_30:.2f}"
    )


async def _start(request: Request, earlier: list[dict[str, str]]) -> Conversation:
    """A new conversation: the dashboard now, the actions of the services set up (and loading
    new data), and the earlier turns the browser kept (e.g. after a restart) as plain text."""
    plugins = ordered_plugins(request)
    links: dict[str, str] = {}
    context = widget_context(request)
    text, set_up = await dashboard(context, plugins, links)
    text += "\n\n" + await _claude_spend(request, context.now)
    actions, tools = offered(set_up, [_refresh_action(plugins, links)])
    async with request.app.state.sessionmaker() as db:
        assistant = (await load_profile(db, request.app.state.secret_box))["assistant"]
    conversation = Conversation(
        dashboard=text, actions=actions, tools=tools, assistant=assistant, links=links
    )
    conversation.messages = [{"role": m["role"], "content": m["content"]} for m in earlier]
    request.app.state.ask_conversations.add(conversation)
    return conversation


def _stream(lines: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(lines, media_type="application/x-ndjson")


async def _linked(
    conversation: Conversation, events: AsyncIterator[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    """A turn's events, then where the links in its answer lead: {"links": {id: URL}}."""
    text = []
    async for event in events:
        text.append(str(event.get("text") or ""))
        yield event
    used = set(LINK.findall("".join(text)))
    if found := {i: url for i, url in conversation.links.items() if i in used}:
        yield {"links": found}


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
    if request.app.state.settings.demo:
        question = str((body.get("messages") or [{}])[-1].get("content", ""))
        return _stream(demo.answer(question, widget_context(request).now))

    async def events() -> AsyncIterator[dict[str, Any]]:
        history = clean_history(body.get("messages"))
        # The question as typed (the history merges turns that do not alternate).
        question = str(body["messages"][-1]["content"])
        client, model = await _client(request)
        conversation = request.app.state.ask_conversations.get(body.get("conversation"))
        if conversation is None:
            conversation = await _start(request, history[:-1])
        yield {"conversation": conversation.id}
        ask_question(conversation, question)
        async for event in _linked(
            conversation,
            turn(
                client,
                model,
                conversation,
                widget_context(request),
                on_usage=request.app.state.brief.record_usage,
            ),
        ):
            yield event

    return _stream(_guarded(events()))


@router.post("/ask/warm")
async def warm_up(request: Request) -> JSONResponse:
    """{"conversation": id or null, "messages": [earlier turns]} when the user is about to ask:
    the conversation (started now if the server has none) with its dashboard in Claude's
    cache. {"conversation": id}; nothing is lost when warming fails, the question just waits."""
    body = await _body(request)
    if request.app.state.settings.demo:
        return JSONResponse({"conversation": body.get("conversation")})
    try:
        client, model = await _client(request)
    except BriefError as error:
        return JSONResponse({"error": str(error)}, status_code=409)
    conversation = request.app.state.ask_conversations.get(body.get("conversation"))
    if conversation is None:
        messages = body.get("messages")
        try:
            # The earlier turns end with an answer; clean_history wants a question last.
            earlier = clean_history([*messages, {"role": "user", "content": "?"}])[:-1]
        except (BriefError, TypeError):
            earlier = []
        conversation = await _start(request, earlier)
    try:
        await warm(client, model, conversation)
    except anthropic.AnthropicError:
        logger.warning("Ask warm-up failed", exc_info=True)
    return JSONResponse({"conversation": conversation.id})


@router.post("/ask/decide")
async def decide_actions(request: Request) -> StreamingResponse:
    """The user's answer to the proposed actions: {"conversation": id, "approved": [ids]}.
    Lines of {"done": …} for each, then Claude goes on as for /ask."""
    body = await _body(request)
    if request.app.state.settings.demo:
        approved = {str(i) for i in body.get("approved") or [] if isinstance(i, str)}
        async with request.app.state.sessionmaker() as db:
            assistant = (await load_profile(db, request.app.state.secret_box))["assistant"]
        context = widget_context(request)
        return _stream(demo.decide(context.sessionmaker, approved, context.now, assistant))

    async def events() -> AsyncIterator[dict[str, Any]]:
        conversation = request.app.state.ask_conversations.get(body.get("conversation"))
        if conversation is None or not conversation.pending:
            raise BriefError("These actions are no longer waiting. Ask again.")
        client, model = await _client(request)
        approved = {str(i) for i in body.get("approved") or [] if isinstance(i, str)}
        async for event in decide(widget_context(request), conversation, approved):
            yield event
        yield {"refresh": True}
        async for event in _linked(
            conversation,
            turn(
                client,
                model,
                conversation,
                widget_context(request),
                on_usage=request.app.state.brief.record_usage,
            ),
        ):
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
