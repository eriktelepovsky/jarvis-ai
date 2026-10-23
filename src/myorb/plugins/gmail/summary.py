"""The AI summary of unread emails: what needs the user, what is worth a look, what can wait."""

import json
from datetime import datetime
from typing import Any

import anthropic

from myorb.brief.writer import LANGUAGES, BriefError, api_error, model_options

# The newest unread emails summarized; older ones are mentioned only as a count.
MAX_EMAILS = 60
PRIORITIES = ("high", "medium", "low")

SYSTEM = """You are {assistant}, the user's personal assistant. Triage their unread emails: \
say what needs them and what can wait, so they know what to open first.

You get each email's index, sender, subject, the address it was sent to, when it arrived and \
the start of its text. Work only with that; never invent content.

- overview: one or two short sentences on the whole inbox, the most important first, e.g. \
"Two clients wait for a reply; the rest is newsletters and receipts."
- items: every email, by its index, with a priority: "high" for a person waiting for the \
user's reply or decision, a deadline, a payment or something broken; "medium" for things \
worth reading soon; "low" for newsletters, notifications, receipts and marketing. reason is \
one short clause (at most 12 words) on why; action is at most two words, e.g. "Reply", \
"Pay", "Approve", "Read", "Archive"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "priority": {"type": "string", "enum": list(PRIORITIES)},
                    "reason": {"type": "string"},
                    "action": {"type": "string"},
                },
                "required": ["index", "priority", "reason", "action"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "items"],
    "additionalProperties": False,
}


def emails_prompt(emails: list[dict[str, Any]], now: datetime, language: str) -> str:
    lines = [
        f"Now: {now:%A %d %B %Y, %H:%M}.",
        f"Write the overview and reasons in {LANGUAGES.get(language, 'English')}.",
        "",
    ]
    for index, email in enumerate(emails[:MAX_EMAILS]):
        received = email["received"].astimezone(now.tzinfo)
        lines.append(
            f"[{index}] From: {email['sender']} | To: {email.get('to', '')} | "
            f"{received:%d %b %H:%M} | Subject: {email['subject']}\n"
            f"    {email.get('snippet', '')[:300]}"
        )
    if len(emails) > MAX_EMAILS:
        lines.append(f"(and {len(emails) - MAX_EMAILS} older unread emails)")
    return "\n".join(lines)


async def summarize(
    client: anthropic.AsyncAnthropic,
    model: str,
    emails: list[dict[str, Any]],
    now: datetime,
    language: str,
    assistant: str,
) -> dict[str, Any]:
    """{"overview": str, "items": [{"email": {...}, "priority", "reason", "action"}]}."""
    try:
        response = await client.beta.messages.create(
            **model_options(model),
            max_tokens=16000,
            # Triage is a light task: low effort keeps it quick and cheap.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM.replace("{assistant}", assistant),
            messages=[{"role": "user", "content": emails_prompt(emails, now, language)}],
        )
    except anthropic.AuthenticationError as error:
        raise BriefError("Claude rejected the API key. Check it in Integrations.") from error
    except anthropic.RateLimitError as error:
        raise BriefError("Claude is rate limited right now. Try again later.") from error
    except anthropic.APIStatusError as error:
        raise BriefError(api_error(error)) from error
    except anthropic.APIConnectionError as error:
        raise BriefError("Claude could not be reached.") from error
    if response.stop_reason == "refusal":
        raise BriefError("Claude declined to summarize these emails.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise BriefError("Claude's answer could not be read. Try again.") from error
    shown = emails[:MAX_EMAILS]
    items = []
    for item in data.get("items", []):
        index = item.get("index")
        if isinstance(index, int) and 0 <= index < len(shown):
            priority = item.get("priority") if item.get("priority") in PRIORITIES else "low"
            items.append(
                {
                    "email": shown[index],
                    "priority": priority,
                    "reason": str(item.get("reason", "")).strip(),
                    "action": str(item.get("action", "")).strip()[:20],
                }
            )
    return {"overview": str(data.get("overview", "")).strip(), "items": items}
