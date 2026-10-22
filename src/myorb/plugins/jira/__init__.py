import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2

from myorb.connections.providers import (
    Choice,
    ConnectedAccount,
    Field,
    Provider,
    ProviderError,
    Verified,
    hidden,
    request_json,
)
from myorb.plugins.base import (
    BriefSection,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
    open_buttons,
)
from myorb.plugins.connection_data import AccountResult, connected_status, load_connected

ICON = "M4 4h16v16H4zM9 4v16M15 4v16M11 8h2M5.5 8h2"
MINE = "assignee = currentUser() AND statusCategory != Done"
ORDER = " ORDER BY priority DESC, updated DESC"
FIELDS = "summary,status,priority,duedate,updated,project"
# Issues read per workspace (in pages of PAGE); the details list all of them.
MAX_ISSUES = 200
PAGE = 100
URGENT_PRIORITIES = {"Highest", "Blocker", "Critical"}


@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    status: str
    # Jira's status category: "new" (to do), "indeterminate" (in progress) or "done".
    category: str
    priority: str
    due: date | None
    url: str
    workspace: str
    # The project's name, e.g. "Transportly"; its key when Jira does not say.
    project: str = ""
    # In the backlog: still to do, and in status "Backlog" or, in a project with sprints, not
    # in an active one.
    backlog: bool = False


def site_url(value: str) -> str:
    parts = urlsplit(value.strip().rstrip("/"))
    if parts.scheme != "https" or not parts.netloc:
        raise ProviderError("Enter the Jira address, e.g. https://mycompany.atlassian.net.")
    return f"https://{parts.netloc}"


def workspace_name(site: str) -> str:
    """mycompany.atlassian.net -> mycompany"""
    return urlsplit(site).netloc.removesuffix(".atlassian.net")


class Jira(Provider):
    id = "jira"
    name = "Jira"
    logo = "J"
    summary = "Tasks assigned to you. Connect each workspace (site) separately."
    instructions = "jira.html"
    fields = [
        Field("site", "Jira address", "url", "https://mycompany.atlassian.net"),
        Field("email", "Atlassian account email", "email"),
        Field("token", "API token", "password"),
    ]
    choices_label = "Projects"

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        credentials = {**values, "site": site_url(values["site"])}
        await request_json(
            http,
            "GET",
            f"{credentials['site']}/rest/api/3/myself",
            "Jira",
            auth=httpx2.BasicAuth(values["email"], values["token"]),
        )
        capability = {"id": "issues", "label": "Read your issues", "ok": True}
        try:
            await self.issues(http, credentials)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        return Verified(workspace_name(credentials["site"]), credentials, [capability])

    async def choices(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[Choice]:
        """The site's projects the user can see, page by page."""
        projects: list[dict[str, Any]] = []
        while True:
            page = await request_json(
                http,
                "GET",
                f"{credentials['site']}/rest/api/3/project/search",
                "Jira",
                auth=httpx2.BasicAuth(credentials["email"], credentials["token"]),
                params={"startAt": len(projects), "maxResults": 50, "orderBy": "name"},
            )
            projects += page.get("values", [])
            if page.get("isLast", True) or not page.get("values"):
                break
        return [Choice(p["key"], f"{p.get('name') or p['key']} ({p['key']})") for p in projects]

    async def _search(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any], jql: str, fields: str
    ) -> list[dict[str, Any]]:
        """Issues for a JQL query, page by page, up to MAX_ISSUES."""
        items: list[dict[str, Any]] = []
        token = None
        while len(items) < MAX_ISSUES:
            params = {"jql": jql, "fields": fields, "maxResults": PAGE}
            if token:
                params["nextPageToken"] = token
            data = await request_json(
                http,
                "GET",
                f"{credentials['site']}/rest/api/3/search/jql",
                "Jira",
                auth=httpx2.BasicAuth(credentials["email"], credentials["token"]),
                params=params,
            )
            items += data.get("issues", [])
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                break
        return items[:MAX_ISSUES]

    async def _sprints(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any], projects: set[str]
    ) -> tuple[set[str], set[str]]:
        """The user's issues in an active sprint, and the projects that plan in sprints.

        In a project with a scrum board, what is not in an active sprint is in its backlog.
        Without Jira Software (no sprints), both are empty.
        """
        auth = httpx2.BasicAuth(credentials["email"], credentials["token"])
        try:
            jql = f"{mine(credentials)} AND sprint in openSprints()"
            in_sprint = await self._search(http, credentials, jql, "status")
        except ProviderError:
            return set(), set()

        async def scrum(project: str) -> str | None:
            try:
                boards = await request_json(
                    http,
                    "GET",
                    f"{credentials['site']}/rest/agile/1.0/board",
                    "Jira",
                    auth=auth,
                    params={"projectKeyOrId": project, "type": "scrum", "maxResults": 1},
                )
            except ProviderError:
                return None
            return project if boards.get("values") else None

        found = await asyncio.gather(*(scrum(p) for p in sorted(projects)))
        return {item["key"] for item in in_sprint}, {p for p in found if p}

    async def issues(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[Issue]:
        site = credentials["site"]
        left_out = hidden(credentials)
        items = [
            item
            for item in await self._search(http, credentials, mine(credentials) + ORDER, FIELDS)
            if (item.get("fields", {}).get("project") or {}).get("key") not in left_out
        ]
        projects = {(i.get("fields", {}).get("project") or {}).get("key") for i in items} - {None}
        in_sprint, scrum_projects = await self._sprints(http, credentials, projects)
        issues = []
        for item in items:
            fields = item.get("fields", {})
            status = fields.get("status") or {}
            due = fields.get("duedate")
            project = fields.get("project") or {}
            category = (status.get("statusCategory") or {}).get("key", "new")
            # Only what is still to do: work in progress is never the backlog, sprint or not.
            backlog = category == "new" and (
                status.get("name", "").lower() == "backlog"
                or (project.get("key") in scrum_projects and item["key"] not in in_sprint)
            )
            issues.append(
                Issue(
                    key=item["key"],
                    summary=fields.get("summary") or "(no summary)",
                    status=status.get("name", ""),
                    category=category,
                    priority=(fields.get("priority") or {}).get("name", ""),
                    due=date.fromisoformat(due) if due else None,
                    url=f"{site}/browse/{item['key']}",
                    workspace=workspace_name(site),
                    project=project.get("name") or item["key"].split("-")[0],
                    backlog=backlog,
                )
            )
        return issues


def mine(credentials: dict[str, Any]) -> str:
    """JQL for the user's open issues, without the projects they left out."""
    left_out = sorted(hidden(credentials))
    if not left_out:
        return MINE
    keys = ", ".join('"' + key.replace('"', "") + '"' for key in left_out)
    return f"{MINE} AND project not in ({keys})"


def is_overdue(issue: Issue, today: date) -> bool:
    return issue.due is not None and issue.due < today


class JiraPlugin(Plugin):
    """Jira issues assigned to the user, across several workspaces."""

    id = "jira"
    name = "Jira"
    templates_dir = Path(__file__).parent / "templates"
    position = 35

    def __init__(self) -> None:
        self.provider = Jira()

    def connections(self) -> list[Provider]:
        return [self.provider]

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="assigned",
                label="Jira",
                title="Jira · assigned to you",
                icon=ICON,
                load=self._widget,
            )
        ]

    async def _load(self, context: WidgetContext) -> list[AccountResult]:
        async def fetch(provider: Provider, account: ConnectedAccount) -> list[Issue]:
            return await self.provider.issues(context.http, account.credentials)

        return await load_connected(
            context, [self.provider], "issues", fetch, fresh=3 * 60, max_age=60 * 60
        )

    async def _widget(self, context: WidgetContext) -> WidgetData:
        results = await self._load(context)
        status = connected_status(
            results, "Jira", Link("Connect Jira", "/integrations/jira/connect")
        )
        loaded = [r for r in results if r.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        today = context.now.date()
        issues = [issue for r in loaded for issue in r.data]
        # The orb counts what is being worked on; the backlog is listed, but last and apart.
        active = [i for i in issues if not i.backlog]
        backlog = [i for i in issues if i.backlog]
        overdue = [i for i in active if is_overdue(i, today)]
        urgent = [i for i in active if i.priority in URGENT_PRIORITIES]
        in_progress = [i for i in active if i.category == "indeterminate"]
        # Overdue first, then in progress, then the rest in Jira's priority order; backlog last.
        ordered = sorted(
            issues,
            key=lambda i: (i.backlog, not is_overdue(i, today), i.category != "indeterminate"),
        )
        rows = []
        for issue in ordered:
            if issue.backlog:
                badge, tone = "Backlog", "neutral"
            elif is_overdue(issue, today):
                badge, tone = f"▲ Due {issue.due:%-d %b}", "fail"
            else:
                badge = issue.status
                tone = "info" if issue.category == "indeterminate" else "neutral"
            subtitle = " · ".join(
                p for p in (issue.workspace if len(loaded) > 1 else "", issue.priority) if p
            )
            tags = tuple(
                tag
                for tag, applies in (
                    ("overdue", is_overdue(issue, today)),
                    ("progress", issue.category == "indeterminate"),
                    ("active", not issue.backlog),
                    ("backlog", issue.backlog),
                )
                if applies
            )
            rows.append(
                Row(
                    f"{issue.key} {issue.summary}",
                    subtitle,
                    badge,
                    tone,
                    issue.url,
                    tags=tags,
                    group=issue.workspace,
                    subgroup=issue.project,
                )
            )
        return WidgetData(
            value=len(active),
            summary=" · ".join(
                p
                for p in (
                    "assigned to you",
                    f"{len(overdue)} overdue" if overdue else "",
                    f"{len(backlog)} more in the backlog" if backlog else "",
                )
                if p
            ),
            attention=bool(overdue or urgent),
            stats=[
                Stat(len(active), "Active", filter="active"),
                Stat(len(in_progress), "In progress", filter="progress"),
                Stat(len(backlog), "Backlog", filter="backlog"),
            ],
            list_title="Assigned to you",
            group_label="workspaces",
            rows=rows,
            **open_buttons(
                "Open Jira",
                [
                    (r.data[0].workspace, f"{r.data[0].url.split('/browse/')[0]}/jira/your-work")
                    for r in loaded
                    if r.data
                ],
            ),
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [r for r in await self._load(context) if r.data is not None]
        if not loaded:
            return None
        today = context.now.date()
        issues = [i for r in loaded for i in r.data]
        backlog = sum(1 for i in issues if i.backlog)
        lines = [f"{backlog} more issues wait in the backlog."] if backlog else []
        for issue in [i for i in issues if not i.backlog][:20]:
            notes = [issue.status, issue.priority, issue.workspace]
            if is_overdue(issue, today):
                notes.insert(0, f"overdue since {issue.due:%-d %b}")
            elif issue.due:
                notes.append(f"due {issue.due:%-d %b}")
            lines.append(f"{issue.key} {issue.summary} ({', '.join(n for n in notes if n)})")
        return BriefSection("Jira (assigned to you)", lines or ["No open Jira issues."])
