from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2

from myorb.connections.providers import (
    ConnectedAccount,
    Field,
    Provider,
    ProviderError,
    Verified,
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
JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC, updated DESC"
FIELDS = "summary,status,priority,duedate,updated"
MAX_ISSUES = 50
ROWS_SHOWN = 12
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

    async def issues(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[Issue]:
        site = credentials["site"]
        data = await request_json(
            http,
            "GET",
            f"{site}/rest/api/3/search/jql",
            "Jira",
            auth=httpx2.BasicAuth(credentials["email"], credentials["token"]),
            params={"jql": JQL, "fields": FIELDS, "maxResults": MAX_ISSUES},
        )
        issues = []
        for item in data.get("issues", []):
            fields = item.get("fields", {})
            status = fields.get("status") or {}
            due = fields.get("duedate")
            issues.append(
                Issue(
                    key=item["key"],
                    summary=fields.get("summary") or "(no summary)",
                    status=status.get("name", ""),
                    category=(status.get("statusCategory") or {}).get("key", "new"),
                    priority=(fields.get("priority") or {}).get("name", ""),
                    due=date.fromisoformat(due) if due else None,
                    url=f"{site}/browse/{item['key']}",
                    workspace=workspace_name(site),
                )
            )
        return issues


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
        overdue = [i for i in issues if is_overdue(i, today)]
        urgent = [i for i in issues if i.priority in URGENT_PRIORITIES]
        in_progress = [i for i in issues if i.category == "indeterminate"]
        # Overdue first, then in progress, then the rest in Jira's priority order.
        ordered = sorted(
            issues, key=lambda i: (not is_overdue(i, today), i.category != "indeterminate")
        )
        rows = []
        for issue in ordered[:ROWS_SHOWN]:
            if is_overdue(issue, today):
                badge, tone = f"▲ Due {issue.due:%-d %b}", "fail"
            else:
                badge = issue.status
                tone = "info" if issue.category == "indeterminate" else "neutral"
            subtitle = " · ".join(
                p for p in (issue.workspace if len(loaded) > 1 else "", issue.priority) if p
            )
            rows.append(Row(f"{issue.key} {issue.summary}", subtitle, badge, tone, issue.url))
        return WidgetData(
            value=len(issues),
            summary="assigned to you" + (f" · {len(overdue)} overdue" if overdue else ""),
            attention=bool(overdue or urgent),
            stats=[
                Stat(len(issues), "Assigned"),
                Stat(len(in_progress), "In progress"),
                Stat(len(loaded), "Workspaces"),
            ],
            list_title="Assigned to you",
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
        lines = []
        for issue in [i for r in loaded for i in r.data][:20]:
            notes = [issue.status, issue.priority, issue.workspace]
            if is_overdue(issue, today):
                notes.insert(0, f"overdue since {issue.due:%-d %b}")
            elif issue.due:
                notes.append(f"due {issue.due:%-d %b}")
            lines.append(f"{issue.key} {issue.summary} ({', '.join(n for n in notes if n)})")
        return BriefSection("Jira (assigned to you)", lines or ["No open Jira issues."])
