import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from markupsafe import Markup

from jarvis_hud.plugins import WidgetContext

router = APIRouter()

logger = logging.getLogger(__name__)


def widget_context(request: Request) -> WidgetContext:
    state = request.app.state
    return WidgetContext(now=datetime.now(state.timezone), cache=state.cache, google=state.google)


@router.get("/healthz", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    plugins = sorted(request.app.state.plugins.values(), key=lambda p: (p.position, p.id))
    widgets = [(plugin, widget) for plugin in plugins for widget in plugin.widgets()]
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"widgets": widgets}
    )


@router.get("/widgets/{plugin_id}/{widget_id}", response_class=HTMLResponse)
async def widget(request: Request, plugin_id: str, widget_id: str) -> HTMLResponse:
    plugin = request.app.state.plugins.get(plugin_id)
    found = plugin and next((w for w in plugin.widgets() if w.id == widget_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="Widget not found")
    templates = request.app.state.templates
    try:
        context = await found.load(widget_context(request))
        body = templates.env.get_template(f"{plugin_id}/{found.template}").render(context)
        card = context.get("card", {})
    except Exception:
        # One broken widget must not break the dashboard; show an honest error instead.
        logger.exception("Widget %s/%s failed", plugin_id, widget_id)
        body = templates.env.get_template("widget_error.html").render()
        card = {"state": "error"}
    return templates.TemplateResponse(
        request,
        "widgets/card.html",
        {"plugin": plugin, "widget": found, "card": card, "body": Markup(body), "loading": False},
    )
