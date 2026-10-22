import time
from urllib.parse import parse_qs

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis

from jarvis_hud.plugins.slack import SlackPlugin, plain_text

TOKEN = "xoxp-good"


def slack_api(apis: FakeApis, matches: list[dict], scope_ok: bool = True) -> None:
    def handle(method: str):
        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return httpx2.Response(200, json={"ok": False, "error": "invalid_auth"})
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if method == "auth.test":
                return httpx2.Response(
                    200,
                    json={
                        "ok": True,
                        "user_id": "U1",
                        "team": "Stark",
                        "url": "https://stark.slack.com/",
                    },
                )
            if not scope_ok:
                return httpx2.Response(200, json={"ok": False, "error": "missing_scope"})
            if form["query"] == "test":
                return httpx2.Response(200, json={"ok": True, "messages": {"matches": []}})
            assert form["query"].startswith("<@U1> after:")
            return httpx2.Response(200, json={"ok": True, "messages": {"matches": matches}})

        return respond

    for method in ("auth.test", "search.messages"):
        apis.routes[("slack.com", f"/api/{method}")] = handle(method)


def match(text: str, hours_ago: float, user: str = "U2", **channel) -> dict:
    return {
        "text": text,
        "user": user,
        "username": "pepper",
        "ts": str(time.time() - hours_ago * 3600),
        "channel": {"name": "dev-backend", **channel},
        "permalink": "https://stark.slack.com/archives/C1/p1",
    }


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"slack": SlackPlugin()})
    complete_setup(client)
    return client


def test_plain_text():
    text = "<@U1> see <#C1|dev> and <https://x.example|the docs> <!here> &amp; <https://y.example>"
    assert plain_text(text) == "@someone see #dev and the docs @here & https://y.example"


def test_connect_page_shows_manifest(client: TestClient):
    text = client.get("/integrations/slack/connect").text
    assert "&#34;search:read&#34;" in text or '"search:read"' in text
    assert "api.slack.com/apps" in text


def test_slack_mentions(client: TestClient, apis: FakeApis):
    slack_api(
        apis,
        [
            match("<@U1> can you check staging?", 0.2),
            match("my own message mentioning <@U1>", 1, user="U1"),
            match("yesterday's", 30),
            match("ping", 2, is_im=True),
        ],
    )
    response = client.post("/integrations/slack/connect", data={"token": TOKEN})
    assert "Stark" in response.text
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">2</span>' in text
    assert "@someone can you check staging?" in text
    assert "#dev-backend · pepper" in text
    assert "Direct message · pepper" in text
    assert "my own message" not in text and "yesterday" not in text


@pytest.mark.parametrize(
    ("token", "message"),
    [("xoxb-bot", "starts with xoxp-"), ("xoxp-wrong", "Slack rejected the token")],
)
def test_wrong_tokens(client: TestClient, apis: FakeApis, token: str, message: str):
    slack_api(apis, [])
    response = client.post("/integrations/slack/connect", data={"token": token})
    assert response.status_code == 400
    assert message in response.text


def test_missing_scope_is_reported(client: TestClient, apis: FakeApis):
    slack_api(apis, [], scope_ok=False)
    response = client.post("/integrations/slack/connect", data={"token": TOKEN})
    assert "lacks the search:read permission" in response.text
