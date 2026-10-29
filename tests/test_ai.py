"""Another AI than Claude, through an OpenAI-compatible API (brief/compatible.py)."""

import json
from typing import Any

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient

from myorb.brief.compatible import chat_messages
from myorb.tasks.plugin import TasksPlugin

BASE = "http://ollama.test:11434/v1"
BRIEF = {
    "headline": "One task today.",
    "summary": ["Water the plants."],
    "priorities": [{"tag": "Today", "tone": "info", "title": "Plants", "detail": "Water them"}],
}


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


class FakeOllama:
    """An OpenAI-compatible API: its models, and answers queued for chat completions."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.answers: list[httpx2.Response] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != "ollama.test":
            raise httpx2.ConnectError("No route to host", request=request)
        if request.url.path == "/v1/models":
            return httpx2.Response(200, json={"data": [{"id": "llama3.2"}, {"id": "qwen3"}]})
        self.requests.append(json.loads(request.content))
        return self.answers.pop(0)


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def client(make_client: MakeClient, ollama: FakeOllama) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(ollama), plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    return client


def connect(client: TestClient, model: str = "llama3.2") -> Any:
    form = {"service": "ollama", "base_url": BASE, "model_name": model, "language": "en"}
    return client.post("/integrations/claude", data=form | {"brief_time": "06:30"})


def test_a_local_model_writes_the_brief(client: TestClient, ollama: FakeOllama):
    wrong = connect(client, "llama9")
    assert wrong.status_code == 400
    assert "Ollama has no model llama9. It offers: llama3.2, qwen3" in wrong.text
    assert connect(client).url.path == "/integrations"  # no API key needed
    page = client.get("/integrations").text
    assert "<strong>Ollama</strong>" in page and "with llama3.2" in page
    field = 'name="model_name" value="llama3.2"'
    assert field in client.get("/integrations/claude").text and "<function" not in page

    client.post("/command", data={"text": "Water the plants today"})
    content = json.dumps(BRIEF)
    ollama.answers.append(
        httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    client.post("/brief/generate")
    [request] = ollama.requests
    assert request["model"] == "llama3.2"
    assert request["response_format"]["json_schema"]["schema"]["required"] == [
        "headline",
        "summary",
        "priorities",
    ]
    system, facts = request["messages"]
    assert system["role"] == "system" and "daily brief" in system["content"]
    assert "Water the plants" in facts["content"]
    assert "One task today." in client.get("/brief/headline").text
    assert "Ollama settings" not in client.get("/brief/report").text  # no error


def test_a_service_without_json_schemas_is_asked_for_json(client: TestClient, ollama: FakeOllama):
    connect(client)
    ollama.answers += [
        httpx2.Response(400, json={"error": {"message": "response_format not supported"}}),
        httpx2.Response(200, json={"choices": [{"message": {"content": json.dumps(BRIEF)}}]}),
    ]
    client.post("/brief/generate")
    assert ollama.requests[1]["response_format"] == {"type": "json_object"}
    assert "Reply with JSON only" in ollama.requests[1]["messages"][0]["content"]
    assert "One task today." in client.get("/brief/headline").text


def test_ask_streams_and_proposes_actions(client: TestClient, ollama: FakeOllama):
    connect(client)
    call = {"id": "call_1", "function": {"name": "tasks__add_task", "arguments": ""}}
    ollama.answers.append(
        httpx2.Response(
            200,
            content=sse(
                {"choices": [{"delta": {"content": "Adding it"}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, **call}]}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '{"text": "call mom"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
            headers={"content-type": "text/event-stream"},
        )
    )
    response = client.post("/ask", json={"messages": [{"role": "user", "content": "Add call mom"}]})
    lines = [json.loads(line) for line in response.text.splitlines() if line]
    assert "".join(line.get("text", "") for line in lines) == "Adding it"
    [proposal] = [line["proposal"] for line in lines if "proposal" in line]
    assert proposal == {"id": "call_1", "summary": "Add the task “call mom”"}
    request = ollama.requests[0]
    assert request["stream"] is True
    assert "tasks__add_task" in [tool["function"]["name"] for tool in request["tools"]]

    ollama.answers.append(
        httpx2.Response(200, content=sse({"choices": [{"delta": {"content": "Done."}}]}))
    )
    decided = client.post(
        "/ask/decide", json={"conversation": lines[0]["conversation"], "approved": ["call_1"]}
    )
    assert "Added the task" in decided.text and "Done." in decided.text
    messages = ollama.requests[1]["messages"]
    assert messages[-2]["tool_calls"][0]["function"]["name"] == "tasks__add_task"
    assert messages[-1]["role"] == "tool" and messages[-1]["tool_call_id"] == "call_1"


def test_an_unreachable_service_says_where(client: TestClient):
    form = {"service": "lmstudio", "base_url": "http://nowhere.test:1234/v1", "model_name": "x"}
    response = client.post("/integrations/claude", data=form | {"brief_time": "06:30"})
    assert response.status_code == 400
    assert "LM Studio could not be reached at http://nowhere.test:1234/v1." in response.text


def test_messages_become_chat_completions():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "…"},
                {"type": "text", "text": "Adding"},
                {"type": "tool_use", "id": "t1", "name": "add", "input": {"text": "x"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "No.", "is_error": True},
                {"type": "text", "text": "Why?"},
            ],
        },
    ]
    system = [{"type": "text", "text": "Be brief."}, {"type": "text", "text": "# Dashboard"}]
    assert chat_messages(system, messages) == [
        {"role": "system", "content": "Be brief.\n\n# Dashboard"},
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "Adding",
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"text": "x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "Error: No."},
        {"role": "user", "content": "Why?"},
    ]
