import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select

from myorb.brief.models import Brief
from myorb.brief.service import kind_for
from myorb.brief.writer import BriefError, facts_prompt
from myorb.connections.models import ProviderSettings
from myorb.plugins import BriefSection
from myorb.tasks.plugin import TasksPlugin

API_KEY = "sk-ant-test-key"
TZ = ZoneInfo("Europe/Bratislava")

BRIEF = {
    "headline": "One thing needs you <today>.",
    "summary": ["A quiet day.", "Water the plants."],
    "priorities": [
        {"tag": "Today", "tone": "info", "title": "Water plants", "detail": "From your list"},
        {"tag": "x", "tone": "purple", "title": "Bad tone", "detail": ""},
    ],
}


class FakeClaude:
    """The Anthropic API endpoints myOrb uses, as an httpx2 transport."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []
        self.reply: dict[str, Any] = BRIEF
        self.stop_reason = "end_turn"
        self.status = 200
        self.models = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        key = request.headers.get("x-api-key") or request.headers.get(
            "authorization", ""
        ).removeprefix("Bearer ")
        if key != API_KEY:
            return httpx2.Response(
                401, json={"type": "error", "error": {"type": "authentication_error"}}
            )
        if request.url.path.startswith("/v1/models/"):
            model = request.url.path.removeprefix("/v1/models/")
            if model not in self.models:
                return httpx2.Response(
                    404,
                    json={
                        "type": "error",
                        "error": {"type": "not_found_error", "message": f"model: {model}"},
                    },
                )
            return httpx2.Response(
                200,
                json={
                    "id": model,
                    "type": "model",
                    "display_name": "Claude Opus 5.5",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            )
        if request.url.path == "/v1/messages":
            self.requests.append(json.loads(request.content))
            self.headers.append(request.headers)
            if self.status != 200:
                return httpx2.Response(
                    self.status, json={"type": "error", "error": {"type": "api_error"}}
                )
            text = "" if self.stop_reason == "refusal" else json.dumps(self.reply)
            if "Translate" in str(self.requests[-1].get("system", "")):
                text = "Pokojný deň.\n\nZalej rastliny."
            return httpx2.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": self.requests[-1]["model"],
                    "content": [{"type": "text", "text": text}] if text else [],
                    "stop_reason": self.stop_reason,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            )
        return httpx2.Response(404)


@pytest.fixture
def claude() -> FakeClaude:
    return FakeClaude()


@pytest.fixture
def client(make_client: MakeClient, claude: FakeClaude) -> TestClient:
    transport = httpx2.MockTransport(claude)
    client = make_client(transport=transport, plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    return client


def connect(client: TestClient, **fields: str) -> httpx2.Response:
    data = {"api_key": API_KEY, "language": "en", "brief_time": "06:30", **fields}
    return client.post("/integrations/claude", data=data)


def briefs(client: TestClient) -> list[Brief]:
    async def query() -> list[Brief]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(Brief)))

    return client.portal.call(query)


# Settings


def test_connect_claude_validates_and_encrypts_the_key(client: TestClient):
    response = connect(client, api_key="wrong")
    assert response.status_code == 400
    assert "Claude rejected this API key." in response.text

    response = connect(client)
    assert "Claude is connected" in response.text

    async def stored() -> bytes:
        async with client.app.state.sessionmaker() as db:
            return (await db.get(ProviderSettings, "anthropic")).data

    assert API_KEY.encode() not in client.portal.call(stored)

    # Saving again without a key keeps the stored one.
    response = connect(client, api_key="", language="sk", brief_time="07:15")
    assert "Claude is connected" in response.text
    settings = client.portal.call(client.app.state.brief.settings)
    assert (settings.api_key, settings.language, settings.brief_time) == (API_KEY, "sk", "07:15")


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"brief_time": "25:00"}, "HH:MM"),
        ({"language": "xx"}, "Choose a language"),
        ({"model": "gpt-5"}, "Choose a model"),
    ],
)
def test_invalid_settings(client: TestClient, fields: dict[str, str], error: str):
    response = connect(client, **fields)
    assert response.status_code == 400
    assert error in response.text


def test_disconnect_claude_keeps_written_briefs(client: TestClient):
    connect(client)
    client.post("/brief/generate")
    response = client.post("/integrations/claude/remove")
    assert "Claude was disconnected" in response.text
    assert client.portal.call(client.app.state.brief.settings) is None
    report = client.get("/brief/report").text
    assert "Connect Claude" in report
    assert "A quiet day." in report


# Writing the brief


def test_report_without_claude_asks_to_connect(client: TestClient):
    assert "Connect Claude" in client.get("/brief/report").text
    assert "Daily brief →" in client.get("/brief/headline").text


def test_generate_brief(client: TestClient, claude: FakeClaude):
    connect(client)
    client.post("/tasks", data={"text": "Water plants today"})
    response = client.post("/brief/generate", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "myorb:brief"

    [request] = claude.requests
    assert request["model"] == "claude-sonnet-5-5"
    assert request["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in claude.headers[0]["anthropic-beta"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["effort"] == "medium"
    assert "Never invent" in request["system"]
    assert request["system"].startswith("You are Orb, a personal assistant.")
    facts = request["messages"][0]["content"]
    assert "## To-do list" in facts
    assert "Water plants" in facts
    assert "Write the brief in English." in facts

    [brief] = briefs(client)
    assert brief.sources == ["To-do list"]
    assert brief.content["priorities"][1]["tone"] == "neutral"

    headline = client.get("/brief/headline").text
    assert "One thing needs you &lt;today&gt;." in headline
    assert "Full report →" in headline
    report = client.get("/brief/report").text
    assert "A quiet day." in report and "Water the plants." in report
    assert "Water plants" in report
    assert 'data-brief-language="en"' in report
    assert "Claude Sonnet 5.5" in report


@pytest.mark.parametrize(
    ("model", "effort", "fallback"),
    [("claude-opus-5-5", "medium", True), ("claude-haiku-5-5", "low", False)],
)
def test_chosen_model_is_used(
    client: TestClient, claude: FakeClaude, model: str, effort: str, fallback: bool
):
    connect(client, model=model)
    client.post("/brief/generate")
    [request] = claude.requests
    assert request["model"] == model
    assert request["output_config"]["effort"] == effort
    # Haiku has no server-side refusal fallback, so the request must not ask for one.
    assert ("fallbacks" in request) is fallback
    assert ("server-side-fallback" in claude.headers[0].get("anthropic-beta", "")) is fallback
    name = "Claude Opus 5.5" if fallback else "Claude Haiku 5.5"
    assert f"with {name}." in client.get("/integrations").text


def test_model_change_is_checked(client: TestClient, claude: FakeClaude):
    connect(client)
    claude.models.discard("claude-opus-5-5")
    response = connect(client, api_key="", model="claude-opus-5-5")
    assert response.status_code == 400
    assert "HTTP 404): model: claude-opus-5-5" in response.text
    assert connect(client, model="gpt").status_code == 400


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda fake: setattr(fake, "stop_reason", "refusal"), "declined"),
        (lambda fake: setattr(fake, "stop_reason", "max_tokens"), "cut off"),
        (lambda fake: setattr(fake, "status", 500), "HTTP 500"),
    ],
)
def test_failed_generation_is_shown(client: TestClient, claude: FakeClaude, setup, message: str):
    connect(client)
    setup(claude)
    client.post("/brief/generate", headers={"HX-Request": "true"})
    assert briefs(client) == []
    assert message in client.get("/brief/report").text


def test_brief_requires_sign_in(client: TestClient):
    client.cookies.clear()
    assert client.post("/brief/generate").status_code == 401
    assert client.get("/integrations/claude", follow_redirects=False).status_code == 303


# Scheduling


def test_scheduled_brief_is_due_once_a_day(client: TestClient, claude: FakeClaude):
    service = client.app.state.brief
    assert client.portal.call(service.due) is False  # not connected
    now = datetime.now(TZ)
    # A brief time already passed today makes it due; one later today does not.
    connect(client, brief_time="00:00")
    assert client.portal.call(service.due) is True
    client.portal.call(service.generate)
    assert client.portal.call(service.due) is False

    # Tomorrow's brief is due again, at the set time.
    async def age_brief() -> None:
        async with client.app.state.sessionmaker() as db:
            brief = await db.scalar(select(Brief))
            brief.created_at = datetime.now(UTC) - timedelta(days=1)
            await db.commit()

    client.portal.call(age_brief)
    service._last_attempt = None
    assert client.portal.call(service.due) is True
    if now.hour < 23:
        connect(client, brief_time="23:59")
        assert client.portal.call(service.due) is False


def test_failed_attempt_waits_before_retrying(client: TestClient, claude: FakeClaude):
    connect(client, brief_time="00:00")
    claude.status = 500
    with pytest.raises(BriefError):
        client.portal.call(client.app.state.brief.generate)
    assert client.portal.call(client.app.state.brief.due) is False


# Helpers


@pytest.mark.parametrize(
    ("hour", "kind"), [(3, "evening"), (6, "morning"), (12, "midday"), (18, "evening")]
)
def test_kind_for(hour: int, kind: str):
    assert kind_for(hour) == kind


def test_facts_prompt():
    now = datetime(2026, 10, 22, 7, 42, tzinfo=TZ)
    prompt = facts_prompt([BriefSection("Calendar", ["Today 09:00: Standup"])], now, "sk")
    assert "Thursday 22 October 2026, 07:42" in prompt
    assert "Write the brief in Slovak." in prompt
    assert "## Calendar\n- Today 09:00: Standup" in prompt


def test_workspace_id_is_sent_for_organization_keys(client: TestClient, claude: FakeClaude):
    response = connect(client, workspace_id="my workspace")
    assert response.status_code == 400
    assert "wrkspc_" in response.text
    connect(client, workspace_id="wrkspc_01AbCdEf234")
    assert 'value="wrkspc_01AbCdEf234"' in client.get("/integrations/claude").text
    client.post("/brief/generate")
    assert claude.headers[0]["anthropic-workspace-id"] == "wrkspc_01AbCdEf234"


def test_brief_is_translated_once_for_reading_aloud(client: TestClient, claude: FakeClaude):
    connect(client)
    client.post("/brief/generate")
    [brief] = briefs(client)
    report = client.get("/brief/report").text
    assert f'data-brief="{brief.id}"' in report and '<option value="sk" data-tag="sk-SK"' in report

    same = client.post(f"/brief/{brief.id}/translation", data={"language": "en"}).json()
    assert same == {"text": "A quiet day.\n\nWater the plants.", "lang": "en-GB"}
    assert len(claude.requests) == 1

    for _ in range(2):
        response = client.post(f"/brief/{brief.id}/translation", data={"language": "sk"})
        assert response.json() == {"text": "Pokojný deň.\n\nZalej rastliny.", "lang": "sk-SK"}
    assert len(claude.requests) == 2  # translated once, then kept with the brief
    assert claude.requests[1]["model"] == "claude-haiku-5-5"
    assert "Slovak" in claude.requests[1]["system"]

    bad = client.post(f"/brief/{brief.id}/translation", data={"language": "xx"})
    assert bad.status_code == 400 and bad.json()["error"] == "Choose a language."
