"""The AI summary of unread emails in the email details: shown when there is one, written on
request (it costs a Claude call), and kept until the unread emails change."""

import time
from html import escape
from string import hexdigits
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from myorb.brief.writer import BriefError
from myorb.profile import load_profile
from myorb.web.routes import widget_context

router = APIRouter()


async def _unread(request: Request) -> list[dict[str, Any]]:
    """The unread emails the details list, newest first (from the cache, as the widget)."""
    plugin = request.app.state.plugins["gmail"]
    accounts = await plugin._accounts(widget_context(request))
    emails = [m for a in accounts if a.data is not None for m in a.data["latest_unread"]]
    return sorted(emails, key=lambda m: m["received"], reverse=True)


def _key(emails: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(f"{m['url']}@{m['received'].timestamp()}" for m in emails))


def _render(request: Request, **context: Any) -> Response:
    return request.app.state.templates.TemplateResponse(request, "gmail/summary.html", context)


@router.get("/email/summary", response_class=HTMLResponse)
async def summary(request: Request) -> Response:
    if await request.app.state.brief.settings() is None:
        return HTMLResponse("")  # without Claude, the details stay as they are
    emails = await _unread(request)
    saved = getattr(request.app.state, "email_summary", None)
    if saved is None or not emails:
        return _render(request, summary=None, count=len(emails))
    current = set(_key(emails))
    new = len(current - set(saved["key"]))
    return _render(request, summary=saved, count=len(emails), new=new)


@router.post("/email/summary", response_class=HTMLResponse)
async def write_summary(request: Request) -> Response:
    from myorb.plugins.gmail.summary import summarize

    service = request.app.state.brief
    settings = await service.settings()
    emails = await _unread(request)
    if settings is None or not emails:
        return _render(request, summary=None, count=len(emails))
    async with request.app.state.sessionmaker() as db:
        assistant = (await load_profile(db, request.app.state.secret_box))["assistant"]
    try:
        content = await summarize(
            service.client(settings.api_key, settings.workspace_id),
            settings.model,
            emails,
            widget_context(request).now,
            settings.language,
            assistant,
        )
    except BriefError as error:
        return _render(request, summary=None, count=len(emails), error=str(error))
    saved = {**content, "key": _key(emails), "at": time.time()}
    request.app.state.email_summary = saved
    return _render(request, summary=saved, count=len(emails), new=0)


# A test, removed once one works: which googlegmail:// address opens the Gmail app on an
# iPhone at a given email (the app's addresses are undocumented).
@router.get("/email/app-links", response_class=HTMLResponse)
async def app_links(request: Request) -> Response:
    plugin = request.app.state.plugins["gmail"]
    accounts = await plugin._accounts(widget_context(request))
    found = [(a.email, m) for a in accounts if a.data is not None for m in a.data["latest_unread"]]
    if not found:
        return HTMLResponse("<p>No unread email to test with.</p>")
    email, newest = max(found, key=lambda item: item[1]["received"])
    thread = newest.get("thread_id") or newest["url"].rsplit("/", 1)[-1]
    message = newest.get("message_id") or thread
    decimal = int(thread, 16) if thread and all(c in hexdigits for c in thread) else 0
    variants = {
        "A": f"googlegmail:///cv={thread}/accountId=1&create-new-tab",
        "B": f"googlegmail:///cv={thread}",
        "C": f"googlegmail://cv={thread}",
        "D": f"googlegmail:///cv={message}/accountId=1&create-new-tab",
        "E": f"googlegmail:///cv=thread-f:{decimal}/accountId=1&create-new-tab",
        "F": f"googlegmail:///cv={thread}/accountId=0&create-new-tab",
        "G": f"googlegmail:///cv={thread}/accountId={email}&create-new-tab",
        "H": f"googlegmail://message?id={message}",
        "I": f"googlegmail:///tl/inbox/cv={thread}",
    }
    links = "".join(
        f'<li><a href="{escape(url)}">{name}</a> <code>{escape(url)}</code></li>'
        for name, url in variants.items()
    )
    return HTMLResponse(
        '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Gmail app links</title>"
        '<body style="font: 17px system-ui; padding: 16px; line-height: 2.2">'
        f"<p>Each should open <strong>{escape(newest['subject'])}</strong> from "
        f"{escape(newest['sender'])} ({escape(email)}).</p>"
        f"<ol style='list-style: none; padding: 0'>{links}</ol>"
    )
