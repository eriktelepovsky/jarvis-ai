"""The actions plugins offer Ask, run as Ask runs them once the user confirms: against fake
services, checking what they send, what they say they did, and that undo puts it back."""

import json
from typing import Any

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis, connect_jira, github, issue, jira_site

from myorb.plugins import ActionError, AskAction, Plugin
from myorb.plugins.asana import AsanaPlugin
from myorb.plugins.code_review import CodeReviewPlugin
from myorb.plugins.jira import JiraPlugin
from myorb.plugins.uptime import UptimePlugin
from myorb.plugins.youtube import YouTubePlugin
from myorb.web.routes import app_context


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    plugins = {
        "jira": JiraPlugin(),
        "asana": AsanaPlugin(),
        "code_review": CodeReviewPlugin(),
        "uptime": UptimePlugin(),
        "youtube": YouTubePlugin(),
    }
    client = make_client(transport=httpx2.MockTransport(apis), plugins=plugins)
    complete_setup(client)
    return client


def action(client: TestClient, plugin_id: str, name: str) -> AskAction:
    plugin: Plugin = client.app.state.plugins[plugin_id]
    return next(a for a in plugin.ask_actions() if a.name == name)


def summary(client: TestClient, found: AskAction, data: dict[str, Any]) -> str:
    return client.portal.call(found.summary, app_context(client.app), data)


def run(client: TestClient, found: AskAction, data: dict[str, Any]):
    return client.portal.call(found.run, app_context(client.app), data)


def undo(client: TestClient, result) -> str:
    return client.portal.call(result.undo, app_context(client.app))


class Recorder:
    """A fake endpoint that remembers what was sent to it."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.status, self.body = status, body

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append((request.method, json.loads(request.content) if request.content else None))
        if self.status == 204:
            return httpx2.Response(204)
        return httpx2.Response(self.status, json=self.body if self.body is not None else {})


# Jira


def test_jira_issue_moves_and_moves_back(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "indeterminate")])
    connect_jira(client, "dev")
    host = "dev.atlassian.net"
    status = {"name": "In Progress"}
    transitions = [
        {"id": "31", "name": "Finish", "to": {"name": "Done"}},
        {"id": "21", "name": "Start", "to": {"name": "In Progress"}},
    ]
    moves = Recorder(204)

    def issue_transitions(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            moves(request)
            status["name"] = next(
                t["to"]["name"]
                for t in transitions
                if t["id"] == json.loads(request.content)["transition"]["id"]
            )
            return httpx2.Response(204)
        return httpx2.Response(200, json={"transitions": transitions})

    apis.routes[(host, "/rest/api/3/issue/TRP-1/transitions")] = issue_transitions
    apis.routes[(host, "/rest/api/3/issue/TRP-1")] = lambda r: httpx2.Response(
        200, json={"fields": {"status": dict(status)}}
    )

    move = action(client, "jira", "move_issue")
    data = {"workspace": "dev", "issue_key": "TRP-1", "status": "done"}
    assert summary(client, move, data) == "Move TRP-1 (dev) to Done"
    with pytest.raises(ActionError, match="It can move to: Done, In Progress"):
        summary(client, move, {**data, "status": "Blocked"})
    with pytest.raises(ActionError, match="No Jira workspace prod"):
        summary(client, move, {**data, "workspace": "prod"})

    result = run(client, move, data)
    assert result.message == "Moved TRP-1 from In Progress to Done."
    assert moves.sent == [("POST", {"transition": {"id": "31"}})]
    assert undo(client, result) == "TRP-1 is back in In Progress."
    assert moves.sent[-1] == ("POST", {"transition": {"id": "21"}})


def test_jira_comment_and_its_removal(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "new")])
    connect_jira(client, "dev")
    comments = Recorder(201, {"id": "100"})
    removed = Recorder(204)
    apis.routes[("dev.atlassian.net", "/rest/api/3/issue/TRP-1/comment")] = comments
    apis.routes[("dev.atlassian.net", "/rest/api/3/issue/TRP-1/comment/100")] = removed

    comment = action(client, "jira", "comment_issue")
    result = run(
        client, comment, {"workspace": "dev", "issue_key": "TRP-1", "comment": "Done.\n\nThanks!"}
    )
    assert result.message == "Commented on TRP-1."
    body = comments.sent[0][1]["body"]
    assert [p["content"][0]["text"] for p in body["content"]] == ["Done.", "Thanks!"]
    assert undo(client, result) == "Removed the comment on TRP-1."
    assert removed.sent == [("DELETE", None)]


# Asana


def test_asana_task_completes_gets_a_due_date_and_a_comment(client: TestClient, apis: FakeApis):
    from test_asana import TOKEN, fake_asana, task

    fake_asana(apis, {"home": [task("Pay rent")]})
    client.post("/integrations/asana/connect", data={"token": TOKEN})
    state = {"gid": "1234", "name": "Pay rent", "due_on": "2026-10-20", "completed": False}
    updates: list[dict] = []

    def one_task(request: httpx2.Request) -> httpx2.Response:
        if request.method == "PUT":
            fields = json.loads(request.content)["data"]
            updates.append(fields)
            state.update(fields)
        return httpx2.Response(200, json={"data": dict(state)})

    apis.routes[("app.asana.com", "/api/1.0/tasks/1234")] = one_task
    stories = Recorder(201, {"data": {"gid": "77"}})
    apis.routes[("app.asana.com", "/api/1.0/tasks/1234/stories")] = stories
    apis.routes[("app.asana.com", "/api/1.0/stories/77")] = Recorder(200, {"data": {}})

    complete = action(client, "asana", "complete_task")
    assert summary(client, complete, {"task_id": "1234"}) == "Complete “Pay rent” in Asana"
    result = run(client, complete, {"task_id": "1234"})
    assert result.message == "Completed “Pay rent” in Asana."
    assert undo(client, result) == "“Pay rent” is open again in Asana."
    assert updates == [{"completed": True}, {"completed": False}]

    due = action(client, "asana", "set_due_date")
    result = run(client, due, {"task_id": "1234", "due_on": "2026-10-30"})
    assert result.message == "“Pay rent” is now due 2026-10-30."
    undo(client, result)
    assert updates[-1] == {"due_on": "2026-10-20"}
    with pytest.raises(ActionError, match="YYYY-MM-DD"):
        summary(client, due, {"task_id": "1234", "due_on": "next week"})

    comment = action(client, "asana", "comment_task")
    result = run(client, comment, {"task_id": "1234", "comment": "Paid."})
    assert stories.sent == [("POST", {"data": {"text": "Paid."}})]
    assert undo(client, result) == "Removed the comment on “Pay rent”."
    with pytest.raises(ActionError, match="by its ref"):
        summary(client, complete, {"task_id": "abc"})


# GitHub


def test_github_pull_request_approved_and_commented(client: TestClient, apis: FakeApis):
    github(apis)
    client.post("/integrations/github/connect", data={"token": "ghp_good"})
    reviews = Recorder(200, {"id": 5})
    comments = Recorder(201, {"id": 9})
    removed = Recorder(204)
    apis.routes[("api.github.com", "/repos/acme/api/pulls/318/reviews")] = reviews
    apis.routes[("api.github.com", "/repos/acme/api/issues/318/comments")] = comments
    apis.routes[("api.github.com", "/repos/acme/api/issues/comments/9")] = removed
    url = "https://github.com/acme/api/pull/318"

    approve = action(client, "code_review", "approve_pull_request")
    assert (
        summary(client, approve, {"url": url, "comment": "LGTM"})
        == "Approve acme/api#318 with “LGTM”"
    )
    result = run(client, approve, {"url": url, "comment": ""})
    assert result.message == "Approved acme/api#318." and result.undo is None
    assert reviews.sent == [("POST", {"event": "APPROVE"})]

    comment = action(client, "code_review", "comment_pull_request")
    result = run(client, comment, {"url": url, "comment": "Rebase, please."})
    assert comments.sent == [("POST", {"body": "Rebase, please."})]
    assert undo(client, result) == "Removed the comment on acme/api#318."
    with pytest.raises(ActionError, match="Only GitHub"):
        summary(
            client, comment, {"url": "https://gitlab.com/a/b/-/merge_requests/1", "comment": "x"}
        )


# Uptime and YouTube


def monitors(client: TestClient) -> list[tuple[str, str]]:
    from sqlalchemy import select

    from myorb.plugins.uptime.models import Monitor

    async def query() -> list[tuple[str, str]]:
        async with client.app.state.sessionmaker() as db:
            return [(m.name, m.group_name) for m in await db.scalars(select(Monitor))]

    return client.portal.call(query)


def test_uptime_monitor_added_and_removed(client: TestClient, apis: FakeApis):
    apis.routes[("example.com", "/health")] = lambda r: httpx2.Response(200, text="ok")
    add = action(client, "uptime", "add_monitor")
    data = {"name": "API", "url": "https://example.com/health", "group": "Acme"}
    assert summary(client, add, data) == "Watch API (https://example.com/health) in Acme"
    result = run(client, add, data)
    assert result.message == "Watching API (https://example.com/health); it is up."
    assert monitors(client) == [("API", "Acme")]
    assert undo(client, result) == "Stopped watching API."
    assert monitors(client) == []
    with pytest.raises(ActionError, match="full address"):
        summary(client, add, {**data, "url": "example.com"})


def test_youtube_videos_marked_seen_and_back(client: TestClient):
    from sqlalchemy import select

    from myorb.plugins.youtube.models import SeenVideo

    def seen() -> list[str]:
        async def query() -> list[str]:
            async with client.app.state.sessionmaker() as db:
                return list(await db.scalars(select(SeenVideo.video_id)))

        return sorted(client.portal.call(query))

    mark = action(client, "youtube", "mark_videos_seen")
    data = {"video_ids": ["abc", "def", "abc", "<bad>"]}
    assert summary(client, mark, data) == "Mark 2 videos as seen"
    result = run(client, mark, data)
    assert seen() == ["abc", "def"]
    assert undo(client, result) == "2 videos not seen again."
    assert seen() == []
