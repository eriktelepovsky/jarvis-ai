from pathlib import Path
from typing import Any

import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient

from myorb import __version__
from myorb.plugins import (
    Panel,
    Plugin,
    Row,
    Widget,
    WidgetContext,
    WidgetData,
    discover_plugins,
)
from myorb.plugins.calendar import CalendarPlugin
from myorb.plugins.gmail import GmailPlugin


class EchoPlugin(Plugin):
    """A plugin that shows text as given, to check escaping and error handling."""

    id = "echo"
    name = "Echo"
    position = 1

    def __init__(self, templates_dir: Path, text: str = "<script>", fail: bool = False) -> None:
        self.templates_dir = templates_dir
        self.text = text
        self.fail = fail

    def widgets(self) -> list[Widget]:
        return [Widget(id="echo", label="Echo", title="Echo", icon="M0 0", load=self._widget)]

    def panels(self) -> list[Panel]:
        return [Panel(id="echo", title="Echo", template="echo.html", load=self._panel)]

    async def _widget(self, context: WidgetContext) -> WidgetData:
        if self.fail:
            raise RuntimeError("boom")
        return WidgetData(value=self.text, rows=[Row(title=self.text)])

    async def _panel(self, context: WidgetContext) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("boom")
        return {"value": self.text}


@pytest.fixture
def echo(tmp_path: Path) -> Path:
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "echo.html").write_text("<p>{{ value }}</p>")
    return tmp_path / "templates"


def test_health_check_needs_no_login(make_client: MakeClient):
    client = make_client()
    complete_setup(client)
    client.cookies.clear()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


def test_builtin_plugins_are_discovered_via_entry_points():
    plugins = discover_plugins()
    assert isinstance(plugins["gmail"], GmailPlugin)
    assert isinstance(plugins["calendar"], CalendarPlugin)


def test_welcome_screen_loads_widgets_and_panels(make_client: MakeClient, echo: Path):
    client = make_client(plugins={"echo": EchoPlugin(echo), "calendar": CalendarPlugin()})
    complete_setup(client)
    text = client.get("/").text
    # Narrow, the day and the activity are screens beside the orb's, chosen in this bar.
    assert (
        "data-panes-nav" in text
        and 'data-pane="0">Today<' in text
        and 'data-pane="2">Activity<' in text
    )
    # Each widget loads on its own, so a slow service does not hold up the others.
    assert 'hx-get="/welcome/widget/calendar/today"' in text
    assert client.get("/welcome/widget/calendar/today").text == ""  # Google not set up: empty
    assert client.get("/welcome/widget/calendar/nothing").status_code == 404
    assert 'hx-get="http://testserver/panels/echo/echo"' in text
    # The calendar's "next event" goes to the header, its schedule to the side column.
    assert text.index("panels/calendar/next") < text.index('class="side side-left"')
    assert text.index('class="side side-left"') < text.index("panels/calendar/today")


def test_widgets_and_panels_escape_plugin_data(make_client: MakeClient, echo: Path):
    client = make_client(plugins={"echo": EchoPlugin(echo)})
    complete_setup(client)
    for path in ["/welcome/widgets", "/panels/echo/echo"]:
        text = client.get(path).text
        assert "&lt;script&gt;" in text
        assert "<script>" not in text


def test_failing_plugin_shows_error(make_client: MakeClient, echo: Path):
    client = make_client(plugins={"echo": EchoPlugin(echo, fail=True)})
    complete_setup(client)
    widgets = client.get("/welcome/widgets")
    assert widgets.status_code == 200
    assert "could not be loaded" in widgets.text
    assert "1 source needs attention" in widgets.text
    panel = client.get("/panels/echo/echo")
    assert panel.status_code == 200
    assert "could not be loaded" in panel.text


@pytest.mark.parametrize("path", ["/panels/missing/echo", "/panels/echo/missing"])
def test_unknown_panel_returns_404(make_client: MakeClient, echo: Path, path: str):
    client = make_client(plugins={"echo": EchoPlugin(echo)})
    complete_setup(client)
    assert client.get(path).status_code == 404


def test_welcome_screen_without_plugins(make_client: MakeClient):
    client = make_client()
    complete_setup(client)
    text = client.get("/").text
    assert "Connect a service" in text
    # The running version, in the bottom-left corner.
    assert f'title="What changed in each version">myOrb v{__version__}</button>' in text
    # Stylesheets and scripts carry the version, so a CDN that keeps static files for hours
    # (Cloudflare's Browser Cache TTL overrides no-cache) cannot serve an old one after a release.
    assert f"/static/welcome.css?v={__version__}" in text
    assert f"/static/welcome.js?v={__version__}" in text
    assert '/static/welcome.css"' not in text
    assert "Online · 0/0 sources" in client.get("/welcome/widgets").text


def test_server_app_trusts_forwarded_headers_only_from_proxies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MYORB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYORB_TRUSTED_PROXIES", "127.0.0.1")

    from myorb.app import create_server_app

    # A request through a local proxy is treated as coming from the forwarded client,
    # so it must not get the loopback-only setup form.
    forwarded = {"X-Forwarded-For": "203.0.113.5", "X-Forwarded-Proto": "https"}
    with TestClient(create_server_app(), client=("127.0.0.1", 50000)) as client:
        assert 'name="password"' in client.get("/setup").text
        assert 'name="password"' not in client.get("/setup", headers=forwarded).text

    # Untrusted clients cannot pretend to be local.
    with TestClient(create_server_app(), client=("198.51.100.7", 50000)) as client:
        local = {"X-Forwarded-For": "127.0.0.1"}
        assert 'name="password"' not in client.get("/setup", headers=local).text


# Installable app (PWA)


@pytest.mark.parametrize("setup_done", [False, True])
def test_app_files_are_public(make_client: MakeClient, setup_done: bool):
    client = make_client()
    if setup_done:
        complete_setup(client)
        client.cookies.clear()
    manifest = client.get("/manifest.webmanifest", follow_redirects=False)
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["display"] == "standalone"
    worker = client.get("/sw.js", follow_redirects=False)
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("text/javascript")
    assert "__VERSION__" not in worker.text
    assert client.get("/static/offline.html").status_code == 200


def test_manifest_icons_exist(make_client: MakeClient):
    client = make_client()
    for icon in client.get("/manifest.webmanifest").json()["icons"]:
        response = client.get(icon["src"])
        assert response.status_code == 200
        assert response.content.startswith(b"\x89PNG")


def test_pages_link_the_manifest(make_client: MakeClient):
    client = make_client()
    complete_setup(client)
    for path in ["/", "/tasks", "/login"]:
        text = client.get(path).text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in text
        assert "app.js" in text


# Greeting


def test_name_in_greeting(make_client: MakeClient):
    client = make_client()
    token = client.app.state.auth.setup_token
    data = {"password": "correct horse battery", "password_confirmation": "correct horse battery"}
    client.post("/setup", data={**data, "token": token, "name": "  Tony   "})
    assert ', <span class="greeting-name">Tony</span>' in client.get("/").text

    response = client.post("/settings/profile", data={"name": "<Pepper>"})
    assert "The profile was saved." in response.text
    assert 'value="&lt;Pepper&gt;"' in response.text
    assert '<span class="greeting-name">&lt;Pepper&gt;</span>' in client.get("/").text

    client.post("/settings/profile", data={"name": ""})
    assert "greeting-name" not in client.get("/").text


def test_assistant_is_named_by_the_user(make_client: MakeClient):
    client = make_client()
    token = client.app.state.auth.setup_token
    data = {"password": "correct horse battery", "password_confirmation": "correct horse battery"}
    assert 'value="Orb"' in client.get("/setup", params={"token": token}).text
    client.post("/setup", data={**data, "token": token, "assistant": "Iris"})
    page = client.get("/").text
    assert '<span class="core-name" style="--chars: 4">Iris</span>' in page
    assert "<title>Iris</title>" in page
    assert client.get("/manifest.webmanifest").json()["name"] == "Iris"
    assert "<title>Tasks · Iris</title>" in client.get("/tasks").text

    client.post("/settings/profile", data={"name": "Tony", "assistant": "  HAL   9000 "})
    assert '<span class="core-name" style="--chars: 8">HAL 9000</span>' in client.get("/").text
    # Saving only the user's name keeps the assistant's; an empty name falls back to the default.
    client.post("/settings/profile", data={"name": "Pepper"})
    assert 'value="HAL 9000"' in client.get("/settings/security").text
    client.post("/settings/profile", data={"name": "Pepper", "assistant": ""})
    assert '<span class="core-name" style="--chars: 3">Orb</span>' in client.get("/").text


def test_orb_color_is_chosen_by_the_user(make_client: MakeClient):
    client = make_client()
    complete_setup(client)
    assert '--orb-hue: 0deg"' in client.get("/").text
    assert 'value="cyan" checked' in client.get("/settings/security").text

    client.post("/settings/profile", data={"name": "Tony", "color": "magenta"})
    assert '--orb-hue: 120deg"' in client.get("/").text
    assert 'value="magenta" checked' in client.get("/settings/security").text
    # An unknown color or none (e.g. the name saved elsewhere) keeps the chosen one.
    client.post("/settings/profile", data={"name": "Tony", "color": "plaid"})
    client.post("/settings/profile", data={"name": "Tony"})
    assert '--orb-hue: 120deg"' in client.get("/").text


def test_static_files_are_revalidated(make_client: MakeClient):
    response = make_client().get("/static/style.css")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers.get("etag")


class CountingPlugin(Plugin):
    """A widget whose data comes through the cache, counting how often it is fetched."""

    id = "counting"
    name = "Counting"
    position = 1

    def __init__(self) -> None:
        self.fetches = 0
        self.fresh = 60

    def widgets(self) -> list[Widget]:
        return [Widget(id="n", label="N", title="N", icon="M0 0", load=self._widget, refresh=120)]

    async def _widget(self, context: WidgetContext) -> WidgetData:
        async def fetch() -> int:
            self.fetches += 1
            return self.fetches

        cached = await context.cache.get("counting", fetch, self.fresh, 3600)
        return WidgetData(value=cached.value, updated_at=cached.fetched_at)


def test_due_widget_is_fetched_again(make_client: MakeClient):
    plugin = CountingPlugin()
    client = make_client(plugins={"counting": plugin})
    complete_setup(client)
    text = client.get("/welcome/widget/counting/n").text
    assert 'data-refresh="120"' in text and 'data-age="0' in text
    client.get("/welcome/widget/counting/n")
    assert plugin.fetches == 1  # fresh: from memory
    due = "/welcome/widget/counting/n?due=true"
    # Due while the data is still fresh (a source with its own slower pace): kept.
    assert '<span class="node-value">1</span>' in client.get(due).text
    # Due and stale: the service is asked again now, not in the background.
    plugin.fresh = 0
    assert '<span class="node-value">2</span>' in client.get(due).text


def test_a_side_panel_can_add(make_client: MakeClient):
    from myorb.tasks.plugin import TasksPlugin

    client = make_client(plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    text = client.get("/").text
    # Tasks' head has Add instead of the source's name; it opens a one-line form.
    assert 'data-add="add-tasks-today">Add</button>' in text
    assert 'hx-post="/tasks" hx-swap="none" data-add-form' in text


def test_responses_say_the_running_version(make_client: MakeClient):
    from myorb import __version__

    client = make_client()
    complete_setup(client)
    response = client.get("/")
    assert response.headers["X-Myorb-Version"] == __version__
    assert f'<meta name="myorb-version" content="{__version__}">' in response.text


def test_versions_with_what_changed(make_client: MakeClient):
    import httpx2

    from myorb import __version__
    from myorb.web import releases

    body = (
        "## What's Changed\n"
        "* Tasks that repeat by @someone in https://github.com/x/y/pull/137\n"
        "* Version 0.46.2 by @someone in https://github.com/x/y/pull/147\n\n"
        "**Full Changelog**: https://github.com/x/y/compare/v1...v2"
    )
    calls = []

    def github(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        data = [
            {
                "tag_name": f"v{__version__}",
                "published_at": "2026-10-27T14:17:26Z",
                "html_url": "https://github.com/x/y/releases/tag/v1",
                "body": body,
            },
            {
                "tag_name": "v0.1.0",
                "published_at": "2026-10-20T10:00:00Z",
                "html_url": "",
                "body": "",
            },
        ]
        return httpx2.Response(200, json=data)

    releases._cache.update(at=0.0, releases=None)
    client = make_client(transport=httpx2.MockTransport(github))
    complete_setup(client)
    assert "data-open-releases" in client.get("/").text
    page = client.get("/releases").text
    assert f"v{__version__}</a>" in page and ">Running<" in page and "v0.1.0" in page
    assert '<a href="https://github.com/x/y/pull/137"' in page and ">Tasks that repeat<" in page
    assert "Version 0.46.2" not in page and "Full Changelog" not in page
    client.get("/releases")
    assert len(calls) == 1  # kept, not read again
    releases._cache.update(at=0.0, releases=None)
