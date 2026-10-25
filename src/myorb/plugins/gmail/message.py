"""One email conversation as myOrb shows it: who wrote what, the text, and the attachments.

An email's HTML is shown in a sandboxed frame (no scripts) whose content policy blocks
everything from the internet until the user asks for images: they also tell the sender
that the email was opened.
"""

import base64
import binascii
import html
import re
from datetime import datetime, tzinfo
from email.utils import getaddresses, parseaddr
from typing import Any

IMAGES_BLOCKED = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
IMAGES_ALLOWED = "default-src 'none'; style-src 'unsafe-inline' https:; img-src data: https: http:"
# The frame never scrolls up and down itself: the page sizes it to its content. A wide email
# still scrolls sideways.
FRAME_STYLE = (
    "html{overflow-y:hidden!important;overflow-x:auto}"
    "body{margin:0;padding:18px;font:15px/1.55 -apple-system,system-ui,sans-serif;color:#1d2125;"
    "background:#fff;overflow-wrap:anywhere}img{max-width:100%;height:auto}"
    "table{max-width:100%}a{color:#0b6bcb}"
)


def _parts(part: dict[str, Any]):
    yield part
    for child in part.get("parts", []) or []:
        yield from _parts(child)


def _decode(data: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _people(value: str) -> str:
    return ", ".join(name or address for name, address in getaddresses([value]) if name or address)


def frame_document(body_html: str, images: bool) -> str:
    """The email's HTML as the frame's document: its own styles, links opening a new tab."""
    policy = IMAGES_ALLOWED if images else IMAGES_BLOCKED
    # A refresh would take the frame to another page; scripts cannot run in it anyway.
    body_html = re.sub(r"<meta[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>", "", body_html, flags=re.I)
    return (
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
        f'content="{policy}"><base target="_blank"><style>{FRAME_STYLE}</style>{body_html}'
    )


def read_message(message: dict[str, Any], timezone: tzinfo) -> dict[str, Any]:
    """One message of a thread fetched with format=full."""
    payload = message.get("payload", {})
    parts = list(_parts(payload))
    body_html = next(
        (
            _decode(p["body"]["data"])
            for p in parts
            if p.get("mimeType") == "text/html" and p.get("body", {}).get("data")
        ),
        "",
    )
    text = next(
        (
            _decode(p["body"]["data"])
            for p in parts
            if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data")
        ),
        "",
    )
    name, address = parseaddr(_header(message, "From"))
    return {
        "id": message.get("id", ""),
        "sender": name or address or "Unknown sender",
        "address": address,
        "to": _people(_header(message, "To")),
        "cc": _people(_header(message, "Cc")),
        "subject": _header(message, "Subject") or "(no subject)",
        "received": datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone),
        "unread": "UNREAD" in message.get("labelIds", []),
        "html": body_html,
        "text": text or html.unescape(message.get("snippet", "")),
        "attachments": [
            {"name": p["filename"], "size": p.get("body", {}).get("size", 0)}
            for p in parts
            if p.get("filename") and p.get("body", {}).get("attachmentId")
        ],
    }


def size_label(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, round(size / 1000))} kB"
