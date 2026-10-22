import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud import __version__
from jarvis_hud.db import get_db
from jarvis_hud.music import load_music
from jarvis_hud.plugins import Link, Plugin, Widget, WidgetContext, WidgetData

router = APIRouter()

logger = logging.getLogger(__name__)

FAILED = WidgetData(
    value=None,
    message="This could not be loaded. Details are in the Jarvis log.",
    message_tone="fail",
    message_action=Link("Check integrations", "/integrations"),
)


def widget_context(request: Request) -> WidgetContext:
    return app_context(request.app)


def app_context(app: FastAPI) -> WidgetContext:
    """The context plugins load with, also outside a request (e.g. the brief scheduler)."""
    state = app.state
    return WidgetContext(
        now=datetime.now(state.timezone),
        cache=state.cache,
        google=state.google,
        sessionmaker=state.sessionmaker,
        http=state.http,
        connections=state.connections,
    )


def ordered_plugins(request: Request) -> list[Plugin]:
    return sorted(request.app.state.plugins.values(), key=lambda p: (p.position, p.id))


@router.get("/healthz", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


STATIC_DIR = Path(__file__).parent / "static"


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    """Web app manifest: lets phones and desktops install Jarvis as an app."""
    icons = "/static/icons"
    return JSONResponse(
        {
            "name": "Jarvis",
            "short_name": "Jarvis",
            "description": "Personal AI daily briefing dashboard",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#06080b",
            "theme_color": "#07090c",
            "icons": [
                {"src": f"{icons}/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": f"{icons}/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": f"{icons}/icon-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        },
        media_type="application/manifest+json",
    )


@router.get("/sw.js")
async def service_worker() -> Response:
    # Served from the root, not /static, so it controls every page. The cache name changes
    # with each version, which drops files cached by the previous one.
    script = (STATIC_DIR / "sw.js").read_text().replace("__VERSION__", __version__)
    return Response(script, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/", response_class=HTMLResponse)
async def welcome(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> HTMLResponse:
    panels = [(plugin, panel) for plugin in ordered_plugins(request) for panel in plugin.panels()]
    return request.app.state.templates.TemplateResponse(
        request,
        "welcome.html",
        {
            "now": datetime.now(request.app.state.timezone),
            "side_panels": [(p, panel) for p, panel in panels if panel.placement == "side"],
            "header_panels": [(p, panel) for p, panel in panels if panel.placement == "header"],
            "music": await load_music(request, db),
        },
    )


async def _load_widget(context: WidgetContext, plugin: Plugin, widget: Widget) -> WidgetData:
    try:
        return await widget.load(context)
    except Exception:
        # One broken widget must not break the screen; it shows an honest error instead.
        logger.exception("Widget %s/%s failed", plugin.id, widget.id)
        return FAILED


@router.get("/welcome/widgets", response_class=HTMLResponse)
async def widgets(request: Request) -> HTMLResponse:
    """The numbers around the orb and their details, loaded together."""
    context = widget_context(request)
    entries = [
        (plugin, widget) for plugin in ordered_plugins(request) for widget in plugin.widgets()
    ]
    results = await asyncio.gather(*(_load_widget(context, p, w) for p, w in entries))
    # Widgets of services that are not set up are left out; Integrations lists them instead.
    items = [
        {"plugin": plugin, "widget": widget, "data": data, "key": f"{plugin.id}-{widget.id}"}
        for (plugin, widget), data in zip(entries, results, strict=True)
        if data.connected
    ]
    return request.app.state.templates.TemplateResponse(
        request, "welcome/widgets.html", {"items": items}
    )


@router.get("/panels/{plugin_id}/{panel_id}", response_class=HTMLResponse)
async def panel(request: Request, plugin_id: str, panel_id: str) -> HTMLResponse:
    plugin = request.app.state.plugins.get(plugin_id)
    found = plugin and next((p for p in plugin.panels() if p.id == panel_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="Panel not found")
    templates = request.app.state.templates
    try:
        context = await found.load(widget_context(request))
        return templates.TemplateResponse(request, f"{plugin_id}/{found.template}", context)
    except Exception:
        logger.exception("Panel %s/%s failed", plugin_id, panel_id)
        return templates.TemplateResponse(request, "welcome/panel_error.html", {})
