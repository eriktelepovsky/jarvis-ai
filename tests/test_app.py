from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis_hud.app import create_app
from jarvis_hud.config import Settings
from jarvis_hud.plugins import Plugin, Widget, discover_plugins
from jarvis_hud.plugins.core import CorePlugin, greeting


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path), plugins={"core": CorePlugin()})
    with TestClient(app) as client:
        yield client


def test_core_plugin_is_discovered_via_entry_point():
    assert isinstance(discover_plugins()["core"], CorePlugin)


def test_dashboard_lazy_loads_widgets(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert 'hx-get="http://testserver/widgets/core/today"' in response.text


def test_widget_renders_plugin_template(client: TestClient):
    response = client.get("/widgets/core/today")
    assert response.status_code == 200
    assert '<p class="greeting">' in response.text


@pytest.mark.parametrize("path", ["/widgets/missing/today", "/widgets/core/missing"])
def test_unknown_widget_returns_404(client: TestClient, path: str):
    assert client.get(path).status_code == 404


def test_widget_context_is_autoescaped(tmp_path: Path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "echo.html").write_text("{{ value }}")

    class EchoPlugin(Plugin):
        id = "echo"
        name = "Echo"
        templates_dir = tmp_path / "templates"

        def widgets(self) -> list[Widget]:
            async def load() -> dict[str, Any]:
                return {"value": "<script>"}

            return [Widget(id="echo", title="Echo", template="echo.html", load=load)]

    app = create_app(Settings(data_dir=tmp_path / "data"), plugins={"echo": EchoPlugin()})
    with TestClient(app) as client:
        assert client.get("/widgets/echo/echo").text == "&lt;script&gt;"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(4, "Good evening"), (5, "Good morning"), (12, "Good afternoon"), (18, "Good evening")],
)
def test_greeting(hour: int, expected: str):
    assert greeting(hour) == expected
