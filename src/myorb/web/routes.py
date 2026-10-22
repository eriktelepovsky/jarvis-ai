import asyncio
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb import __version__
from myorb.cache import refreshing
from myorb.db import get_db
from myorb.overview import snapshots
from myorb.plugins import Link, Plugin, Widget, WidgetContext, WidgetData
from myorb.profile import DEFAULT_ASSISTANT, user_name

router = APIRouter()

logger = logging.getLogger(__name__)

FAILED = WidgetData(
    value=None,
    message="This could not be loaded. Details are in the myOrb log.",
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
async def manifest(request: Request) -> JSONResponse:
    """Web app manifest: lets phones and desktops install myOrb as an app, by the assistant's
    name (the manifest is public, like the name on the sign-in page)."""
    icons = "/static/icons"
    name = getattr(request.app.state, "assistant", DEFAULT_ASSISTANT)
    return JSONResponse(
        {
            "name": name,
            "short_name": name[:12],
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
            "name": await user_name(request, db),
            "slots": widget_slots(request),
        },
    )


async def _load_widget(context: WidgetContext, plugin: Plugin, widget: Widget) -> WidgetData:
    try:
        return await widget.load(context)
    except Exception:
        # One broken widget must not break the screen; it shows an honest error instead.
        logger.exception("Widget %s/%s failed", plugin.id, widget.id)
        return FAILED


def node_size(weight: float, data: WidgetData) -> float:
    """How big a widget's circle is around the orb: by what needs the user, as in the design.

    Something that needs attention is large, a source with nothing to show (0 or no data) is
    small; the plugin's weight only adjusts it a little.
    """
    needs = data.attention or data.message_tone in ("fail", "warn")
    empty = data.value in (None, 0, "")
    state = 1.25 if needs else 0.8 if empty else 1.0
    return round(min(1.4, state * (1 + (weight - 1) * 0.5)), 3)


async def _finish(
    request: Request, context: WidgetContext, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Today's numbers kept for "change since yesterday", changes noted in the activity list."""
    values = {
        item["key"]: number
        for item in items
        if (number := snapshots.numeric(item["data"].value)) is not None
    }
    async with request.app.state.sessionmaker() as db:
        previous = await snapshots.changes(db, context.now.date(), values)
        await snapshots.record(db, context.now.date(), values)
    # What changed since the last load goes to the activity list: a source failing, new items.
    await request.app.state.widget_watch.note(request.app.state.sessionmaker, items)
    for item in items:
        item["change"] = snapshots.change_label(previous.get(item["key"]))
        item["size"] = node_size(item["widget"].weight, item["data"])
    return items


async def load_widgets(request: Request) -> list[dict[str, Any]]:
    """All widgets of set-up services, with today's numbers kept for "change since yesterday"."""
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
    return await _finish(request, context, items)


def widget_slots(request: Request) -> list[tuple[Plugin, Widget]]:
    """Every widget, in order: the welcome screen gives each a place and loads it on its own."""
    return [(plugin, widget) for plugin in ordered_plugins(request) for widget in plugin.widgets()]


@router.get("/welcome/widget/{plugin_id}/{widget_id}", response_class=HTMLResponse)
async def one_widget(request: Request, plugin_id: str, widget_id: str) -> Response:
    """One number and its details, as soon as its service answers; empty when not set up."""
    plugin = request.app.state.plugins.get(plugin_id)
    widget = plugin and next((w for w in plugin.widgets() if w.id == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    context = widget_context(request)
    data = await _load_widget(context, plugin, widget)
    if not data.connected:
        return HTMLResponse("")
    [item] = await _finish(
        request,
        context,
        [{"plugin": plugin, "widget": widget, "data": data, "key": f"{plugin_id}-{widget_id}"}],
    )
    return request.app.state.templates.TemplateResponse(
        request, "welcome/widget.html", {"item": item}
    )


@router.post("/welcome/widgets/{plugin_id}/{widget_id}/refresh")
async def refresh_widget(request: Request, plugin_id: str, widget_id: str) -> Response:
    """The Refresh button in a widget's details: its data is fetched again now."""
    plugin = request.app.state.plugins.get(plugin_id)
    widget = plugin and next((w for w in plugin.widgets() if w.id == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    token = refreshing.set(True)
    try:
        await _load_widget(widget_context(request), plugin, widget)
    finally:
        refreshing.reset(token)
    # The welcome screen reloads its widgets (now fresh) and opens the same details again.
    return Response(status_code=204, headers={"HX-Trigger": "myorb:refresh"})


@router.get("/welcome/widgets", response_class=HTMLResponse)
async def widgets(request: Request) -> HTMLResponse:
    """The numbers around the orb and their details, loaded together."""
    items = await load_widgets(request)
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
        params = dict(request.query_params)
        context = await found.load(replace(widget_context(request), params=params))
        return templates.TemplateResponse(request, f"{plugin_id}/{found.template}", context)
    except Exception:
        logger.exception("Panel %s/%s failed", plugin_id, panel_id)
        return templates.TemplateResponse(request, "welcome/panel_error.html", {})
