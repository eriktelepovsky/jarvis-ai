"""Asks Claude to write the brief from the facts plugins collected."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic

from jarvis_hud.plugins import BriefSection


@dataclass(frozen=True)
class Model:
    name: str
    note: str
    effort: str
    # Whether the API retries a safety decline on another model (not available for Haiku).
    fallback: bool


MODELS = {
    "claude-sonnet-5-5": Model("Claude Sonnet 5.5", "balanced (recommended)", "medium", True),
    "claude-opus-5-5": Model("Claude Opus 5.5", "most capable", "medium", True),
    "claude-haiku-5-5": Model("Claude Haiku 5.5", "cheapest", "low", False),
}
DEFAULT_MODEL = "claude-sonnet-5-5"


def model_name(model: str) -> str:
    """A readable name; a fallback model that wrote the brief may not be in MODELS."""
    return MODELS[model].name if model in MODELS else model


TONES = ["fail", "warn", "info", "ok", "neutral"]
MAX_PRIORITIES = 5
LANGUAGES = {"en": "English", "sk": "Slovak"}

SYSTEM = """You are Jarvis, a personal assistant. You write the user's daily brief: a short report \
they read on their dashboard to know what their day holds and what needs them first.

Work only with the facts you are given. Never invent meetings, people, numbers or deadlines; if \
something is unknown, leave it out. Decide what matters: people waiting for a reply, meetings \
coming up soon, overdue tasks and today's deadlines come before newsletters, receipts and \
notifications. Say it plainly and briefly, like a capable assistant talking to their boss. \
Address the user directly as "you". Do not use the user's name. Write times in 24-hour format.

Fields:
- headline: one short sentence (at most 60 characters) shown in the center of the dashboard, \
e.g. "Three things need you before 11:00." or "A quiet day with one deadline."
- summary: one to three short paragraphs, the most important first.
- priorities: at most five items that need the user, most urgent first. tag is a very short \
label (at most 10 characters) such as a time ("09:30"), "Reply", "Overdue" or "Today". tone is \
"fail" for overdue or broken things, "warn" for things waiting on the user, "info" for scheduled \
things, "ok" for good news, "neutral" otherwise. title names the item; detail adds one short \
clause of context. Leave the list empty if nothing needs the user."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "array", "items": {"type": "string"}},
        "priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "tone": {"type": "string", "enum": TONES},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["tag", "tone", "title", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "summary", "priorities"],
    "additionalProperties": False,
}


class BriefError(Exception):
    """Writing the brief failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class Written:
    content: dict[str, Any]
    model: str


def facts_prompt(sections: list[BriefSection], now: datetime, language: str) -> str:
    parts = [
        f"Now: {now:%A %d %B %Y, %H:%M} ({now.tzinfo}).",
        f"Write the brief in {LANGUAGES.get(language, 'English')}.",
    ]
    for section in sections:
        parts.append(f"\n## {section.title}\n" + "\n".join(f"- {line}" for line in section.lines))
    return "\n".join(parts)


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    priorities = [p for p in data.get("priorities", []) if isinstance(p, dict)][:MAX_PRIORITIES]
    for priority in priorities:
        if priority.get("tone") not in TONES:
            priority["tone"] = "neutral"
    return {
        "headline": str(data.get("headline", "")).strip(),
        "summary": [str(p).strip() for p in data.get("summary", []) if str(p).strip()],
        "priorities": priorities,
    }


async def write_brief(
    client: anthropic.AsyncAnthropic,
    sections: list[BriefSection],
    now: datetime,
    language: str,
    model: str = DEFAULT_MODEL,
) -> Written:
    settings = MODELS.get(model, MODELS[DEFAULT_MODEL])
    # On a safety decline the API retries on the model Anthropic recommends.
    fallback: dict[str, Any] = (
        {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        if settings.fallback
        else {}
    )
    try:
        response = await client.beta.messages.create(
            model=model if model in MODELS else DEFAULT_MODEL,
            max_tokens=16000,
            **fallback,
            output_config={
                "effort": settings.effort,
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content": facts_prompt(sections, now, language)}],
        )
    except anthropic.AuthenticationError as error:
        raise BriefError("Claude rejected the API key. Check it in Integrations.") from error
    except anthropic.PermissionDeniedError as error:
        raise BriefError("The API key has no access to this model.") from error
    except anthropic.RateLimitError as error:
        raise BriefError("Claude is rate limited right now. Try again later.") from error
    except anthropic.APIStatusError as error:
        raise BriefError(f"Claude returned an error (HTTP {error.status_code}).") from error
    except anthropic.APIConnectionError as error:
        raise BriefError("Claude could not be reached.") from error

    if response.stop_reason == "refusal":
        raise BriefError("Claude declined to write the brief.")
    if response.stop_reason == "max_tokens":
        raise BriefError("The brief was cut off. Try again.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise BriefError("Claude's answer could not be read. Try again.") from error
    return Written(content=_clean(data), model=response.model)
