import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select

from myorb.brief.models import Brief, ClaudeUsage
from myorb.brief.plugin import BriefPlugin
from myorb.brief.service import kind_for
from myorb.brief.spend import cost_usd, tokens
from myorb.brief.writer import BriefError, facts_prompt
from myorb.connections.models import ProviderSettings
from myorb.plugins import BriefSection
from myorb.tasks.plugin import TasksPlugin

API_KEY = "sk-ant-test-key"
ECB_RATES = """<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-10-23">
<Cube currency="USD" rate="1.25"/><Cube currency="JPY" rate="160.1"/></Cube></Cube>
</gesmes:Envelope>"""
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
        # What a streamed request (Ask) answers, in pieces.
        self.answer = ["You have ", "**one task** today: ", "water the plants."]
        # Tools the next streamed answer calls, as (name, input), once each.
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        # And those the answers after it call, in turn (e.g. reading before proposing).
        self.later_tool_calls: list[list[tuple[str, dict[str, Any]]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "www.ecb.europa.eu":
            # One euro buys 1.25 dollars, so a dollar is 0.80 euros.
            return httpx2.Response(200, text=ECB_RATES)
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
            if self.requests[-1].get("stream"):
                return self.stream(self.requests[-1]["model"])
            text = "" if self.stop_reason == "refusal" else self.reply
            # A plain-text answer (an email reply) as it is, else the JSON asked for.
            text = text if isinstance(text, str) else json.dumps(text)
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

    def stream(self, model: str) -> httpx2.Response:
        """The answer as the API streams it: server-sent events, text in deltas; then the
        tool calls asked for (self.tool_calls), which end the turn with "tool_use"."""
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 1},
        }
        events = [
            {"type": "message_start", "message": message},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            *(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": t},
                }
                for t in self.answer
            ),
            {"type": "content_block_stop", "index": 0},
        ]
        calls = self.tool_calls
        self.tool_calls = self.later_tool_calls.pop(0) if self.later_tool_calls else []
        for index, (name, data) in enumerate(calls, start=1):
            block = {
                "type": "tool_use",
                "id": f"toolu_{len(self.requests)}_{index}",
                "name": name,
                "input": {},
            }
            delta = {"type": "input_json_delta", "partial_json": json.dumps(data)}
            events += [
                {"type": "content_block_start", "index": index, "content_block": block},
                {"type": "content_block_delta", "index": index, "delta": delta},
                {"type": "content_block_stop", "index": index},
            ]
        events += [
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use" if calls else self.stop_reason,
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 20},
            },
            {"type": "message_stop"},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx2.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )


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
    assert "Leave out codes and identifiers" in request["system"]  # e.g. a flight number
    assert request["system"].startswith("You are Orb, a personal assistant.")
    facts = request["messages"][0]["content"]
    assert "## Tasks" in facts
    assert "Water plants" in facts
    assert "Write the brief in English." in facts

    [brief] = briefs(client)
    assert brief.sources == ["Tasks"]
    assert "written: One thing needs you &lt;today&gt;." in client.get("/activity").text
    assert brief.content["priorities"][1]["tone"] == "neutral"

    headline = client.get("/brief/headline").text
    assert "One thing needs you &lt;today&gt;." in headline
    assert "Full report →" in headline
    report = client.get("/brief/report").text
    assert "A quiet day." in report and "Water the plants." in report
    assert "Water plants" in report
    assert 'data-brief-language="en"' in report
    assert "Claude Sonnet 5.5" in report


def test_yesterdays_brief_says_so(client: TestClient, claude: FakeClaude):
    connect(client, brief_time="23:59")
    client.post("/brief/generate", headers={"HX-Request": "true"})
    assert "YESTERDAY" not in client.get("/brief/report").text
    assert briefs(client)[0].content["timeline"] == []  # the day's calendar, kept with it

    async def keep(day_offset: int, title: str) -> None:
        async with client.app.state.sessionmaker() as db:
            brief = await db.scalar(select(Brief))
            day = (datetime.now(TZ) + timedelta(days=day_offset)).replace(hour=9, minute=0)
            event = {"start": day.isoformat(), "end": (day + timedelta(hours=1)).isoformat()}
            brief.content = {**brief.content, "timeline": [{**event, "title": title}]}
            await db.commit()

    # Today's brief shows the calendar it was written from, with "now" still moving.
    client.portal.call(keep, 0, "Kept standup")
    report = client.get("/brief/report").text
    assert "Kept standup" in report and "● Now" in report
    # Hovering an event tells how long it takes (its time is in the app's time zone).
    assert "<strong>Kept standup</strong>" in report
    assert '<span class="glance-tip-length">1 h</span>' in report

    async def keep_ahead(title: str) -> None:
        async with client.app.state.sessionmaker() as db:
            brief = await db.scalar(select(Brief))
            start = datetime.now(TZ) + timedelta(hours=1)
            event = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
            brief.content = {**brief.content, "timeline": [{**event, "title": title}]}
            await db.commit()

    # A meeting cancelled since the brief was written: the report says to write it again.
    client.portal.call(keep_ahead, "Cancelled review")
    report = client.get("/brief/report").text
    assert "The calendar changed since this brief was written" in report
    assert "<strong>Cancelled review</strong> at" in report and "Write it again" in report
    client.portal.call(keep, 0, "Kept standup")

    async def age_brief(days: int) -> None:
        async with client.app.state.sessionmaker() as db:
            brief = await db.scalar(select(Brief))
            brief.created_at = datetime.now(UTC) - timedelta(days=days)
            await db.commit()

    client.post("/tasks", data={"text": "Not on yesterday's calendar"})
    client.portal.call(age_brief, 1)
    report = client.get("/brief/report").text
    # The calendar of the brief's own day, as the brief saw it, not today's.
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%A")
    assert f"{yesterday} · 0 events" in report and "● Now" not in report

    async def keep_event() -> None:
        async with client.app.state.sessionmaker() as db:
            brief = await db.scalar(select(Brief))
            day = brief.created_at.astimezone(TZ).replace(hour=9, minute=0)
            event = {"start": day.isoformat(), "end": (day + timedelta(hours=1)).isoformat()}
            brief.content = {**brief.content, "timeline": [{**event, "title": "Standup"}]}
            await db.commit()

    client.portal.call(keep_event)
    report = client.get("/brief/report").text
    assert f"{yesterday} · 1 event" in report and "Standup" in report
    assert "This is yesterday&#39;s brief." in report or "This is yesterday's brief." in report
    assert "Write today&#39;s now" in report or "Write today's now" in report
    headline = client.get("/brief/headline").text
    assert "One thing needs you" not in headline
    assert "Yesterday&#39;s report →" in headline or "Yesterday's report →" in headline

    client.portal.call(age_brief, 3)
    assert "OLDER" in client.get("/brief/report").text
    assert "Last report →" in client.get("/brief/headline").text


def test_priorities_panel_shows_todays_brief(make_client: MakeClient, claude: FakeClaude):
    client = make_client(
        transport=httpx2.MockTransport(claude),
        plugins={"tasks": TasksPlugin(), "brief": BriefPlugin()},
    )
    complete_setup(client)
    assert "/panels/brief/priorities" in client.get("/").text
    assert "Connect Claude" in client.get("/panels/brief/priorities").text

    connect(client)
    assert "No brief yet today." in client.get("/panels/brief/priorities").text
    client.post("/brief/generate")
    panel = client.get("/panels/brief/priorities").text
    assert '<span class="badge badge-info">Today</span>' in panel
    assert "Water plants" in panel and "From your list" in panel
    assert "data-open-report" in panel

    # Yesterday's brief is stale: its priorities are not shown.
    async def age() -> None:
        async with client.app.state.sessionmaker() as db:
            for brief in await db.scalars(select(Brief)):
                brief.created_at -= timedelta(days=1)
            await db.commit()

    client.portal.call(age)
    assert "No brief yet today." in client.get("/panels/brief/priorities").text


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


# What myOrb spends on Claude


def usage_rows(client: TestClient) -> list[ClaudeUsage]:
    async def query() -> list[ClaudeUsage]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(ClaudeUsage)))

    return client.portal.call(query)


def test_cost_counts_each_kind_of_token():
    usage = {
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 3_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 2_000, "ephemeral_1h_input_tokens": 1_000},
        "cache_read_input_tokens": 10_000,
        "output_tokens": 500,
    }
    counted = tokens(usage)
    assert counted == {
        "input_tokens": 1_000,
        "cache_write_5m": 2_000,
        "cache_write_1h": 1_000,
        "cache_read": 10_000,
        "output_tokens": 500,
    }
    # Sonnet 5.5: $2 input, $2.50 / $4 cache writes, $0.10 cache reads, $10 output per MTok.
    assert cost_usd("claude-sonnet-5-5", counted) == pytest.approx(
        (1_000 * 2 + 2_000 * 2.5 + 1_000 * 4 + 10_000 * 0.1 + 500 * 10) / 1e6
    )
    # Without the split, cache writes count as 5-minute ones.
    assert tokens({"input_tokens": 1, "cache_creation_input_tokens": 7})["cache_write_5m"] == 7
    # Haiku 5.5 costs five times as much past 100,000 prompt tokens.
    short = cost_usd("claude-haiku-5-5", tokens({"input_tokens": 100_000, "output_tokens": 0}))
    long = cost_usd("claude-haiku-5-5", tokens({"input_tokens": 100_001, "output_tokens": 0}))
    assert long == pytest.approx(short * 5, rel=1e-4)


def test_spend_is_counted_and_shown_in_euros(client: TestClient, claude: FakeClaude):
    connect(client)
    assert "in the last 30 days" in client.get("/integrations").text
    assert "€0.00 in the last 30 days" in client.get("/integrations").text

    client.post("/brief/generate", headers={"HX-Request": "true"})
    [row] = usage_rows(client)
    assert (row.model, row.input_tokens, row.output_tokens) == ("claude-sonnet-5-5", 100, 50)
    assert row.cost_usd == pytest.approx((100 * 2 + 50 * 10) / 1e6)

    # Answers priced at $40 in all: €32.00 at 1.25 dollars a euro.
    async def add() -> None:
        async with client.app.state.sessionmaker() as db:
            fields = {k: 0 for k in ("input_tokens", "cache_write_5m", "cache_write_1h")}
            fields |= {"cache_read": 0, "output_tokens": 0, "model": "claude-opus-5-5"}
            db.add(ClaudeUsage(at=datetime.now(UTC), cost_usd=40 - row.cost_usd, **fields))
            # Older than 30 days: not counted.
            db.add(ClaudeUsage(at=datetime.now(UTC) - timedelta(days=31), cost_usd=99, **fields))
            await db.commit()

    client.portal.call(add)
    assert "€32.00 in the last 30 days" in client.get("/integrations").text


# Reading aloud in advance


def test_new_brief_is_read_in_advance(make_client: MakeClient, claude: FakeClaude):
    """With Azure Speech, a new brief is read right away in its own language and the ones
    chosen to preload, so Read aloud plays it without waiting."""
    azure_host = "westeurope.tts.speech.microsoft.com"
    spoken: list[str] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        if request.url.host != azure_host:
            return claude(request)
        if request.url.path.endswith("/voices/list"):
            voices = [
                {"ShortName": name, "LocalName": name, "Gender": "Female", "Locale": name[:5]}
                | {"VoiceType": "Neural"}
                for name in ("en-GB-SoniaNeural", "sk-SK-ViktoriaNeural")
            ]
            return httpx2.Response(200, json=voices)
        spoken.append(request.content.decode())
        return httpx2.Response(200, content=b"ID3 mp3 bytes")

    client = make_client(transport=httpx2.MockTransport(transport), plugins={})
    complete_setup(client)
    connect(client)
    form = {
        "provider": "azure",
        "azure_key": "azure-key",
        "azure_region": "westeurope",
        "voices_shown": "1",
        "preload_sk": "on",
    }
    client.post("/integrations/voice", data=form)
    page = client.get("/integrations/voice").text
    assert 'name="preload_sk" checked' in page
    # The brief is written in English: always read in advance.
    assert 'name="preload_en"' not in page

    client.post("/brief/generate")
    [brief] = briefs(client)
    for _ in range(50):
        if len(spoken) == 2:
            break
        time.sleep(0.05)
    assert len(spoken) == 2
    assert any("A quiet day." in text for text in spoken)
    assert any("Pokojný deň." in text for text in spoken)

    for language in ("en", "sk"):
        audio = client.get(f"/brief/{brief.id}/audio", params={"language": language})
        assert audio.headers["content-type"] == "audio/mpeg"
    assert len(spoken) == 2  # played from what was read in advance


def test_calendar_changes_since_the_brief():
    from myorb.brief.routes import calendar_changes
    from myorb.plugins.base import TimelineItem

    now = datetime(2026, 10, 27, 11, 0, tzinfo=TZ)

    def item(title: str, hour: int) -> TimelineItem:
        return TimelineItem(now.replace(hour=hour), now.replace(hour=hour + 1), title)

    kept = [item("Standup", 9), item("Review", 13), item("Sync", 15), item("1:1", 16)]
    current = [item("Standup", 10), item("Review", 14), item("Sync", 15), item("Demo", 17)]
    changes = [
        (c["kind"], c["item"].title, c["item"].start.hour)
        for c in calendar_changes(kept, current, now)
    ]
    # The standup is over: moving it changes nothing about the rest of the day.
    assert changes == [("moved", "Review", 14), ("cancelled", "1:1", 16), ("added", "Demo", 17)]
    assert calendar_changes(kept, kept, now) == []


def test_priorities_at_a_time_are_completed_once_it_is_over():
    from types import SimpleNamespace

    from myorb.brief.plugin import completed

    now = datetime(2026, 10, 27, 13, 20, tzinfo=TZ)
    meeting = now.replace(hour=13, minute=0)
    brief = SimpleNamespace(
        content={
            "timeline": [
                {
                    "start": meeting.isoformat(),
                    "end": (meeting + timedelta(minutes=30)).isoformat(),
                },
            ],
            "priorities": [
                {"tag": "13:00", "title": "Purchase approval meeting"},  # until 13:30
                {"tag": "9:30", "title": "Call"},  # no event: over at 09:30
                {"tag": "REVIEW", "title": "Two review requests"},  # no time: never
                {"tag": "17:00", "title": "Open day"},
            ],
        }
    )
    assert completed(brief, now) == {1}
    assert completed(brief, now.replace(hour=13, minute=31)) == {0, 1}
