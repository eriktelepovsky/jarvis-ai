"""One email conversation as myOrb shows it: who wrote what, the text, and the attachments.

An email's HTML is shown in a sandboxed frame (no scripts) whose content policy blocks
everything from the internet until the user asks for images: they also tell the sender
that the email was opened. Images sent inside the email (cid: addresses) tell nobody
anything and are always shown, embedded as data: addresses.
"""

import base64
import binascii
import html
import io
import mimetypes
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
    "@media (max-width:480px){body{padding:10px}}"
)
# Larger images sent inside an email are left out rather than put into the page.
MAX_INLINE = 5_000_000


# Notification emails about something that lives elsewhere, by the sender's domain, and the
# link to it in their text: a Sentry issue, a Bitbucket pipeline. Ask links to it directly.
NOTIFIERS = ("sentry.io", "getsentry.com", "bitbucket.org")
ITEM_LINK = re.compile(
    r"https://(?:[\w-]+\.)?sentry\.io/(?:organizations/[\w-]+/)?issues/\d+/?"
    r"|https://bitbucket\.org/[\w.-]+/[\w.-]+/"
    r"(?:addon/pipelines/home#!/results/|pipelines/results/)[^\s\"'<>/]+"
)


def is_notifier(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in NOTIFIERS)


def item_link(message: dict[str, Any]) -> str:
    """The link to what a notification is about, from a message fetched with format=full."""
    for part in message_parts(message.get("payload", {})):
        data = part.get("body", {}).get("data")
        if part.get("mimeType") in ("text/plain", "text/html") and data:
            if found := ITEM_LINK.search(_decode(data)):
                return html.unescape(found.group(0))
    return ""


# Attachments the browser shows itself; anything else is downloaded. Never HTML or SVG: from
# myOrb's address, their scripts would run as myOrb.
SHOWN_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "text/plain",
}
# The width of an attachment's picture on its card, in pixels (twice what the card shows).
THUMBNAIL_WIDTH = 360


def attachment_type(part: dict[str, Any]) -> str:
    """An attachment's media type, from its name when the email does not say."""
    kind = (part.get("mimeType") or "").lower()
    if kind in ("", "application/octet-stream"):
        kind = (mimetypes.guess_type(part.get("filename", ""))[0] or kind or "").lower()
    return kind or "application/octet-stream"


def attachment_label(name: str, kind: str) -> str:
    """The short type on an attachment's card, e.g. "PDF" or "XLSX"."""
    if kind == "application/pdf":
        return "PDF"
    if kind.startswith("image/"):
        return "IMG"
    extension = name.rpartition(".")[2] if "." in name else ""
    return extension[:4].upper() or "FILE"


def thumbnail(kind: str, content: bytes) -> bytes:
    """A PNG of a PDF's first page or of an image, THUMBNAIL_WIDTH wide; ValueError for any
    other attachment or one that cannot be read."""
    from PIL import Image, UnidentifiedImageError

    if kind == "application/pdf":
        import pypdfium2

        try:
            pdf = pypdfium2.PdfDocument(content)
            page = pdf[0]
            picture = page.render(scale=THUMBNAIL_WIDTH / page.get_width()).to_pil()
        except (pypdfium2.PdfiumError, IndexError) as error:
            raise ValueError("The PDF could not be read.") from error
    elif kind.startswith("image/") and kind != "image/svg+xml":
        try:
            picture = Image.open(io.BytesIO(content))
            picture.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError("The image could not be read.") from error
    else:
        raise ValueError("No preview for this kind of attachment.")
    out = io.BytesIO()
    picture.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def message_parts(part: dict[str, Any]):
    yield part
    for child in part.get("parts", []) or []:
        yield from message_parts(child)


def _decode(data: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _part_header(part: dict[str, Any], name: str) -> str:
    headers = part.get("headers", []) or []
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _inline(parts: list[dict[str, Any]], body_html: str) -> list[dict[str, Any]]:
    """The images the HTML shows from inside the email, by their cid: address."""
    found = []
    for part in parts:
        cid = (_part_header(part, "Content-ID") or _part_header(part, "X-Attachment-Id")).strip()
        cid = cid.removeprefix("<").removesuffix(">")
        if not cid or not part.get("mimeType", "").startswith("image/"):
            continue
        if f"cid:{cid}".lower() not in body_html.lower():
            continue
        body = part.get("body", {})
        found.append(
            {
                "cid": cid,
                "mime": part["mimeType"],
                "attachment_id": body.get("attachmentId", ""),
                "size": body.get("size", 0),
                # Small ones come with the message; the others are fetched (attachments.get).
                "data": body.get("data", ""),
            }
        )
    return found


def embed_inline(body_html: str, inline: list[dict[str, Any]]) -> str:
    """The HTML with each cid: address it can fill replaced by the image as a data: address."""
    images = {}
    for image in inline:
        if image["data"]:
            data = image["data"].replace("-", "+").replace("_", "/").rstrip("=")
            data += "=" * (-len(data) % 4)
            images[image["cid"].lower()] = f"data:{image['mime']};base64,{data}"

    def swap(match: re.Match) -> str:
        return images.get(match.group(1).lower(), match.group(0))

    return re.sub(r"cid:([^\"'\s)>]+)", swap, body_html, flags=re.I) if images else body_html


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
    parts = list(message_parts(payload))
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
    inline = _inline(parts, body_html)
    shown_inside = {image["attachment_id"] for image in inline if image["attachment_id"]}
    name, address = parseaddr(_header(message, "From"))
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "sender": name or address or "Unknown sender",
        "address": address,
        # As sent, for a reply (see draft.reply_message).
        "from": _header(message, "From"),
        "reply_to": _header(message, "Reply-To"),
        "to_raw": _header(message, "To"),
        "cc_raw": _header(message, "Cc"),
        "message_id": _header(message, "Message-ID"),
        "references": _header(message, "References"),
        "to": _people(_header(message, "To")),
        "cc": _people(_header(message, "Cc")),
        "subject": _header(message, "Subject") or "(no subject)",
        "received": datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone),
        "unread": "UNREAD" in message.get("labelIds", []),
        "starred": "STARRED" in message.get("labelIds", []),
        "html": body_html,
        "text": text or html.unescape(message.get("snippet", "")),
        "inline": inline,
        # Images shown in the email are not listed again, as in Gmail.
        # Each by its part of the message, which stays the same (Gmail's attachment ids do not).
        "attachments": [
            {
                "name": p["filename"],
                "size": p.get("body", {}).get("size", 0),
                "part": p.get("partId", ""),
                "label": attachment_label(p["filename"], attachment_type(p)),
                "preview": attachment_type(p) == "application/pdf"
                or (attachment_type(p) in SHOWN_TYPES and attachment_type(p).startswith("image/")),
            }
            for p in parts
            if p.get("filename")
            and p.get("body", {}).get("attachmentId")
            and p["body"]["attachmentId"] not in shown_inside
        ],
    }


def size_label(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, round(size / 1000))} kB"
