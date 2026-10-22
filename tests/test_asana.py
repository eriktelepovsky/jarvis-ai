from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis

from myorb.plugins.asana import AsanaPlugin, Task, due_badge, is_overdue

TOKEN = "asana-token"
HOST = "app.asana.com"


def fake_asana(apis: FakeApis, tasks: dict[str, list[dict]]) -> None:
    def authorized(request: httpx2.Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {TOKEN}"

    def me(request: httpx2.Request) -> httpx2.Response:
        if not authorized(request):
            return httpx2.Response(401, json={"errors": [{"message": "Not Authorized"}]})
        workspaces = [{"gid": gid, "name": gid.title()} for gid in tasks]
        return httpx2.Response(
            200,
            json={
                "data": {"name": "Tony", "email": "tony@stark.example", "workspaces": workspaces}
            },
        )

    def task_list(request: httpx2.Request) -> httpx2.Response:
        params = request.url.params
        assert (params["assignee"], params["completed_since"]) == ("me", "now")
        items = tasks[params["workspace"]]
        start = int(params.get("offset", 0))
        end = start + int(params["limit"])
        page = {"data": items[start:end], "next_page": None}
        if end < len(items):
            page["next_page"] = {"offset": str(end)}
        return httpx2.Response(200, json=page)

    apis.routes[(HOST, "/api/1.0/users/me")] = me
    apis.routes[(HOST, "/api/1.0/tasks")] = task_list


def task(name: str, due: str | None = None, project: str = "Website") -> dict:
    return {
        "name": name,
        "due_on": due,
        "due_at": None,
        "permalink_url": f"https://app.asana.com/0/1/{abs(hash(name))}",
        "projects": [{"name": project}] if project else [],
    }


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"asana": AsanaPlugin()})
    complete_setup(client)
    return client


def test_connect_and_list_tasks(client: TestClient, apis: FakeApis):
    today = datetime.now().date()
    fake_asana(
        apis,
        {
            "home": [task("Pay rent", (today - timedelta(days=2)).isoformat(), "Household")],
            "work": [
                task("Ship <v2>", today.isoformat()),
                task("Write docs", (today + timedelta(days=3)).isoformat()),
                task("Someday", None, ""),
            ]
            + [task(f"Task {n}") for n in range(150)],  # read over two pages
        },
    )
    bad = client.post("/integrations/asana/connect", data={"token": "wrong"})
    assert bad.status_code == 400
    response = client.post("/integrations/asana/connect", data={"token": TOKEN})
    assert "tony@stark.example" in response.text

    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">154</span>' in text
    assert "assigned to you · 1 overdue · 1 due today" in text
    assert "node warn" in text
    assert text.index("Pay rent") < text.index("Ship &lt;v2&gt;") < text.index("Write docs")
    assert text.index("Write docs") < text.index("Someday")
    assert 'data-group-value="Work"' in text and 'data-subgroup-value="Household"' not in text
    assert 'data-filter="overdue"' in text and 'data-filter="soon"' in text


def test_due_badges():
    tz = ZoneInfo("Europe/Bratislava")
    now = datetime(2026, 10, 22, 12, 0, tzinfo=tz)

    def make(due: str | None, at: datetime | None = None) -> Task:
        day = datetime.fromisoformat(due).date() if due else None
        return Task("t", day, at, "u", "w", "p")

    assert due_badge(make("2026-10-20"), now) == "▲ Due 20 Oct"
    assert due_badge(make("2026-10-22"), now) == "Today"
    at = datetime(2026, 10, 22, 15, 30, tzinfo=UTC)
    assert due_badge(make("2026-10-22", at), now) == "Today 17:30"
    assert is_overdue(make("2026-10-22", datetime(2026, 10, 22, 9, 0, tzinfo=UTC)), now)
    assert due_badge(make("2026-10-24"), now) == "Sat"
    assert due_badge(make("2026-11-30"), now) == "30 Nov"
    assert due_badge(make(None), now) == ""
