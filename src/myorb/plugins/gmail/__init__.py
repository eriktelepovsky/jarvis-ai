import asyncio
import html
from datetime import datetime, tzinfo
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from myorb.connections import google as google_oauth
from myorb.connections.google_accounts import GoogleAccount, GoogleAccounts
from myorb.plugins.base import (
    BriefSection,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
    open_buttons,
)
from myorb.plugins.google_data import account_status, load_accounts

if TYPE_CHECKING:
    from fastapi import APIRouter

API = "https://gmail.googleapis.com/gmail/v1/users/me"
API_NAME = "Gmail API"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# Unread conversations listed in the details (with search), as Gmail counts them: each by
# its newest unread email. The brief gets the newest BRIEF_UNREAD.
UNREAD_SHOWN = 100
BRIEF_UNREAD = 15
# Message headers are read this many at a time, to stay within Gmail's rate limits.
PARALLEL = 10
# Counting today's emails stops here; more is shown as "500+".
TODAY_LIMIT = 500
ICON = "M3 6h18v12H3zM3 7l9 6 9-6"


def inbox_url(email: str, thread_id: str | None = None) -> str:
    base = f"https://mail.google.com/mail/u/?authuser={quote(email)}#inbox"
    return f"{base}/{thread_id}" if thread_id else base


def _header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


RECIPIENT_HEADERS = ["To", "Cc", "X-Original-To", "Delivered-To"]


def recipient(message: dict[str, Any], email: str, own: set[str]) -> str:
    """The address the email was sent to, e.g. one of the user's other mailboxes that Gmail
    collects: the first of the user's own addresses (`own`, Gmail's "Send mail as") among
    the recipients; else where it was originally sent (a group address, a list)."""
    headers = message.get("payload", {}).get("headers", [])

    def addresses(*names: str) -> list[str]:
        values = [h["value"] for name in names for h in headers if h["name"].lower() == name]
        return [a.lower() for _, a in getaddresses(values) if "@" in a]

    # The recipients first: Delivered-To is mostly the Gmail address itself.
    for address in addresses("to", "cc", "x-original-to") + addresses("delivered-to"):
        if address in own:
            return address
    found = addresses("x-original-to") + addresses("to")
    return found[0] if found else email.lower()


def summarize_message(
    message: dict[str, Any], email: str, timezone: tzinfo, own: set[str] = frozenset()
) -> dict[str, Any]:
    name, address = parseaddr(_header(message, "From"))
    received = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone)
    return {
        "sender": name or address or "Unknown sender",
        "subject": _header(message, "Subject") or "(no subject)",
        "received": received,
        "url": inbox_url(email, message.get("threadId")),
        "to": recipient(message, email, own or {email.lower()}),
        # The start of the text, as Gmail shows it in the list: for the AI summary.
        "snippet": html.unescape(message.get("snippet", "")),
        # Gmail's own "important" marker, which it learns from what the user reads and answers.
        "important": "IMPORTANT" in message.get("labelIds", []),
    }


async def own_addresses(google: GoogleAccounts, account: GoogleAccount) -> set[str]:
    """The user's addresses in this Gmail ("Send mail as"), e.g. other mailboxes it collects."""
    try:
        data = await google.get_json(account, f"{API}/settings/sendAs", API_NAME)
    except google_oauth.GoogleError:
        return set()  # the emails are then grouped by where they were sent
    return {s["sendAsEmail"].lower() for s in data.get("sendAs", []) if s.get("sendAsEmail")}


async def fetch_inbox(
    google: GoogleAccounts, account: GoogleAccount, day_start: datetime
) -> dict[str, Any]:
    own, inbox, today, unread = await asyncio.gather(
        own_addresses(google, account),
        google.get_json(account, f"{API}/labels/INBOX", API_NAME),
        google.get_json(
            account,
            f"{API}/messages",
            API_NAME,
            {"q": f"in:inbox after:{int(day_start.timestamp())}", "maxResults": TODAY_LIMIT},
        ),
        google.get_json(
            account,
            f"{API}/threads",
            API_NAME,
            {"q": "in:inbox is:unread", "maxResults": UNREAD_SHOWN},
        ),
    )
    limit = asyncio.Semaphore(PARALLEL)

    async def newest_unread(thread_id: str) -> dict[str, Any] | None:
        async with limit:
            thread = await google.get_json(
                account,
                f"{API}/threads/{thread_id}",
                API_NAME,
                {"format": "metadata", "metadataHeaders": ["From", "Subject", *RECIPIENT_HEADERS]},
            )
        messages = thread.get("messages") or []
        unread_ones = [m for m in messages if "UNREAD" in m.get("labelIds", [])]
        return (unread_ones or messages or [None])[-1]

    found = await asyncio.gather(*(newest_unread(t["id"]) for t in unread.get("threads", [])))
    messages = [message for message in found if message]
    return {
        "unread": inbox.get("threadsUnread", 0),
        # More unread conversations than were read: counts from them are a lower bound.
        "unread_more": "nextPageToken" in unread,
        "today": len(today.get("messages", [])),
        "today_more": "nextPageToken" in today,
        "latest_unread": [
            summarize_message(message, account.email, day_start.tzinfo, own) for message in messages
        ],
        "url": inbox_url(account.email),
    }


def received_badge(received: datetime, now: datetime) -> str:
    return received.strftime("%H:%M" if received.date() == now.date() else "%-d %b")


class GmailPlugin(Plugin):
    """Unread and today's emails from connected Gmail accounts."""

    id = "gmail"
    name = "Gmail"
    position = 10
    templates_dir = Path(__file__).parent / "templates"

    def router(self) -> "APIRouter":
        from myorb.plugins.gmail.routes import router

        return router

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="inbox",
                label="Unread",
                title="Email",
                icon=ICON,
                load=self._load,
                weight=1.2,
                refresh=2 * 60,
            )
        ]

    async def _accounts(self, context: WidgetContext) -> list:
        day_start = context.now.replace(hour=0, minute=0, second=0, microsecond=0)

        async def fetch(account: GoogleAccount) -> dict[str, Any]:
            return await fetch_inbox(context.google, account, day_start)

        result = await load_accounts(
            context, SCOPE, f"gmail:{day_start.date()}", fetch, fresh=60, max_age=30 * 60
        )
        return result["accounts"]

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [a for a in await self._accounts(context) if a.data is not None]
        if not loaded:
            return None
        lines = [
            f"{sum(a.data['unread'] for a in loaded)} unread conversations in the inbox, "
            f"{sum(a.data['today'] for a in loaded)} emails received today."
        ]
        for account in loaded:
            for message in account.data["latest_unread"][:BRIEF_UNREAD]:
                received = received_badge(message["received"], context.now)
                lines.append(f"Unread from {message['sender']}: {message['subject']} ({received})")
        return BriefSection("Email (Gmail)", lines)

    async def _load(self, context: WidgetContext) -> WidgetData:
        accounts = await self._accounts(context)
        status = account_status(accounts, "Gmail")
        loaded = [account for account in accounts if account.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        unread = sum(account.data["unread"] for account in loaded)
        today = sum(account.data["today"] for account in loaded)
        today_label = f"{today}+" if any(a.data["today_more"] for a in loaded) else str(today)
        # Among the unread conversations read (up to UNREAD_SHOWN per account), those Gmail
        # marks as important; with more unread than that, at least this many. Data cached by
        # an earlier version has no marker and counts none.
        important = sum(
            message.get("important", False)
            for account in loaded
            for message in account.data["latest_unread"]
        )
        more = any(account.data.get("unread_more", False) for account in loaded)
        important_label = f"{important}+" if more else str(important)
        messages = sorted(
            (
                (account.email, message)
                for account in loaded
                for message in account.data["latest_unread"]
            ),
            key=lambda item: item[1]["received"],
            reverse=True,
        )[:UNREAD_SHOWN]
        several = len(accounts) > 1
        return WidgetData(
            value=unread,
            summary=f"unread · {today_label} new today",
            stats=[
                # The count is every email today, read or not; the list it narrows is unread.
                Stat(today_label, "New today", filter="today"),
                Stat(unread, "Unread"),
                Stat(important_label, "Important", filter="important"),
            ],
            list_title="Latest unread",
            rows=[
                Row(
                    title=message["sender"],
                    subtitle=message["subject"] + (f" · {email}" if several else ""),
                    badge=received_badge(message["received"], context.now),
                    url=message["url"],
                    tags=tuple(
                        tag
                        for tag, applies in (
                            ("important", message.get("important")),
                            ("today", message["received"].date() == context.now.date()),
                        )
                        if applies
                    ),
                    # By the address it was sent to; with several accounts, under the account.
                    group=email if several else message.get("to", email),
                    subgroup=message.get("to", email) if several else "",
                )
                for email, message in messages
            ],
            group_label="accounts" if several else "recipients",
            details_url="/email/summary",
            **open_buttons("Open inbox", [(a.email, a.data["url"]) for a in loaded]),
            **status,
        )
