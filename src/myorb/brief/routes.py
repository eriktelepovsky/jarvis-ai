import asyncio
import logging
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from myorb.brief.service import DEFAULT_TIME, KICKERS, BriefService, ClaudeSettings
from myorb.brief.writer import DEFAULT_MODEL, LANGUAGES, MODELS, READ_LANGUAGES, BriefError
from myorb.overview.glance import Glance, duration, glance
from myorb.plugins import Plugin, TimelineItem
from myorb.voice import load_voice
from myorb.web.routes import load_widgets, ordered_plugins, widget_context

logger = logging.getLogger(__name__)

router = APIRouter()

REFRESH = {"HX-Trigger": "myorb:brief"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WORKSPACE = re.compile(r"^wrkspc_[A-Za-z0-9]{8,64}$")


def _service(request: Request) -> BriefService:
    return request.app.state.brief


def _render(request: Request, template: str, context: dict, status_code: int = 200) -> Response:
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


@router.get("/integrations/claude", response_class=HTMLResponse)
async def claude_page(request: Request) -> Response:
    settings = await _service(request).settings()
    return _render(request, "brief/settings.html", _form_context(settings))


def _form_context(settings: ClaudeSettings | None, **values: str) -> dict:
    return {
        "settings": settings,
        "languages": LANGUAGES,
        "models": MODELS,
        "default_time": DEFAULT_TIME,
        "default_model": DEFAULT_MODEL,
        **values,
    }


@router.post("/integrations/claude")
async def save_claude(
    request: Request,
    api_key: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    brief_time: Annotated[str, Form()] = DEFAULT_TIME,
    model: Annotated[str, Form()] = DEFAULT_MODEL,
    workspace_id: Annotated[str, Form()] = "",
) -> Response:
    service = _service(request)
    existing = await service.settings()
    api_key = api_key.strip() or (existing.api_key if existing else "")
    workspace_id = workspace_id.strip()
    error = None
    if not api_key:
        error = "Enter an API key."
    elif language not in LANGUAGES:
        error = "Choose a language."
    elif not TIME.match(brief_time):
        error = "Enter the time as HH:MM."
    elif model not in MODELS:
        error = "Choose a model."
    elif workspace_id and not WORKSPACE.match(workspace_id):
        error = "A workspace ID looks like wrkspc_… — copy it from the Anthropic Console."
    elif (
        not existing
        or api_key != existing.api_key
        or model != existing.model
        or workspace_id != existing.workspace_id
    ):
        error = await service.check_key(api_key, model, workspace_id)
    if error:
        context = _form_context(
            existing,
            error=error,
            language=language,
            brief_time=brief_time,
            model=model,
            workspace_id=workspace_id,
        )
        return _render(request, "brief/settings.html", context, status_code=400)
    await service.save_settings(ClaudeSettings(api_key, language, brief_time, model, workspace_id))
    return RedirectResponse("/integrations?status=claude-saved", status_code=303)


@router.post("/integrations/claude/remove")
async def remove_claude(request: Request) -> Response:
    await _service(request).remove_settings()
    return RedirectResponse("/integrations?status=claude-removed", status_code=303)


async def _timeline(request: Request) -> Glance:
    context = widget_context(request)

    async def collect(plugin: Plugin) -> list[TimelineItem]:
        try:
            return await plugin.timeline(context)
        except Exception:
            logger.exception("Timeline of %s failed", plugin.id)
            return []

    results = await asyncio.gather(*(collect(p) for p in ordered_plugins(request)))
    return glance([item for items in results for item in items], context.now)


async def _brief_context(request: Request) -> dict:
    service = _service(request)
    brief = await service.latest()
    settings = await service.settings()
    now = widget_context(request).now
    # After midnight the latest brief is a day old until the schedule writes today's: it says
    # "tomorrow" about today, so the report says whose day it describes.
    written = brief.created_at.astimezone(now.tzinfo).date() if brief else None
    return {
        "configured": settings is not None,
        "stale": written is not None and written < now.date(),
        "yesterday": written is not None and (now.date() - written).days == 1,
        "brief_time": settings.brief_time if settings else None,
        "written_later": settings is not None and now.strftime("%H:%M") < settings.brief_time,
        "brief": brief,
        "kicker": KICKERS.get(brief.kind, "Report") if brief else None,
        "generating": service.generating,
        "error": service.error,
        "read_languages": READ_LANGUAGES,
    }


@router.post("/brief/{brief_id}/translation")
async def translation(
    request: Request, brief_id: int, language: Annotated[str, Form()]
) -> Response:
    """The summary to read aloud in another language than the brief was written in."""
    try:
        paragraphs = await _service(request).translation(brief_id, language)
    except BriefError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse({"text": "\n\n".join(paragraphs), "lang": READ_LANGUAGES[language][1]})


@router.get("/brief/headline", response_class=HTMLResponse)
async def headline(request: Request) -> Response:
    return _render(request, "brief/headline.html", await _brief_context(request))


@router.get("/brief/report", response_class=HTMLResponse)
async def report(request: Request) -> Response:
    """The brief, with the day's timeline and every source's number around it."""
    context = await _brief_context(request)
    now = widget_context(request).now
    brief = context["brief"]
    if context["stale"]:
        # Yesterday's brief shows yesterday's calendar, as it saw it, so the two agree; today's
        # calendar comes with today's brief.
        written = brief.created_at.astimezone(now.tzinfo)
        items = [
            TimelineItem(
                datetime.fromisoformat(i["start"]).astimezone(now.tzinfo),
                datetime.fromisoformat(i["end"]).astimezone(now.tzinfo),
                i["title"],
            )
            for i in brief.content.get("timeline", [])
        ]
        context["glance"] = glance(items, written, day_over=True)
        context["sources"] = await load_widgets(request)
        context["glance_day"] = written
        context["glance_kept"] = "timeline" in brief.content
    else:
        context["glance"], context["sources"] = await asyncio.gather(
            _timeline(request), load_widgets(request)
        )
    context["now"] = now
    async with request.app.state.sessionmaker() as db:
        context["voice"] = await load_voice(request, db)
    context["duration"] = duration
    return _render(request, "brief/report.html", context)


@router.post("/brief/generate")
async def generate(request: Request) -> Response:
    try:
        await _service(request).generate()
    except BriefError:
        # The error is kept by the service and shown in the report.
        pass
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers=REFRESH)
    return RedirectResponse("/", status_code=303)
