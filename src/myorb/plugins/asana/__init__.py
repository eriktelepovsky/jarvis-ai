"""Asana: tasks assigned to the user across their workspaces, overdue and due soon first."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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
)
from myorb.plugins.connection_data import AccountResult, connected_status, load_connected

API = "https://app.asana.com/api/1.0"
ICON = (
    "M12 7a3 3 0 1 0 0-6 3 3 0 0 0 0 6M5 17a3 3 0 1 0 0-6 3 3 0 0 0 0 6"
    "M19 17a3 3 0 1 0 0-6 3 3 0 0 0 0 6"
)
TASK_FIELDS = "name,due_on,due_at,permalink_url,projects.name"
# Tasks read per workspace, in pages of PAGE.
MAX_TASKS = 300
PAGE = 100
SOON = timedelta(days=7)


@dataclass(frozen=True)
class Task:
    name: str
    due: date | None
    due_at: datetime | None  # when the task has a time, not just a day
    url: str
    workspace: str
    project: str


class Asana(Provider):
    id = "asana"
    name = "Asana"
    logo = "A"
    summary = "Tasks assigned to you, from all your workspaces."
    instructions = "asana.html"
    fields = [Field("token", "Personal access token", "password")]

    @staticmethod
    def _headers(credentials: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {credentials['token']}"}

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        me = await request_json(
            http,
            "GET",
            f"{API}/users/me",
            "Asana",
            headers=self._headers(values),
            params={"opt_fields": "name,email"},
        )
        user = me.get("data", {})
        credentials = {"token": values["token"]}
        capability = {"id": "tasks", "label": "Read your tasks", "ok": True}
        try:
            await self.tasks(http, credentials)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        return Verified(user.get("email") or user.get("name", "Asana"), credentials, [capability])

    async def tasks(self, http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[Task]:
        headers = self._headers(credentials)
        me = await request_json(
            http,
            "GET",
            f"{API}/users/me",
            "Asana",
            headers=headers,
            params={"opt_fields": "workspaces.name"},
        )
        workspaces = me.get("data", {}).get("workspaces", [])

        async def in_workspace(workspace: dict[str, Any]) -> list[Task]:
            items: list[dict[str, Any]] = []
            offset = None
            while len(items) < MAX_TASKS:
                # completed_since=now: only tasks that are not completed.
                params = {
                    "assignee": "me",
                    "workspace": workspace["gid"],
                    "completed_since": "now",
                    "opt_fields": TASK_FIELDS,
                    "limit": PAGE,
                }
                if offset:
                    params["offset"] = offset
                page = await request_json(
                    http, "GET", f"{API}/tasks", "Asana", headers=headers, params=params
                )
                items += page.get("data", [])
                offset = (page.get("next_page") or {}).get("offset")
                if not offset:
                    break
            return [convert(item, workspace.get("name", "")) for item in items[:MAX_TASKS]]

        results = await asyncio.gather(*(in_workspace(w) for w in workspaces))
        return [task for tasks in results for task in tasks]


def convert(item: dict[str, Any], workspace: str) -> Task:
    due_at = datetime.fromisoformat(item["due_at"]) if item.get("due_at") else None
    due_on = date.fromisoformat(item["due_on"]) if item.get("due_on") else None
    projects = item.get("projects") or []
    return Task(
        name=item.get("name") or "(untitled)",
        due=due_on or (due_at.date() if due_at else None),
        due_at=due_at,
        url=item.get("permalink_url") or "https://app.asana.com/",
        workspace=workspace,
        project=projects[0].get("name", "") if projects else "No project",
    )


def is_overdue(task: Task, now: datetime) -> bool:
    if task.due_at is not None:
        return task.due_at < now
    return task.due is not None and task.due < now.date()


def due_badge(task: Task, now: datetime) -> str:
    if task.due is None:
        return ""
    local = task.due_at.astimezone(now.tzinfo) if task.due_at else None
    if is_overdue(task, now):
        return f"▲ Due {task.due:%-d %b}"
    if task.due == now.date():
        return f"Today {local:%H:%M}" if local else "Today"
    if task.due - now.date() < SOON:
        return f"{task.due:%a}" + (f" {local:%H:%M}" if local else "")
    return f"{task.due:%-d %b}"


class AsanaPlugin(Plugin):
    """Asana tasks assigned to the user."""

    id = "asana"
    name = "Asana"
    templates_dir = Path(__file__).parent / "templates"
    position = 36

    def __init__(self) -> None:
        self.provider = Asana()

    def connections(self) -> list[Provider]:
        return [self.provider]

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="tasks",
                label="Asana",
                title="Asana · assigned to you",
                icon=ICON,
                load=self._widget,
            )
        ]

    async def _load(self, context: WidgetContext) -> list[AccountResult]:
        async def fetch(provider: Provider, account: ConnectedAccount) -> list[Task]:
            return await self.provider.tasks(context.http, account.credentials)

        return await load_connected(
            context, [self.provider], "tasks", fetch, fresh=3 * 60, max_age=60 * 60
        )

    async def _widget(self, context: WidgetContext) -> WidgetData:
        results = await self._load(context)
        status = connected_status(
            results, "Asana", Link("Connect Asana", "/integrations/asana/connect")
        )
        loaded = [r for r in results if r.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        now = context.now
        today = now.date()
        tasks = [task for r in loaded for task in r.data]
        overdue = [t for t in tasks if is_overdue(t, now)]
        due_today = [t for t in tasks if t.due == today and not is_overdue(t, now)]
        soon = [t for t in tasks if t.due and today < t.due < today + SOON]
        # Overdue first, then by due date; tasks without one last.
        ordered = sorted(tasks, key=lambda t: (t.due is None, t.due or date.max, t.name.lower()))
        rows = []
        for task in ordered:
            late = is_overdue(task, now)
            tags = tuple(
                tag
                for tag, applies in (
                    ("overdue", late),
                    ("today", task in due_today),
                    ("soon", task in soon),
                )
                if applies
            )
            rows.append(
                Row(
                    task.name,
                    task.project,
                    due_badge(task, now),
                    "fail" if late else "info" if task.due == today else "neutral",
                    task.url,
                    tags=tags,
                    group=task.workspace,
                    subgroup=task.project,
                )
            )
        return WidgetData(
            value=len(tasks),
            summary=" · ".join(
                p
                for p in (
                    "assigned to you",
                    f"{len(overdue)} overdue" if overdue else "",
                    f"{len(due_today)} due today" if due_today else "",
                )
                if p
            ),
            attention=bool(overdue),
            stats=[
                Stat(len(overdue), "Overdue", filter="overdue"),
                Stat(len(due_today), "Due today", filter="today"),
                Stat(len(soon), "Next 7 days", filter="soon"),
            ],
            list_title="Assigned to you",
            group_label="workspaces",
            rows=rows,
            action=Link("Open Asana", "https://app.asana.com/0/my-tasks"),
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [r for r in await self._load(context) if r.data is not None]
        if not loaded:
            return None
        now = context.now
        tasks = [t for r in loaded for t in r.data]
        lines = [f"{len(tasks)} open Asana tasks assigned to you."]
        for task in sorted(tasks, key=lambda t: t.due or date.max):
            if task.due is None or task.due > now.date() + timedelta(days=2):
                continue
            when = "overdue since" if is_overdue(task, now) else "due"
            lines.append(f"{task.name} ({task.project}, {when} {due_badge(task, now)})")
        return BriefSection("Asana (assigned to you)", lines)
