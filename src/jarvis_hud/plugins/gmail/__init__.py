import asyncio
from datetime import datetime, tzinfo
from email.utils import parseaddr
from typing import Any
from urllib.parse import quote

from jarvis_hud.connections.google_accounts import GoogleAccount, GoogleAccounts
from jarvis_hud.plugins.base import (
    BriefSection,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from jarvis_hud.plugins.google_data import account_status, load_accounts

API = "https://gmail.googleapis.com/gmail/v1/users/me"
API_NAME = "Gmail API"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
UNREAD_SHOWN = 6
# Counting today's emails stops here; more is shown as "500+".
TODAY_LIMIT = 500
ICON = "M3 6h18v12H3zM3 7l9 6 9-6"


def inbox_url(email: str, thread_id: str | None = None) -> str:
    base = f"https://mail.google.com/mail/u/?authuser={quote(email)}#inbox"
    return f"{base}/{thread_id}" if thread_id else base


def _header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def summarize_message(message: dict[str, Any], email: str, timezone: tzinfo) -> dict[str, Any]:
    name, address = parseaddr(_header(message, "From"))
    received = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone)
    return {
        "sender": name or address or "Unknown sender",
        "subject": _header(message, "Subject") or "(no subject)",
        "received": received,
        "url": inbox_url(email, message.get("threadId")),
    }


async def fetch_inbox(
    google: GoogleAccounts, account: GoogleAccount, day_start: datetime
) -> dict[str, Any]:
    inbox, today, unread = await asyncio.gather(
        google.get_json(account, f"{API}/labels/INBOX", API_NAME),
        google.get_json(
            account,
            f"{API}/messages",
            API_NAME,
            {"q": f"in:inbox after:{int(day_start.timestamp())}", "maxResults": TODAY_LIMIT},
        ),
        google.get_json(
            account,
            f"{API}/messages",
            API_NAME,
            {"q": "in:inbox is:unread", "maxResults": UNREAD_SHOWN},
        ),
    )
    messages = await asyncio.gather(
        *(
            google.get_json(
                account,
                f"{API}/messages/{message['id']}",
                API_NAME,
                {"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            )
            for message in unread.get("messages", [])
        )
    )
    return {
        "unread": inbox.get("threadsUnread", 0),
        "total": inbox.get("threadsTotal", 0),
        "today": len(today.get("messages", [])),
        "today_more": "nextPageToken" in today,
        "latest_unread": [
            summarize_message(message, account.email, day_start.tzinfo) for message in messages
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

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="inbox", label="Unread", title="Email", icon=ICON, load=self._load, weight=1.2
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
            for message in account.data["latest_unread"]:
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
                Stat(today_label, "New today"),
                Stat(unread, "Unread"),
                Stat(sum(account.data["total"] for account in loaded), "In inbox"),
            ],
            list_title="Latest unread",
            rows=[
                Row(
                    title=message["sender"],
                    subtitle=message["subject"] + (f" · {email}" if several else ""),
                    badge=received_badge(message["received"], context.now),
                    url=message["url"],
                )
                for email, message in messages
            ],
            action=Link("Open inbox", loaded[0].data["url"]),
            **status,
        )
