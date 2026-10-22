from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter()


@router.get("/healthz", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    plugins = request.app.state.plugins
    widgets = [(plugin, widget) for plugin in plugins.values() for widget in plugin.widgets()]
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"widgets": widgets}
    )


@router.get("/widgets/{plugin_id}/{widget_id}", response_class=HTMLResponse)
async def widget(request: Request, plugin_id: str, widget_id: str) -> HTMLResponse:
    plugin = request.app.state.plugins.get(plugin_id)
    found = plugin and next((w for w in plugin.widgets() if w.id == widget_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="Widget not found")
    context = await found.load()
    return request.app.state.templates.TemplateResponse(
        request, f"{plugin_id}/{found.template}", context
    )
