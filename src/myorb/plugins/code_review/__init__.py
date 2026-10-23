import asyncio
from dataclasses import dataclass
from pathlib import Path

from myorb.connections.providers import ConnectedAccount
from myorb.plugins.base import (
    AskAction,
    BriefSection,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from myorb.plugins.code_review.providers import (
    Bitbucket,
    CodeHost,
    GitHub,
    GitLab,
    PullRequest,
)
from myorb.plugins.connection_data import AccountResult, connected_status, load_connected

ICON = (
    "M4 5a2 2 0 1 0 4 0a2 2 0 1 0-4 0M4 19a2 2 0 1 0 4 0a2 2 0 1 0-4 0M16 19a2 2 0 1 0 4 0"
    "a2 2 0 1 0-4 0M6 7v10M18 17V9a3 3 0 0 0-3-3h-4M13 4l-2 2 2 2"
)
CI_TEXT = {"success": "✓ CI", "failure": "✕ CI failing", "pending": "◌ CI running"}


@dataclass(frozen=True)
class Step:
    """What is expected next for a pull request, shown as its badge; rank sorts by urgency."""

    label: str
    tone: str
    tag: str
    rank: int


def next_step(pr: PullRequest, yours: bool) -> Step:
    if pr.draft:
        return Step("Draft", "neutral", "draft", 9)
    if yours:
        if pr.ci == "failure":
            return Step("Fix CI", "fail", "fix", 0)
        if pr.decision == "changes_requested":
            return Step("Address changes", "warn", "fix", 1)
        if pr.conflicts:
            return Step("Resolve conflicts", "warn", "fix", 2)
        if pr.decision == "approved" and pr.ci != "pending":
            return Step("Ready to merge", "ok", "ready", 5)
        if pr.ci == "pending":
            return Step("CI running", "info", "waiting", 6)
        return Step("Waiting for review", "neutral", "waiting", 7)
    if pr.your_review == "outdated":
        return Step("Re-review", "warn", "todo", 3)
    if pr.your_review == "approved":
        return Step("You approved", "neutral", "done", 8)
    if pr.your_review == "reviewed":
        return Step("Waiting on author", "neutral", "done", 8)
    return Step("Review", "info", "todo", 4)


class CodeReviewPlugin(Plugin):
    """Pull and merge requests from GitHub, GitLab and Bitbucket."""

    id = "code_review"
    name = "Pull requests"
    templates_dir = Path(__file__).parent / "templates"
    position = 40

    def __init__(self) -> None:
        self.hosts: list[CodeHost] = [GitHub(), GitLab(), Bitbucket()]

    def ask_actions(self) -> list[AskAction]:
        from myorb.plugins.code_review.actions import ask_actions

        github = next(host for host in self.hosts if isinstance(host, GitHub))
        return ask_actions(github)

    def connections(self) -> list[CodeHost]:
        return self.hosts

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="pulls",
                label="PRs",
                title="Pull requests",
                icon=ICON,
                load=self._widget,
                weight=1.3,
                refresh=3 * 60,
            )
        ]

    async def _load(self, context: WidgetContext) -> list[AccountResult]:
        async def fetch(host: CodeHost, account: ConnectedAccount) -> dict[str, list[PullRequest]]:
            return await host.pull_requests(context.http, account.credentials)

        # A host with a pace of its own (Bitbucket: asked repository by repository and limited
        # per hour) is read at that pace; the others as often as the widget refreshes.
        results = await asyncio.gather(
            *(
                load_connected(
                    context,
                    [host],
                    "pulls",
                    fetch,
                    fresh=host.refresh or 3 * 60,
                    max_age=max(60 * 60, 4 * (host.refresh or 0)),
                )
                for host in self.hosts
            )
        )
        return [result for part in results for result in part]

    @staticmethod
    def _merged(results: list[AccountResult]) -> tuple[list[PullRequest], list[PullRequest]]:
        loaded = [r for r in results if r.data is not None]
        review = sorted(
            (pr for r in loaded for pr in r.data["review"]), key=lambda p: p.updated, reverse=True
        )
        mine = sorted(
            (pr for r in loaded for pr in r.data["mine"]), key=lambda p: p.updated, reverse=True
        )
        return review, mine

    async def _widget(self, context: WidgetContext) -> WidgetData:
        results = await self._load(context)
        sources = (
            " · ".join(dict.fromkeys(r.provider.name for r in results))
            or "GitHub · GitLab · Bitbucket"
        )
        status = connected_status(results, sources, Link("Connect a code host", "/integrations"))
        if not any(r.data is not None for r in results):
            return WidgetData(value=None, **status)
        review, mine = self._merged(results)
        entries = [(pr, False, next_step(pr, False)) for pr in review]
        entries += [(pr, True, next_step(pr, True)) for pr in mine]
        # Waiting for your review first, then yours; within each, what is most urgent first.
        entries.sort(key=lambda e: (e[1], e[2].rank))
        rows = [
            Row(
                title=f"{pr.ref} {pr.title}",
                subtitle=" · ".join(
                    p
                    for p in ("Yours" if yours else "", CI_TEXT.get(pr.ci or "", ""), pr.repo)
                    if p
                ),
                badge=step.label,
                tone=step.tone,
                url=pr.url,
                tags=("mine" if yours else "review", step.tag),
                # GitHub's pull requests can be approved and commented on from Ask.
                ref=pr.url if pr.url.startswith("https://github.com/") else "",
                group=pr.repo,
            )
            for pr, yours, step in entries
        ]
        to_review = [e for e in entries if e[2].tag == "todo"]
        to_fix = [e for e in entries if e[2].tag == "fix"]
        ready = [e for e in entries if e[2].tag == "ready"]
        failing = [pr for pr in [*review, *mine] if pr.ci == "failure"]
        parts = [
            f"{len(to_fix)} to fix" if to_fix else "",
            f"{len(ready)} ready to merge" if ready else "",
        ]
        return WidgetData(
            value=len(to_review),
            summary=" · ".join(["waiting for your review", *(p for p in parts if p)]),
            attention=bool(to_fix or failing),
            stats=[
                Stat(len(to_review), "To review", filter="todo"),
                Stat(len(mine), "Yours open", filter="mine"),
                Stat(len(to_fix), "Needs fixing", filter="fix"),
                Stat(len(ready), "Ready to merge", filter="ready"),
            ],
            list_title="Waiting for your review, then yours",
            group_label="repositories",
            rows=rows,
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        results = await self._load(context)
        if not any(r.data is not None for r in results):
            return None
        review, mine = self._merged(results)
        lines = []
        for pr in review:
            step = next_step(pr, False)
            if step.tag == "todo":
                failing = " — checks failing" if pr.ci == "failure" else ""
                lines.append(f"{step.label} requested: {pr.ref} {pr.title} ({pr.repo}){failing}")
        for pr in mine:
            step = next_step(pr, True)
            if step.tag in ("fix", "ready"):
                lines.append(f"Your pull request — {step.label}: {pr.ref} {pr.title} ({pr.repo})")
        return BriefSection("Pull requests", lines or ["No pull requests are waiting for you."])
