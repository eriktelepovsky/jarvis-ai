import time
from urllib.parse import parse_qs

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis

from myorb.plugins.slack import SlackPlugin, plain_text

TOKEN = "xoxp-good"


def slack_api(
    apis: FakeApis,
    matches: list[dict],
    scope_ok: bool = True,
    dms: list[dict] | None = None,
    read: dict[str, float] | None = None,
    dm_scope_ok: bool = True,
) -> None:
    """`dms` are what a search of the last days finds; `read` is each conversation's last read
    message time."""

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
            if method in ("users.conversations", "conversations.info") and not dm_scope_ok:
                return httpx2.Response(
                    200, json={"ok": False, "error": "missing_scope", "needed": "im:read"}
                )
            if method == "users.conversations":
                return httpx2.Response(200, json={"ok": True, "channels": []})
            if method == "conversations.info":
                channel = form["channel"]
                return httpx2.Response(
                    200,
                    json={
                        "ok": True,
                        "channel": {
                            "id": channel,
                            "is_mpim": channel.startswith("G"),
                            "last_read": str((read or {}).get(channel, 0)),
                        },
                    },
                )
            if form["query"].startswith("after:"):
                return httpx2.Response(
                    200,
                    json={"ok": True, "messages": {"matches": dms or [], "paging": {"pages": 1}}},
                )
            if form["query"] == "test":
                return httpx2.Response(200, json={"ok": True, "messages": {"matches": []}})
            assert form["query"].startswith("<@U1> after:")
            return httpx2.Response(200, json={"ok": True, "messages": {"matches": matches}})

        return respond

    for method in ("auth.test", "search.messages", "users.conversations", "conversations.info"):
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


def test_unread_direct_messages(client: TestClient, apis: FakeApis):
    now = time.time()
    dm = {"id": "D1", "is_im": True, "name": "U2"}
    group = {"id": "G1", "is_mpim": True, "name": "mpdm-tony--pepper--happy-1"}
    slack_api(
        apis,
        [],
        dms=[
            match("are you free at 3?", 0.5, **dm),
            match("and bring the slides", 0.4, **dm),
            match("lunch on friday?", 5, **group),
            match("my own reply", 0.1, user="U1", **dm),
            match("in a channel", 0.2, id="C1"),
            match("read already", 30, **(dm | {"id": "D3"})),
        ],
        # The group message was read; in D3 everything was read.
        read={"G1": now, "D3": now},
    )
    client.post("/integrations/slack/connect", data={"token": TOKEN})
    text = client.get("/welcome/widgets").text
    assert '<span class="node-label">DMs</span>' in text
    assert "unread in 1 conversation" in text
    assert "and bring the slides" in text
    assert "Direct message · pepper · 2 unread" in text
    for hidden in ("lunch on friday", "my own reply", "in a channel", "read already"):
        assert hidden not in text


def test_direct_messages_need_their_permissions(client: TestClient, apis: FakeApis):
    slack_api(apis, [], dms=[match("hi", 1, id="D1", is_im=True)], dm_scope_ok=False)
    response = client.post("/integrations/slack/connect", data={"token": TOKEN})
    assert "Needs the im:read and mpim:read permissions" in response.text
    # Mentions keep working; the direct messages say what is missing.
    text = client.get("/welcome/widgets").text
    assert "lacks the im:read permission" in text
