"""The AI summary of unread emails in the email details: shown when there is one, written on
request (it costs a Claude call), and kept until the unread emails change."""

import asyncio
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from myorb.brief.writer import BriefError
from myorb.connections import store
from myorb.connections.google import GoogleError
from myorb.connections.google_accounts import GoogleAccount
from myorb.plugins.gmail import API, API_NAME, MODIFY_SCOPE, inbox_url, own_addresses
from myorb.plugins.gmail.draft import reply_message, write_reply
from myorb.plugins.gmail.message import (
    MAX_INLINE,
    embed_inline,
    frame_document,
    read_message,
    size_label,
)
from myorb.profile import load_profile
from myorb.web.routes import widget_context

router = APIRouter()


async def _unread(request: Request) -> list[dict[str, Any]]:
    """The unread emails the details list, newest first (from the cache, as the widget)."""
    plugin = request.app.state.plugins["gmail"]
    accounts = await plugin._accounts(widget_context(request))
    emails = [m for a in accounts if a.data is not None for m in plugin.unread_of(a.email, a.data)]
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


# Senders whose emails show their images without asking, by the domain of their address.
IMAGES_PROVIDER = "gmail_images"
MAX_DOMAINS = 1000


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].strip().lower() if "@" in address else ""


async def _image_domains(request: Request) -> list[str]:
    async with request.app.state.sessionmaker() as db:
        data = await store.get_provider_settings(db, request.app.state.secret_box, IMAGES_PROVIDER)
    return (data or {}).get("domains", [])


async def _fetch_inline(request: Request, account: GoogleAccount, messages: list) -> None:
    """Fetches the images sent inside the emails that did not come with the thread."""

    async def fetch(message: dict[str, Any], image: dict[str, Any]) -> None:
        url = f"{API}/messages/{message['id']}/attachments/{image['attachment_id']}"
        try:
            found = await request.app.state.google.get_json(account, url, API_NAME)
        except GoogleError:
            return  # The email shows without this image.
        image["data"] = found.get("data", "")

    await asyncio.gather(
        *(
            fetch(message, image)
            for message in messages
            for image in message["inline"]
            if not image["data"] and image["attachment_id"] and image["size"] <= MAX_INLINE
        )
    )
    for message in messages:
        message["html"] = embed_inline(message["html"], message["inline"])


def _in_view(request: Request) -> bool:
    """Whether the email is shown over the welcome screen's details rather than as a page."""
    return request.headers.get("HX-Target") == "page-view"


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
        # The same permission saves drafts (a reply), never sends.
        "can_draft": MODIFY_SCOPE in account.scopes,
        "images": images,
        "marked_read": read,
        "in_view": _in_view(request),
        "frame_document": frame_document,
        "size_label": size_label,
    }
    try:
        thread = await request.app.state.google.get_json(
            account, f"{API}/threads/{thread_id}", API_NAME, {"format": "full"}
        )
        timezone = request.app.state.timezone
        context["messages"] = [read_message(m, timezone) for m in thread.get("messages", [])]
        await _fetch_inline(request, account, context["messages"])
    except GoogleError as error:
        context["error"] = str(error)
        context["messages"] = []
    allowed = set(await _image_domains(request))
    with_html = [m for m in context["messages"] if m["html"]]
    for message in with_html:
        message["domain"] = domain_of(message["address"])
        message["images"] = images or message["domain"] in allowed
    domains = sorted({m["domain"] for m in with_html if m["domain"]})
    context["blocked_domains"] = [d for d in domains if d not in allowed]
    context["allowed_domains"] = [d for d in domains if d in allowed]
    return request.app.state.templates.TemplateResponse(request, "gmail/email.html", context)


@router.post("/email/{account_id}/{thread_id}/images")
async def remember_images(
    request: Request,
    account_id: int,
    thread_id: str,
    domain: Annotated[list[str], Form()],
    show: Annotated[str, Form()] = "true",
) -> Response:
    """Show (or block again) the images of every email from these domains."""
    await _account(request, account_id)
    chosen = {d.strip().lower() for d in domain if "." in d and len(d) <= 253}
    domains = set(await _image_domains(request))
    domains = domains - chosen if show == "false" else domains | chosen
    async with request.app.state.sessionmaker() as db:
        await store.save_provider_settings(
            db,
            request.app.state.secret_box,
            IMAGES_PROVIDER,
            {"domains": sorted(domains)[:MAX_DOMAINS]},
        )
    if _in_view(request):
        return await read_email(request, account_id, thread_id)
    return RedirectResponse(f"/email/{account_id}/{thread_id}", status_code=303)


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
    request.app.state.plugins["gmail"].marked_read(account.email, thread_id)
    if _in_view(request):
        # The list behind keeps its place; the email's row leaves it.
        response = await read_email(request, account_id, thread_id, read=True)
        trigger = json.dumps({"myorb:row-done": f"/email/{account_id}/{thread_id}"})
        response.headers["HX-Trigger"] = trigger
        return response
    # A button in the email details refreshes the screen; the email's own page shows it.
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return Response(status_code=204, headers={"HX-Trigger": "myorb:refresh"})
    return RedirectResponse(f"/email/{account_id}/{thread_id}?read=true", status_code=303)


# A reply, written by Claude or the user, saved as a Gmail draft (myOrb never sends it)


async def _draft_account(request: Request, account_id: int) -> GoogleAccount:
    account = await _account(request, account_id)
    if MODIFY_SCOPE not in account.scopes:
        raise HTTPException(
            status_code=403, detail="Connect the Google account again to allow drafts."
        )
    return account


async def _conversation(
    request: Request, account: GoogleAccount, thread_id: str
) -> tuple[list[dict[str, Any]], set[str]]:
    """The conversation's messages (oldest first) and the user's own addresses in it."""
    google = request.app.state.google
    thread, own = await asyncio.gather(
        google.get_json(account, f"{API}/threads/{thread_id}", API_NAME, {"format": "full"}),
        own_addresses(google, account),
    )
    messages = [read_message(m, request.app.state.timezone) for m in thread.get("messages", [])]
    if not messages:
        raise GoogleError("This conversation has no messages.")
    return messages, own | {account.email.lower()}


def _render_draft(request: Request, account: GoogleAccount, thread_id: str, **context: Any):
    return request.app.state.templates.TemplateResponse(
        request,
        "gmail/draft.html",
        {
            "account": account,
            "thread_id": thread_id,
            "gmail_url": inbox_url(account.email, thread_id),
            "note": "",
            "body": None,
            "reply_all": False,
            **context,
        },
    )


@router.get("/email/{account_id}/{thread_id}/draft", response_class=HTMLResponse)
async def start_draft(
    request: Request, account_id: int, thread_id: str, blank: bool = False
) -> Response:
    """The reply form: what Claude should say, or (blank) an empty reply to write oneself."""
    account = await _draft_account(request, account_id)
    can_write = await request.app.state.brief.settings() is not None
    return _render_draft(
        request,
        account,
        thread_id,
        can_write=can_write,
        body="" if blank or not can_write else None,
    )


@router.post("/email/{account_id}/{thread_id}/draft/write", response_class=HTMLResponse)
async def write_draft(
    request: Request,
    account_id: int,
    thread_id: str,
    note: Annotated[str, Form()] = "",
    reply_all: Annotated[bool, Form()] = False,
) -> Response:
    account = await _draft_account(request, account_id)
    service = request.app.state.brief
    settings = await service.settings()
    context: dict[str, Any] = {"can_write": settings is not None, "note": note}
    if settings is None:
        return _render_draft(request, account, thread_id, body="", **context)
    async with request.app.state.sessionmaker() as db:
        profile = await load_profile(db, request.app.state.secret_box)
    try:
        messages, own = await _conversation(request, account, thread_id)
        body = await write_reply(
            service.client(settings.api_key, settings.workspace_id),
            settings.model,
            messages,
            own,
            note,
            profile["assistant"],
            profile["name"],
        )
    except (GoogleError, BriefError) as error:
        return _render_draft(request, account, thread_id, error=str(error), **context)
    return _render_draft(request, account, thread_id, body=body, reply_all=reply_all, **context)


@router.post("/email/{account_id}/{thread_id}/draft", response_class=HTMLResponse)
async def save_draft(
    request: Request,
    account_id: int,
    thread_id: str,
    body: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
    reply_all: Annotated[bool, Form()] = False,
) -> Response:
    """Saves the reply as a draft in the conversation; the user sends it from Gmail."""
    account = await _draft_account(request, account_id)
    can_write = await request.app.state.brief.settings() is not None
    context: dict[str, Any] = {"can_write": can_write, "note": note, "reply_all": reply_all}
    if not body.strip():
        return _render_draft(
            request, account, thread_id, body=body, error="The reply is empty.", **context
        )
    try:
        messages, own = await _conversation(request, account, thread_id)
        draft = reply_message(messages[-1], body, own, reply_all)
        await request.app.state.google.request_json(
            account, "POST", f"{API}/drafts", API_NAME, json={"message": draft}
        )
    except GoogleError as error:
        return _render_draft(request, account, thread_id, body=body, error=str(error), **context)
    return _render_draft(request, account, thread_id, saved=True, **context)
