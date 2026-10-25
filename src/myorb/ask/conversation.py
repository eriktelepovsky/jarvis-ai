"""A conversation in Ask, kept on the server: the dashboard it started with, the actions
Claude may propose, the messages so far (as the API wants them back), and what waits for the
user: proposed actions to confirm, done ones to undo."""

import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from myorb.plugins import AskAction, Plugin, WidgetContext

# Kept for an hour after the last question; at most this many at once.
KEEP = 60 * 60
MAX_CONVERSATIONS = 50


@dataclass
class Conversation:
    dashboard: str
    actions: dict[str, AskAction]
    tools: list[dict[str, Any]]
    assistant: str
    id: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    at: float = field(default_factory=time.monotonic)
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Proposed actions waiting for the user: tool_use id -> (tool name, input).
    pending: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    # Answers for tool calls that need no confirmation (e.g. an unknown action), sent along.
    replies: list[dict[str, Any]] = field(default_factory=list)
    # Done actions that can be undone: id -> undo.
    undo: dict[str, Callable[[WidgetContext], Awaitable[str]]] = field(default_factory=dict)


def tool_name(plugin: Plugin, action: AskAction) -> str:
    return f"{plugin.id}__{action.name}"


def offered(
    plugins: list[Plugin], own: Sequence[AskAction] = ()
) -> tuple[dict[str, AskAction], list[dict[str, Any]]]:
    """The actions of these plugins and myOrb's `own` (as "myorb__…"), by tool name, and their
    tool definitions for the API."""
    actions: dict[str, AskAction] = {}
    tools: list[dict[str, Any]] = []
    named = [(tool_name(p, a), a) for p in plugins for a in p.ask_actions()]
    for name, action in [*named, *((f"myorb__{a.name}", a) for a in own)]:
        actions[name] = action
        tools.append(
            {
                "name": name,
                "description": action.description,
                "input_schema": action.schema,
                "strict": True,
            }
        )
    return actions, tools


class Conversations:
    def __init__(self) -> None:
        self._all: dict[str, Conversation] = {}

    def get(self, conversation_id: Any) -> Conversation | None:
        self._forget_old()
        conversation = self._all.get(conversation_id) if isinstance(conversation_id, str) else None
        if conversation is not None:
            conversation.at = time.monotonic()
        return conversation

    def add(self, conversation: Conversation) -> None:
        self._forget_old()
        while len(self._all) >= MAX_CONVERSATIONS:
            del self._all[min(self._all, key=lambda k: self._all[k].at)]
        self._all[conversation.id] = conversation

    def _forget_old(self) -> None:
        now = time.monotonic()
        for key in [k for k, c in self._all.items() if now - c.at > KEEP]:
            del self._all[key]
