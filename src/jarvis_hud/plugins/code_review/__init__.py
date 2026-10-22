from pathlib import Path

from jarvis_hud.connections.providers import ConnectedAccount
from jarvis_hud.plugins.base import (
    BriefSection,
    Link,
    Plugin,
    Row,
    Stat,
    Widget,
    WidgetContext,
    WidgetData,
)
from jarvis_hud.plugins.code_review.providers import (
    Bitbucket,
    CodeHost,
    GitHub,
    GitLab,
    PullRequest,
)
from jarvis_hud.plugins.connection_data import AccountResult, connected_status, load_connected

ICON = (
    "M4 5a2 2 0 1 0 4 0a2 2 0 1 0-4 0M4 19a2 2 0 1 0 4 0a2 2 0 1 0-4 0M16 19a2 2 0 1 0 4 0"
    "a2 2 0 1 0-4 0M6 7v10M18 17V9a3 3 0 0 0-3-3h-4M13 4l-2 2 2 2"
)
CI_BADGES = {
    "success": ("✓ Passed", "ok"),
    "failure": ("✕ Failed", "fail"),
    "pending": ("◌ Running", "info"),
}
ROWS_SHOWN = 12


def ci_badge(pr: PullRequest) -> tuple[str, str]:
    if pr.draft:
        return "Draft", "neutral"
    return CI_BADGES.get(pr.ci or "", ("", "neutral"))


class CodeReviewPlugin(Plugin):
    """Pull and merge requests from GitHub, GitLab and Bitbucket."""

    id = "code_review"
    name = "Pull requests"
    templates_dir = Path(__file__).parent / "templates"
    position = 40

    def __init__(self) -> None:
        self.hosts: list[CodeHost] = [GitHub(), GitLab(), Bitbucket()]

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
            )
        ]

    async def _load(self, context: WidgetContext) -> list[AccountResult]:
        async def fetch(host: CodeHost, account: ConnectedAccount) -> dict[str, list[PullRequest]]:
            return await host.pull_requests(context.http, account.credentials)

        return await load_connected(
            context, self.hosts, "pulls", fetch, fresh=3 * 60, max_age=60 * 60
        )

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
        failing = [pr for pr in [*review, *mine] if pr.ci == "failure"]
        rows = []
        for pr, yours in [*((pr, False) for pr in review), *((pr, True) for pr in mine)]:
            badge, tone = ci_badge(pr)
            rows.append(
                Row(
                    title=f"{pr.ref} {pr.title}",
                    subtitle=("Yours · " if yours else "") + pr.repo,
                    badge=badge,
                    tone=tone,
                    url=pr.url,
                )
            )
        return WidgetData(
            value=len(review),
            summary="waiting for your review" + (f" · {len(failing)} failing" if failing else ""),
            attention=bool(failing),
            stats=[
                Stat(len(review), "To review"),
                Stat(len(failing), "CI failing"),
                Stat(len(mine), "Yours open"),
            ],
            list_title="Waiting for your review, then yours",
            rows=rows[:ROWS_SHOWN],
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        results = await self._load(context)
        if not any(r.data is not None for r in results):
            return None
        review, mine = self._merged(results)
        failing = " — checks failing"
        lines = [
            f"Waiting for your review: {pr.ref} {pr.title} ({pr.repo})"
            + (failing if pr.ci == "failure" else "")
            for pr in review
        ]
        lines += [
            f"Your pull request with failing checks: {pr.ref} {pr.title} ({pr.repo})"
            for pr in mine
            if pr.ci == "failure"
        ]
        return BriefSection("Pull requests", lines or ["No pull requests are waiting for you."])
