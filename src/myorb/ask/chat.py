"""Answering the user's questions about their day with Claude, streamed as it writes."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from myorb.brief.writer import BriefError, api_error, model_effort, model_options

logger = logging.getLogger(__name__)

SYSTEM = """You are {assistant}, the user's personal assistant on their dashboard. Answer \
their questions about their day and their work from the dashboard data below: email, \
calendar, tasks, issues, pull requests, websites and their own sources.

Work only with that data. If it does not say, answer that you do not know from the dashboard \
rather than guessing; never invent people, numbers, dates or items. Be brief and concrete, \
like a capable assistant answering their boss: the answer first, then only the details that \
help. Name items so the user can find them (sender and subject, issue key, title). Write \
times in 24-hour format. Answer in the language of the question. Use plain text: short \
paragraphs, and "- " lists when listing several items; **bold** sparingly; no headings, \
tables or links."""

# A conversation is the question and the answers before it; long ones keep the latest turns.
MAX_MESSAGES = 20
MAX_QUESTION = 2000


def clean_history(messages: Any) -> list[dict[str, str]]:
    """The conversation from the browser: alternating turns ending with the user's question."""
    if not isinstance(messages, list):
        raise BriefError("Ask a question.")
    history = [
        {"role": m["role"], "content": str(m["content"])[:8000]}
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ][-MAX_MESSAGES:]
    while history and history[0]["role"] != "user":
        history.pop(0)
    if not history or history[-1]["role"] != "user" or not history[-1]["content"].strip():
        raise BriefError("Ask a question.")
    history[-1]["content"] = history[-1]["content"][:MAX_QUESTION]
    # The API wants turns to alternate; merge any that do not (e.g. a lost answer).
    merged: list[dict[str, str]] = []
    for message in history:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] += "\n\n" + message["content"]
        else:
            merged.append(dict(message))
    return merged


async def answer(
    client: anthropic.AsyncAnthropic,
    model: str,
    dashboard: str,
    history: list[dict[str, str]],
    assistant: str,
) -> AsyncIterator[str]:
    """The answer's text as it is written. Raises BriefError with a message for the user."""
    system = [
        {"type": "text", "text": SYSTEM.replace("{assistant}", assistant)},
        # The dashboard stays the same for the whole conversation: cached after the first
        # question, so follow-ups cost a tenth for it.
        {
            "type": "text",
            "text": f"# Dashboard\n\n{dashboard}",
            "cache_control": {"type": "ephemeral"},
        },
    ]
    try:
        async with client.beta.messages.stream(
            **model_options(model),
            max_tokens=16000,
            output_config={"effort": model_effort(model)},
            system=system,
            messages=history,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
    except anthropic.AuthenticationError as error:
        raise BriefError("Claude rejected the API key. Check it in Integrations.") from error
    except anthropic.PermissionDeniedError as error:
        raise BriefError("The API key has no access to this model.") from error
    except anthropic.RateLimitError as error:
        raise BriefError("Claude is rate limited right now. Try again later.") from error
    except anthropic.APIStatusError as error:
        raise BriefError(api_error(error)) from error
    except anthropic.APIConnectionError as error:
        raise BriefError("Claude could not be reached.") from error
    usage = final.usage
    # What the question cost, for the server log: the dashboard is read from the cache after
    # the conversation's first question.
    logger.info(
        "Ask: %s input tokens (%s from the cache, %s written to it), %s output",
        usage.input_tokens,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation_input_tokens or 0,
        usage.output_tokens,
    )
    if final.stop_reason == "refusal":
        raise BriefError("Claude declined to answer this.")
    if final.stop_reason == "max_tokens":
        raise BriefError("The answer was cut off.")
