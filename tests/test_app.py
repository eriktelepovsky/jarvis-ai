from pathlib import Path
from typing import Any

import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient

from jarvis_hud.plugins import (
    Panel,
    Plugin,
    Row,
    Widget,
    WidgetContext,
    WidgetData,
    discover_plugins,
)
from jarvis_hud.plugins.calendar import CalendarPlugin
from jarvis_hud.plugins.gmail import GmailPlugin


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
    assert 'hx-get="http://testserver/welcome/widgets"' in text
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
    assert "Connect a service" in client.get("/").text
    assert "Online · 0/0 sources" in client.get("/welcome/widgets").text


def test_server_app_trusts_forwarded_headers_only_from_proxies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_TRUSTED_PROXIES", "127.0.0.1")

    from jarvis_hud.app import create_server_app

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
    assert "Your name was saved." in response.text
    assert 'value="&lt;Pepper&gt;"' in response.text
    assert '<span class="greeting-name">&lt;Pepper&gt;</span>' in client.get("/").text

    client.post("/settings/profile", data={"name": ""})
    assert "greeting-name" not in client.get("/").text
