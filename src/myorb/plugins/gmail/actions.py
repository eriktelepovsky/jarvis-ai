"""What Claude may do with emails from Ask: read a conversation (to answer about it or to
reply), and save a reply as a draft in Gmail once the user confirms it. myOrb never sends a
reply: the draft waits in the conversation in Gmail, and can be undone from Ask."""

import asyncio
import re
from datetime import tzinfo
from typing import Any

from myorb.connections.google import GoogleError
from myorb.connections.google_accounts import GoogleAccount, GoogleAccounts
from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.gmail import API, API_NAME, MODIFY_SCOPE, own_addresses
from myorb.plugins.gmail.draft import conversation_text, reply_message
from myorb.plugins.gmail.message import read_message

# "email 1/18c2f0a9d3e4b5c6": the Google account's id in myOrb and the Gmail thread.
REF = re.compile(r"^\s*email\s+(\d+)/([A-Za-z0-9_-]+)\s*$")
EMAIL = {"type": "string", "description": "The email's ref, e.g. email 1/18c2f0a9d3e4b5c6."}


async def fetch_conversation(
    google: GoogleAccounts, account: GoogleAccount, thread_id: str, timezone: tzinfo
) -> tuple[list[dict[str, Any]], set[str]]:
    """The conversation's messages (oldest first) and the user's own addresses in it."""
    thread, own = await asyncio.gather(
        google.get_json(account, f"{API}/threads/{thread_id}", API_NAME, {"format": "full"}),
        own_addresses(google, account),
    )
    messages = [read_message(m, timezone) for m in thread.get("messages", [])]
    if not messages:
        raise GoogleError("This conversation has no messages.")
    return messages, own | {account.email.lower()}


async def _conversation(
    context: WidgetContext, ref: str
) -> tuple[GoogleAccount, list[dict[str, Any]], set[str]]:
    match = REF.match(ref)
    if match is None:
        raise ActionError("That is not an email's ref.")
    accounts = {a.id: a for a in await context.google.list()}
    account = accounts.get(int(match[1]))
    if account is None:
        raise ActionError("That Google account is not connected.")
    try:
        messages, own = await fetch_conversation(
            context.google, account, match[2], context.now.tzinfo
        )
    except GoogleError as error:
        raise ActionError(str(error)) from error
    return account, messages, own


async def _read_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    return "Read the email"


async def _read(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    _, messages, own = await _conversation(context, data["email"])
    return ActionResult(conversation_text(messages, own))


def _drafting(account: GoogleAccount) -> None:
    if MODIFY_SCOPE not in account.scopes:
        raise ActionError(
            f"myOrb may not save drafts for {account.email}. Connect the account again in "
            "Integrations and allow it."
        )


async def _draft_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    account, messages, _ = await _conversation(context, data["email"])
    _drafting(account)
    if not data["body"].strip():
        raise ActionError("The reply is empty.")
    newest = messages[-1]
    everyone = ", to everyone in it" if data["reply_all"] else ""
    return (
        f"Save a reply to {newest['sender']} (“{newest['subject']}”) as a Gmail draft"
        f"{everyone}. Nothing is sent.\n\n{data['body'].strip()}"
    )


async def _draft(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account, messages, own = await _conversation(context, data["email"])
    _drafting(account)
    draft = reply_message(messages[-1], data["body"], own, data["reply_all"])
    try:
        saved = await context.google.request_json(
            account, "POST", f"{API}/drafts", API_NAME, json={"message": draft}
        )
    except GoogleError as error:
        raise ActionError(str(error)) from error
    draft_id = (saved or {}).get("id")

    async def undo(context: WidgetContext) -> str:
        await context.google.request_json(account, "DELETE", f"{API}/drafts/{draft_id}", API_NAME)
        return "Removed the draft from Gmail."

    sender = messages[-1]["sender"]
    return ActionResult(
        f"Saved the reply to {sender} as a draft in Gmail; the user sends it from there.",
        undo if draft_id else None,
    )


def ask_actions() -> list[AskAction]:
    return [
        AskAction(
            "read_email",
            "Read an email conversation from the dashboard (its newest messages), to answer "
            "about it or before writing a reply. Runs without asking the user.",
            {
                "type": "object",
                "properties": {"email": EMAIL},
                "required": ["email"],
                "additionalProperties": False,
            },
            _read_summary,
            _read,
            confirm=False,
            working="Reading the email…",
        ),
        AskAction(
            "draft_reply",
            "Save a reply to an email conversation as a draft in Gmail, for the user to send "
            "from Gmail; it is never sent. Read the conversation first. Write the whole email "
            "as the user would: in the conversation's language and tone, with the greeting and "
            "closing, plain text. Never invent facts, dates or promises: put what the user must "
            "fill in [in brackets]. reply_all also copies everyone else in the email.",
            {
                "type": "object",
                "properties": {
                    "email": EMAIL,
                    "body": {"type": "string", "description": "The text of the reply."},
                    "reply_all": {"type": "boolean"},
                },
                "required": ["email", "body", "reply_all"],
                "additionalProperties": False,
            },
            _draft_summary,
            _draft,
        ),
    ]
