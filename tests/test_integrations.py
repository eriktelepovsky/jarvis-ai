import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select

from myorb.connections.models import Connection
from myorb.plugins.code_review import CodeReviewPlugin
from myorb.plugins.jira import JiraPlugin

Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeApis:
    """Third-party APIs keyed by host and path, as an httpx2 transport."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.calls: list[str] = []

    def json(self, host: str, path: str, body: Any, status: int = 200) -> None:
        self.routes[(host, path)] = lambda request: httpx2.Response(status, json=body)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(f"{request.method} {request.url.host}{request.url.path}")
        handler = self.routes.get((request.url.host, request.url.path))
        return handler(request) if handler else httpx2.Response(404, json={})


def bearer(token: str, body: Any) -> Handler:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("authorization") != f"Bearer {token}":
            return httpx2.Response(401, json={"message": "Bad credentials"})
        return httpx2.Response(200, json=body)

    return handle


def basic(user: str, password: str, body: Any) -> Handler:
    expected = httpx2.BasicAuth(user, password)

    def handle(request: httpx2.Request) -> httpx2.Response:
        probe = next(expected.auth_flow(httpx2.Request("GET", "https://x")))
        if request.headers.get("authorization") != probe.headers["authorization"]:
            return httpx2.Response(401, json={})
        return httpx2.Response(200, json=body)

    return handle


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    plugins = {"code_review": CodeReviewPlugin(), "jira": JiraPlugin()}
    client = make_client(transport=httpx2.MockTransport(apis), plugins=plugins)
    complete_setup(client)
    return client


def connections(client: TestClient) -> list[Connection]:
    async def query() -> list[Connection]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(Connection).order_by(Connection.id)))

    return client.portal.call(query)


# GitHub

GITHUB_PR = {
    "number": 318,
    "title": "feat: auth token refresh",
    "url": "https://github.com/acme/api/pull/318",
    "isDraft": False,
    "updatedAt": "2026-10-22T08:00:00Z",
    "repository": {"nameWithOwner": "acme/api"},
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
}
GITHUB_MINE = {
    **GITHUB_PR,
    "number": 321,
    "title": "chore: bump Django",
    "url": "https://github.com/acme/api/pull/321",
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
    "reviewDecision": "APPROVED",
    "mergeable": "MERGEABLE",
}


def github(apis: FakeApis, token: str = "ghp_good") -> None:
    apis.routes[("api.github.com", "/user")] = bearer(token, {"login": "tony"})
    graphql = {"data": {"review": {"nodes": [GITHUB_PR]}, "mine": {"nodes": [GITHUB_MINE]}}}
    apis.routes[("api.github.com", "/graphql")] = bearer(token, graphql)


def test_integrations_page_lists_form_providers(client: TestClient):
    text = client.get("/integrations").text
    for name in ["GitHub", "GitLab", "Bitbucket", "Jira"]:
        assert f"<strong>{name}</strong>" in text
    assert 'href="/integrations/github/connect"' in text


def test_connect_page_shows_fields_and_instructions(client: TestClient):
    text = client.get("/integrations/github/connect").text
    assert 'name="token"' in text and 'type="password"' in text
    assert "github.com/settings/tokens/new" in text
    assert client.get("/integrations/nothing/connect").status_code == 404


def test_connect_github(client: TestClient, apis: FakeApis):
    github(apis)
    response = client.post("/integrations/github/connect", data={"token": "ghp_good"})
    assert "The connection works." in response.text
    assert "tony" in response.text
    [connection] = connections(client)
    assert (connection.provider, connection.account) == ("github", "tony")
    assert b"ghp_good" not in connection.credentials
    assert [c["ok"] for c in connection.capabilities] == [True, True]


def test_rejected_token_is_explained_and_not_echoed(client: TestClient, apis: FakeApis):
    github(apis)
    response = client.post("/integrations/github/connect", data={"token": "ghp_wrong"})
    assert response.status_code == 400
    assert "GitHub rejected the credentials" in response.text
    assert "ghp_wrong" not in response.text
    assert connections(client) == []


def test_missing_fields(client: TestClient):
    response = client.post("/integrations/jira/connect", data={"site": "https://x.atlassian.net"})
    assert response.status_code == 400
    assert "Fill in: Atlassian account email, API token." in response.text
    assert 'value="https://x.atlassian.net"' in response.text


def test_test_and_disconnect(client: TestClient, apis: FakeApis):
    github(apis)
    client.post("/integrations/github/connect", data={"token": "ghp_good"})
    connection_id = connections(client)[0].id
    apis.routes.pop(("api.github.com", "/user"))
    response = client.post(f"/integrations/github/{connection_id}/test")
    assert "1 problem" in response.text
    response = client.post(f"/integrations/github/{connection_id}/disconnect")
    assert "The connection was removed." in response.text
    assert connections(client) == []
    assert client.post(f"/integrations/github/{connection_id}/test").status_code == 404


def test_pull_requests_widget_from_github(client: TestClient, apis: FakeApis):
    # Not shown around the orb until a code host is connected.
    assert 'data-open="detail-code_review-pulls"' not in client.get("/welcome/widgets").text
    github(apis)
    client.post("/integrations/github/connect", data={"token": "ghp_good"})
    text = client.get("/welcome/widgets").text
    assert "waiting for your review · 1 ready to merge" in text
    assert "#318 feat: auth token refresh" in text
    # Each pull request says what is expected next; CI is a detail under the title.
    assert ">Review</span>" in text and ">Ready to merge</span>" in text
    assert "✕ CI failing · acme/api" in text and "Yours · ✓ CI · acme/api" in text
    # The numbers filter the list: rows are tagged by what they are.
    for tag in ("todo", "mine", "fix", "ready"):
        assert f'data-filter="{tag}"' in text
    assert 'data-tags="review todo"' in text and 'data-tags="mine ready"' in text


# GitLab


def test_gitlab_merge_requests(client: TestClient, apis: FakeApis):
    host = "gitlab.example.com"
    apis.routes[(host, "/api/v4/user")] = lambda r: (
        httpx2.Response(200, json={"username": "pepper"})
        if r.headers.get("private-token") == "glpat-good"
        else httpx2.Response(401, json={})
    )
    mr = {
        "iid": 12,
        "title": "Fix pipeline",
        "webUrl": f"https://{host}/acme/app/-/merge_requests/12",
        "draft": False,
        "updatedAt": "2026-10-22T08:00:00Z",
        "project": {"fullPath": "acme/app"},
        "headPipeline": {"status": "RUNNING"},
    }
    data = {"data": {"currentUser": {"review": {"nodes": [mr]}, "mine": {"nodes": []}}}}
    apis.routes[(host, "/api/graphql")] = bearer("glpat-good", data)
    response = client.post(
        "/integrations/gitlab/connect", data={"url": f"https://{host}/", "token": "glpat-good"}
    )
    assert "pepper · gitlab.example.com" in response.text
    text = client.get("/welcome/widgets").text
    assert "!12 Fix pipeline" in text
    assert "◌ CI running" in text


# Bitbucket


def test_bitbucket_pull_requests(client: TestClient, apis: FakeApis):
    host = "api.bitbucket.org"
    uuid = "{abc}"
    auth = ("tony@stark.example", "atatt-good")
    apis.routes[(host, "/2.0/user")] = basic(*auth, {"uuid": uuid, "display_name": "Tony"})
    apis.routes[(host, "/2.0/workspaces/acme")] = basic(*auth, {"slug": "acme"})
    pr = {
        "id": 54,
        "title": "fix: redis healthcheck",
        "updated_on": "2026-10-22T08:00:00Z",
        "links": {
            "html": {"href": "https://bitbucket.org/acme/infra/pull-requests/54"},
            "statuses": {
                "href": f"https://{host}/2.0/repositories/acme/infra/pullrequests/54/statuses"
            },
        },
        "destination": {"repository": {"full_name": "acme/infra"}},
    }
    apis.routes[(host, "/2.0/workspaces/acme/pullrequests/{abc}")] = basic(*auth, {"values": []})
    apis.routes[(host, "/2.0/repositories/acme")] = basic(*auth, {"values": [{"slug": "infra"}]})

    merged = {**pr, "id": 50, "title": "chore: merged long ago", "state": "MERGED"}

    def review(request: httpx2.Request) -> httpx2.Response:
        # As Bitbucket does: with `q`, the `state` parameter is ignored and must be in `q`.
        assert request.url.params["q"] == 'state="OPEN" AND reviewers.uuid="{abc}"'
        return httpx2.Response(200, json={"values": [{**pr, "state": "OPEN"}, merged]})

    apis.routes[(host, "/2.0/repositories/acme/infra/pullrequests")] = review
    apis.routes[(host, "/2.0/repositories/acme/infra/pullrequests/54/statuses")] = basic(
        *auth, {"values": [{"state": "SUCCESSFUL"}, {"state": "FAILED"}]}
    )
    data = {"email": auth[0], "token": auth[1], "workspace": "ACME"}
    response = client.post("/integrations/bitbucket/connect", data=data)
    assert "Tony · acme" in response.text
    text = client.get("/welcome/widgets").text
    assert "#54 fix: redis healthcheck" in text
    assert "✕ CI failing" in text
    assert "merged long ago" not in text


# Jira


def jira_site(apis: FakeApis, name: str, issues: list[dict[str, Any]]) -> None:
    host = f"{name}.atlassian.net"
    auth = ("tony@stark.example", "jira-token")
    apis.routes[(host, "/rest/api/3/myself")] = basic(*auth, {"displayName": "Tony"})

    def search(request: httpx2.Request) -> httpx2.Response:
        # Pages as Jira's /search/jql does: maxResults per page, then nextPageToken.
        assert "assignee = currentUser()" in request.url.params["jql"]
        start = int(request.url.params.get("nextPageToken", 0))
        end = start + int(request.url.params["maxResults"])
        page = {"issues": issues[start:end], "isLast": end >= len(issues)}
        if end < len(issues):
            page["nextPageToken"] = str(end)
        return httpx2.Response(200, json=page)

    apis.routes[(host, "/rest/api/3/search/jql")] = search


def issue(key: str, category: str, due: date | None = None, priority: str = "Medium") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "status": {"name": category.title(), "statusCategory": {"key": category}},
            "priority": {"name": priority},
            "duedate": due.isoformat() if due else None,
        },
    }


def connect_jira(client: TestClient, name: str) -> httpx2.Response:
    data = {"site": f"https://{name}.atlassian.net/jira", "email": "tony@stark.example"}
    return client.post("/integrations/jira/connect", data={**data, "token": "jira-token"})


def test_jira_across_workspaces(client: TestClient, apis: FakeApis):
    yesterday = date.today() - timedelta(days=1)
    jira_site(apis, "dev", [issue("TRP-412", "indeterminate"), issue("TRP-409", "new")])
    jira_site(apis, "support", [issue("SUP-88", "new", due=yesterday)])
    assert "dev" in connect_jira(client, "dev").text
    assert "support" in connect_jira(client, "support").text
    assert [c.account for c in connections(client)] == ["dev", "support"]
    text = client.get("/welcome/widgets").text
    assert '<span class="trunc">All workspaces</span><span class="group-count">3</span>' in text
    assert 'data-group-value="dev"' in text and '<span class="group-count">2</span>' in text
    assert 'data-group="support"' in text

    text = client.get("/welcome/widgets").text
    assert "assigned to you · 1 overdue" in text
    workspaces = re.search(r'num" style="font-size: 24px">(\d+)</div><div [^>]*>Workspaces', text)
    assert workspaces and workspaces.group(1) == "2"
    assert text.index("SUP-88") < text.index("TRP-412") < text.index("TRP-409")
    assert "https://support.atlassian.net/browse/SUP-88" in text


def test_jira_lists_every_issue(client: TestClient, apis: FakeApis):
    jira_site(apis, "acme", [issue(f"AC-{n}", "new") for n in range(1, 151)])
    connect_jira(client, "acme")
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">150</span>' in text
    assert "AC-150 Summary of AC-150" in text  # all of them, read over two pages
    assert 'placeholder="Search 150 items…  ( / )"' in text


def test_jira_rejects_non_https_address(client: TestClient):
    data = {"site": "http://dev.atlassian.net", "email": "x@y.z", "token": "t"}
    response = client.post("/integrations/jira/connect", data=data)
    assert response.status_code == 400
    assert "https://mycompany.atlassian.net" in response.text


def test_brief_facts_from_jira_and_pull_requests(client: TestClient, apis: FakeApis):
    github(apis)
    client.post("/integrations/github/connect", data={"token": "ghp_good"})
    jira_site(apis, "dev", [issue("TRP-412", "indeterminate", priority="Highest")])
    connect_jira(client, "dev")
    context = client.app.state.brief._context
    plugins = client.app.state.plugins

    async def facts() -> dict[str, list[str]]:
        sections = [await p.brief(context()) for p in plugins.values()]
        return {s.title: s.lines for s in sections if s}

    result = client.portal.call(facts)
    assert (
        "Review requested: #318 feat: auth token refresh (acme/api) — checks failing"
        in result["Pull requests"]
    )
    assert any("TRP-412" in line and "Highest" in line for line in result["Jira (assigned to you)"])
    assert json.dumps(result)


@pytest.mark.parametrize(
    ("fields", "yours", "label"),
    [
        ({"draft": True}, True, "Draft"),
        ({"ci": "failure", "decision": "approved"}, True, "Fix CI"),
        ({"decision": "changes_requested"}, True, "Address changes"),
        ({"conflicts": True, "decision": "approved"}, True, "Resolve conflicts"),
        ({"decision": "approved", "ci": "success"}, True, "Ready to merge"),
        ({"decision": "approved", "ci": "pending"}, True, "CI running"),
        ({}, True, "Waiting for review"),
        ({}, False, "Review"),
        ({"your_review": "outdated"}, False, "Re-review"),
        ({"your_review": "approved"}, False, "You approved"),
        ({"your_review": "reviewed"}, False, "Waiting on author"),
    ],
)
def test_next_step(fields: dict[str, Any], yours: bool, label: str):
    from myorb.plugins.code_review import next_step
    from myorb.plugins.code_review.providers import PullRequest

    pr = PullRequest("#1", "t", "r", "u", ci=None, draft=False, updated="")
    pr = PullRequest(**{**pr.__dict__, **fields})
    assert next_step(pr, yours).label == label
