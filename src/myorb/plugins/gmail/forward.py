"""An email forwarded to other addresses, sent from myOrb once the user confirms them.

The only email myOrb sends: a reply stays a Gmail draft. The forward carries the email's
text, the images inside it and its attachments, under the user's note, as Gmail writes one.
"""

import html
import json
import re
import secrets
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from myorb.plugins.gmail.draft import _addresses, _plain

MAX_NOTE = 5000
MAX_RECIPIENTS = 10
# Gmail sends up to 25 MB; the encoding of the attachments takes a third more.
MAX_SIZE = 25_000_000
ADDRESS = re.compile(r"[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+")
HEADERS = ("From", "Date", "Subject", "To", "Cc")


def recipients(value: str) -> list[str]:
    """The addresses typed into the To field, or ValueError with what is wrong with them."""
    found: list[str] = []
    for item in re.split(r"[,;\s]+", value.strip()):
        if not item:
            continue
        address = parseaddr(item)[1] or item
        if not ADDRESS.fullmatch(address):
            raise ValueError(f"“{item}” is not an email address.")
        if address.lower() not in {a.lower() for a in found}:
            found.append(address)
    if not found:
        raise ValueError("Write the address to forward the email to.")
    if len(found) > MAX_RECIPIENTS:
        raise ValueError(f"Forward the email to {MAX_RECIPIENTS} addresses at most.")
    return found


def _body(html_text: str) -> str:
    """What is inside the HTML's <body>, to put into the forward's own."""
    found = re.search(r"(?is)<body\b[^>]*>(.*)</body>", html_text)
    return found.group(1) if found else html_text


def forward_message(
    raw: bytes, message: dict[str, Any], to: list[str], note: str, own: set[str]
) -> bytes:
    """The forward of `raw` (the email as Gmail keeps it; `message` its headers as
    read_message keeps them), sent from the address the email came to, as bytes to send."""
    original = message_from_bytes(raw, policy=policy.default)
    plain_part = original.get_body(preferencelist=("plain",))
    html_part = original.get_body(preferencelist=("html",))
    text = plain_part.get_content() if plain_part else ""
    body_html = html_part.get_content() if html_part else ""
    shown = {id(p) for p in (plain_part, html_part) if p is not None}
    inline, attachments = [], []
    for part in original.walk():
        if part.is_multipart() or id(part) in shown:
            continue
        cid = (part["Content-ID"] or "").strip().removeprefix("<").removesuffix(">")
        if body_html and cid and f"cid:{cid}".lower() in body_html.lower():
            inline.append((part, cid))
        elif part.get_filename() or part.get_content_disposition() == "attachment":
            attachments.append(part)

    heading = "---------- Forwarded message ---------"
    fields = [(name, str(original[name])) for name in HEADERS if original[name]]
    note = note.strip()[:MAX_NOTE]

    forward = EmailMessage()
    received = [a for _, a in _addresses(message["to_raw"], message["cc_raw"]) if a.lower() in own]
    if received:
        forward["From"] = received[0]
    forward["To"] = ", ".join(to)
    subject = message["subject"]
    forward["Subject"] = subject if re.match(r"(?i)fwd?:", subject) else f"Fwd: {subject}"
    if message["message_id"]:
        forward["References"] = f"{message['references']} {message['message_id']}".strip()
    lines = "\n".join(f"{name}: {value}" for name, value in fields)
    plain = text.strip() or _plain(body_html)
    forward.set_content(f"{note}\n\n{heading}\n{lines}\n\n{plain}\n".lstrip("\n"))
    if body_html:
        rows = "<br>".join(f"{name}: {html.escape(value)}" for name, value in fields)
        said = html.escape(note).replace("\n", "<br>")
        forward.add_alternative(
            (f'<div dir="auto">{said}</div><br>' if note else "")
            + f'<div class="gmail_quote">{heading}<br>{rows}<br><br>{_body(body_html)}</div>',
            subtype="html",
        )
        sent_html = forward.get_payload()[1]
        for part, cid in inline:
            sent_html.add_related(
                part.get_payload(decode=True) or b"",
                part.get_content_maintype(),
                part.get_content_subtype(),
                cid=f"<{cid}>",
                filename=part.get_filename(),
            )
    for part in attachments:
        forward.add_attachment(
            part.get_payload(decode=True) or b"",
            part.get_content_maintype(),
            part.get_content_subtype(),
            filename=part.get_filename() or "attachment",
        )
    sent = forward.as_bytes()
    if len(sent) > MAX_SIZE * 4 // 3:
        raise ValueError("The email is too large to forward from myOrb. Forward it in Gmail.")
    return sent


def upload(sent: bytes, thread_id: str) -> tuple[bytes, str]:
    """The request body for Gmail's upload of a message to send in the conversation, and its
    content type: the message as it is, with the conversation beside it."""
    boundary = f"myorb-{secrets.token_hex(12)}"
    metadata = json.dumps({"threadId": thread_id})
    body = (
        (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
            f"--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n"
        ).encode()
        + sent
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/related; boundary={boundary}"
