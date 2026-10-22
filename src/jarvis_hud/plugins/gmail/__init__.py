import asyncio
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jarvis_hud.connections.google_accounts import GoogleAccount, GoogleAccounts
from jarvis_hud.plugins.base import Plugin, Widget, WidgetContext
from jarvis_hud.plugins.google_data import load_accounts

API = "https://gmail.googleapis.com/gmail/v1/users/me"
API_NAME = "Gmail API"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
UNREAD_SHOWN = 5
# Counting today's emails stops here; more is shown as "500+".
TODAY_LIMIT = 500


def inbox_url(email: str, thread_id: str | None = None) -> str:
    base = f"https://mail.google.com/mail/u/?authuser={quote(email)}#inbox"
    return f"{base}/{thread_id}" if thread_id else base


def _header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def summarize_message(message: dict[str, Any], email: str, timezone) -> dict[str, Any]:
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
        "today": len(today.get("messages", [])),
        "today_more": "nextPageToken" in today,
        "latest_unread": [
            summarize_message(message, account.email, day_start.tzinfo) for message in messages
        ],
        "url": inbox_url(account.email),
    }


class GmailPlugin(Plugin):
    """Unread and today's emails from connected Gmail accounts."""

    id = "gmail"
    name = "Gmail"
    templates_dir = Path(__file__).parent / "templates"
    position = 10

    def widgets(self) -> list[Widget]:
        return [Widget(id="inbox", title="Email", template="inbox.html", load=self._load_inbox)]

    async def _load_inbox(self, context: WidgetContext) -> dict[str, Any]:
        day_start = context.now.replace(hour=0, minute=0, second=0, microsecond=0)

        async def fetch(account: GoogleAccount) -> dict[str, Any]:
            return await fetch_inbox(context.google, account, day_start)

        return await load_accounts(
            context,
            SCOPE,
            f"gmail:{day_start.date()}",
            fetch,
            fresh=60,
            max_age=30 * 60,
        )
