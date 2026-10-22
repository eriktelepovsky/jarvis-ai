import re
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from jarvis_hud.brief.service import DEFAULT_TIME, KICKERS, BriefService, ClaudeSettings
from jarvis_hud.brief.writer import DEFAULT_MODEL, LANGUAGES, MODELS, BriefError

router = APIRouter()

REFRESH = {"HX-Trigger": "jarvis:brief"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


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
) -> Response:
    service = _service(request)
    existing = await service.settings()
    api_key = api_key.strip() or (existing.api_key if existing else "")
    error = None
    if not api_key:
        error = "Enter an API key."
    elif language not in LANGUAGES:
        error = "Choose a language."
    elif not TIME.match(brief_time):
        error = "Enter the time as HH:MM."
    elif model not in MODELS:
        error = "Choose a model."
    elif not existing or api_key != existing.api_key or model != existing.model:
        error = await service.check_key(api_key, model)
    if error:
        context = _form_context(
            existing, error=error, language=language, brief_time=brief_time, model=model
        )
        return _render(request, "brief/settings.html", context, status_code=400)
    await service.save_settings(ClaudeSettings(api_key, language, brief_time, model))
    return RedirectResponse("/integrations?status=claude-saved", status_code=303)


@router.post("/integrations/claude/remove")
async def remove_claude(request: Request) -> Response:
    await _service(request).remove_settings()
    return RedirectResponse("/integrations?status=claude-removed", status_code=303)


async def _brief_context(request: Request) -> dict:
    service = _service(request)
    brief = await service.latest()
    return {
        "configured": await service.settings() is not None,
        "brief": brief,
        "kicker": KICKERS.get(brief.kind, "Report") if brief else None,
        "generating": service.generating,
        "error": service.error,
    }


@router.get("/brief/headline", response_class=HTMLResponse)
async def headline(request: Request) -> Response:
    return _render(request, "brief/headline.html", await _brief_context(request))


@router.get("/brief/report", response_class=HTMLResponse)
async def report(request: Request) -> Response:
    return _render(request, "brief/report.html", await _brief_context(request))


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
