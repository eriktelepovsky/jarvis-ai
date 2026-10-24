"""The AI summary of unread emails in the email details: shown when there is one, written on
request (it costs a Claude call), and kept until the unread emails change."""

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from myorb.brief.writer import BriefError
from myorb.connections.google import GoogleError
from myorb.connections.google_accounts import GoogleAccount
from myorb.plugins.gmail import API, API_NAME, MODIFY_SCOPE, inbox_url
from myorb.plugins.gmail.message import frame_document, read_message, size_label
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


# Reading an email in myOrb


async def _account(request: Request, account_id: int) -> GoogleAccount:
    account = next((a for a in await request.app.state.google.list() if a.id == account_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail="This Google account is not connected.")
    return account


@router.get("/email/{account_id}/{thread_id}", response_class=HTMLResponse)
async def read_email(
    request: Request, account_id: int, thread_id: str, images: bool = False, read: bool = False
) -> Response:
    account = await _account(request, account_id)
    context: dict[str, Any] = {
        "account": account,
        "thread_id": thread_id,
        "gmail_url": inbox_url(account.email, thread_id),
        "can_mark_read": MODIFY_SCOPE in account.scopes,
        "images": images,
        "marked_read": read,
        "frame_document": frame_document,
        "size_label": size_label,
    }
    try:
        thread = await request.app.state.google.get_json(
            account, f"{API}/threads/{thread_id}", API_NAME, {"format": "full"}
        )
        timezone = request.app.state.timezone
        context["messages"] = [read_message(m, timezone) for m in thread.get("messages", [])]
    except GoogleError as error:
        context["error"] = str(error)
        context["messages"] = []
    return request.app.state.templates.TemplateResponse(request, "gmail/email.html", context)


@router.post("/email/{account_id}/{thread_id}/read")
async def mark_read(request: Request, account_id: int, thread_id: str) -> Response:
    account = await _account(request, account_id)
    if MODIFY_SCOPE not in account.scopes:
        raise HTTPException(
            status_code=403, detail="Connect the Google account again to allow marking as read."
        )
    try:
        await request.app.state.google.request_json(
            account,
            "POST",
            f"{API}/threads/{thread_id}/modify",
            API_NAME,
            json={"removeLabelIds": ["UNREAD"]},
        )
    except GoogleError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    request.app.state.cache.invalidate(f"google:{account.id}:gmail")
    # A button in the email details refreshes the screen; the email's own page shows it.
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return Response(status_code=204, headers={"HX-Trigger": "myorb:refresh"})
    return RedirectResponse(f"/email/{account_id}/{thread_id}?read=true", status_code=303)
