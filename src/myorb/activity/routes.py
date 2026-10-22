from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from myorb.activity.store import recent

router = APIRouter()

# The welcome screen shows the activity of the last hours, and at least the latest few events
# however old (the log keeps a week), so it is not empty on a quiet day.
WINDOW = timedelta(hours=12)
LATEST = 10


@router.get("/activity", response_class=HTMLResponse)
async def activity(request: Request) -> Response:
    from myorb.web.routes import widget_context

    now = widget_context(request).now
    async with request.app.state.sessionmaker() as db:
        events = await recent(db, now - WINDOW)
        if len(events) < LATEST:
            events = await recent(db, None, LATEST)
    return request.app.state.templates.TemplateResponse(
        request, "welcome/activity.html", {"events": events, "today": now.date()}
    )
