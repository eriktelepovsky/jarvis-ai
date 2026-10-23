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


def jira_site(
    apis: FakeApis,
    name: str,
    issues: list[dict[str, Any]],
    in_sprint: set[str] | None = None,
    scrum: set[str] = frozenset(),
) -> None:
    host = f"{name}.atlassian.net"
    auth = ("tony@stark.example", "jira-token")
    apis.routes[(host, "/rest/api/3/myself")] = basic(*auth, {"displayName": "Tony"})

    def search(request: httpx2.Request) -> httpx2.Response:
        # Pages as Jira's /search/jql does: maxResults per page, then nextPageToken.
        jql = request.url.params["jql"]
        assert "assignee = currentUser()" in jql
        found = issues
        if "sprint in openSprints()" in jql:
            if in_sprint is None:  # no Jira Software: the JQL function does not exist
                return httpx2.Response(400, json={"errorMessages": ["Unknown function"]})
            found = [i for i in issues if i["key"] in in_sprint]
        start = int(request.url.params.get("nextPageToken", 0))
        end = start + int(request.url.params["maxResults"])
        page = {"issues": found[start:end], "isLast": end >= len(found)}
        if end < len(issues):
            page["nextPageToken"] = str(end)
        return httpx2.Response(200, json=page)

    apis.routes[(host, "/rest/api/3/search/jql")] = search

    def boards(request: httpx2.Request) -> httpx2.Response:
        project = request.url.params["projectKeyOrId"]
        return httpx2.Response(200, json={"values": [{"id": 1}] if project in scrum else []})

    apis.routes[(host, "/rest/agile/1.0/board")] = boards

    def projects(request: httpx2.Request) -> httpx2.Response:
        keys = sorted({i["key"].split("-")[0] for i in issues})
        values = [{"key": key, "name": f"Project {key}"} for key in keys]
        return httpx2.Response(200, json={"values": values, "isLast": True})

    apis.routes[(host, "/rest/api/3/project/search")] = projects


def issue(key: str, category: str, due: date | None = None, priority: str = "Medium") -> dict:
    return {
        "key": key,
        "fields": {
            "project": {"key": key.split("-")[0], "name": f"Project {key.split('-')[0]}"},
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
    # Within a workspace, its projects narrow further (dev has TRP only, so none are listed).
    assert 'data-subgroup="Project TRP"' in text
    assert "data-subgroup-value" not in text
    assert 'data-group="support"' in text

    text = client.get("/welcome/widgets").text
    assert "assigned to you · 1 overdue" in text
    active = re.search(r'num" style="font-size: 24px">(\d+)</div><div [^>]*>Active', text)
    assert active and active.group(1) == "3"
    assert text.index("SUP-88") < text.index("TRP-412") < text.index("TRP-409")
    assert "https://support.atlassian.net/browse/SUP-88" in text


def test_jira_projects_can_be_left_out(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "new"), issue("OLD-1", "new"), issue("OLD-2", "new")])
    connect_jira(client, "dev")
    assert '<span class="node-value">3</span>' in client.get("/welcome/widgets").text
    assert 'href="/integrations/jira/1/choices">Projects…</a>' in client.get("/integrations").text
    choices = client.get("/integrations/jira/1/choices").text
    assert "Project OLD (OLD)" in choices and choices.count(" checked>") == 2

    form = {"offered": ["OLD", "TRP"], "shown": ["TRP"]}
    client.post("/integrations/jira/1/choices", data=form)
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">1</span>' in text and "OLD-1" not in text


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


def test_jira_projects_within_a_workspace(client: TestClient, apis: FakeApis):
    jira_site(apis, "acme", [issue("WEB-1", "new"), issue("WEB-2", "new"), issue("API-7", "new")])
    connect_jira(client, "acme")
    text = client.get("/welcome/widgets").text
    assert (
        'data-subgroup-value="Project WEB"' in text and 'data-subgroup-value="Project API"' in text
    )
    assert '<span class="trunc">All workspaces</span>' in text  # shown for one workspace too


def test_jira_backlog_is_not_counted(client: TestClient, apis: FakeApis):
    backlog_status = issue("WEB-3", "new")
    backlog_status["fields"]["status"]["name"] = "Backlog"
    jira_site(
        apis,
        "acme",
        [
            issue("APP-1", "indeterminate"),
            issue("APP-2", "new"),
            issue("APP-3", "indeterminate"),  # in progress outside the sprint: still active
            issue("WEB-1", "new"),
            backlog_status,
        ],
        in_sprint={"APP-1"},
        scrum={"APP"},  # APP plans in sprints: APP-2 is not in the active one
    )
    connect_jira(client, "acme")
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">3</span>' in text  # APP-1, APP-3 and WEB-1
    assert "assigned to you · 2 more in the backlog" in text
    assert 'data-filter="backlog"' in text
    assert text.index("WEB-1") < text.index("APP-2")  # the backlog comes last
    assert ">Backlog</span>" in text


def test_a_rate_limit_is_a_note_not_a_problem(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "new")])
    connect_jira(client, "dev")
    apis.routes[("dev.atlassian.net", "/rest/api/3/search/jql")] = lambda r: httpx2.Response(429)
    client.post("/welcome/widgets/jira/assigned/refresh")
    text = client.get("/welcome/widgets").text
    assert "Jira limits how often myOrb may ask" in text and "nothing to fix" in text
    assert "○ NOTE" in text and "Check the connection" not in text
    assert "0 sources need attention" in text  # nothing for the user to do


def test_integrations_say_how_often_a_service_is_asked(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "new")])
    connect_jira(client, "dev")
    client.get("/welcome/widgets")
    text = client.get("/integrations").text
    # Jira: its widget refreshes every 3 minutes; the connecting and the widget asked its site.
    assert 'class="num">3 min <span class="pace-caret"' in text
    requests = re.search(r'<span class="num">(\d+)</span><span class="label">Requests', text)
    assert requests and int(requests.group(1)) >= 2
    assert "× limited" not in text
    apis.routes[("dev.atlassian.net", "/rest/api/3/search/jql")] = lambda r: httpx2.Response(429)
    client.post("/welcome/widgets/jira/assigned/refresh")
    assert "<small>1× limited</small>" in client.get("/integrations").text


def test_every_says_the_pace_in_words():
    from myorb.connections.routes import every

    assert every([180]) == "every 3 min"
    assert every([120, 1800]) == "every 2–30 min"
    assert every([900, 7200]) == "every 15 min to 2 h"
    assert every([3600]) == "every 1 hour"
    assert every([]) == ""


def test_bitbucket_asks_only_active_repositories_and_rereads_only_what_changed():
    import asyncio

    from myorb.plugins.code_review.providers import Bitbucket

    host = "api.bitbucket.org"
    apis = FakeApis()
    pr = {
        "id": 7,
        "title": "feat: export",
        "updated_on": "2026-10-22T08:00:00Z",
        "links": {
            "self": {"href": f"https://{host}/2.0/repositories/acme/app/pullrequests/7"},
            "statuses": {
                "href": f"https://{host}/2.0/repositories/acme/app/pullrequests/7/statuses"
            },
        },
    }
    ci = {"state": "INPROGRESS"}
    queries: list[str] = []

    def repositories(request: httpx2.Request) -> httpx2.Response:
        queries.append(request.url.params["q"])
        return httpx2.Response(200, json={"values": [{"slug": "app"}]})

    apis.routes[(host, "/2.0/repositories/acme")] = repositories
    apis.routes[(host, "/2.0/workspaces/acme/pullrequests/{abc}")] = lambda r: httpx2.Response(
        200, json={"values": []}
    )
    apis.routes[(host, "/2.0/repositories/acme/app/pullrequests")] = lambda r: httpx2.Response(
        200, json={"values": [dict(pr)]}
    )
    apis.routes[(host, "/2.0/repositories/acme/app/pullrequests/7")] = lambda r: httpx2.Response(
        200, json={**pr, "participants": []}
    )
    apis.routes[(host, "/2.0/repositories/acme/app/pullrequests/7/statuses")] = lambda r: (
        httpx2.Response(200, json={"values": [dict(ci)]})
    )
    clock = [1000.0]
    bitbucket = Bitbucket(clock=lambda: clock[0])
    credentials = {"email": "t@x", "token": "t", "workspace": "acme", "uuid": "{abc}"}

    def read() -> list[str]:
        async def go():
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(apis)) as http:
                return await bitbucket.pull_requests(http, credentials)

        apis.calls.clear()
        result = asyncio.run(go())
        assert result["review"][0].ref == "#7"
        return [call.removeprefix(f"GET {host}/2.0/") for call in apis.calls]

    first = read()
    assert queries[0].startswith("updated_on >= ")  # only repositories active lately
    assert "repositories/acme/app/pullrequests/7" in first  # details read
    # Unchanged, but CI was running: only the CI is read again.
    second = read()
    assert "repositories/acme/app/pullrequests/7" not in second
    assert "repositories/acme/app/pullrequests/7/statuses" in second
    ci["state"] = "SUCCESSFUL"
    read()
    # Unchanged and CI finished: neither details nor CI.
    third = read()
    assert not [c for c in third if c.startswith("repositories/acme/app/pullrequests/7")]
    # Changed (or read too long ago): both again.
    pr["updated_on"] = "2026-10-22T09:00:00Z"
    assert "repositories/acme/app/pullrequests/7" in read()
    clock[0] += 31 * 60
    assert "repositories/acme/app/pullrequests/7" in read()


def test_a_service_pace_is_chosen(client: TestClient, apis: FakeApis):
    jira_site(apis, "dev", [issue("TRP-1", "new")])
    connect_jira(client, "dev")
    assert 'data-refresh="180"' in client.get("/welcome/widget/jira/assigned").text
    response = client.post("/integrations/jira/pace", data={"seconds": "600"})
    assert "The numbers on the welcome screen follow the new pace." in response.text
    assert 'class="num">10 min <span class="pace-caret"' in response.text
    assert 'value="600" class="menu-item is-chosen"' in response.text
    # The number's border and the cache follow it.
    assert 'data-refresh="600"' in client.get("/welcome/widget/jira/assigned").text
    assert client.app.state.cache._pace("jira:1:issues") == 600
    # Bitbucket's limit allows no less than 5 minutes; other values are refused.
    assert client.post("/integrations/bitbucket/pace", data={"seconds": "60"}).status_code == 400
    assert client.post("/integrations/jira/pace", data={"seconds": "7"}).status_code == 400
    assert client.post("/integrations/custom/pace", data={"seconds": "600"}).status_code == 404
    # Back to the default.
    client.post("/integrations/jira/pace", data={"seconds": "default"})
    assert 'data-refresh="180"' in client.get("/welcome/widget/jira/assigned").text
