"""GitHub, GitLab and Bitbucket: pull requests waiting for the user's review, and their own."""

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2

from myorb.connections.providers import (
    Field,
    Provider,
    ProviderError,
    Verified,
    request_json,
)

ITEMS = 20


@dataclass(frozen=True)
class PullRequest:
    ref: str  # "#318" or "!12"
    title: str
    repo: str
    url: str
    # "success", "failure", "pending" or None when the repository runs no checks.
    ci: str | None
    draft: bool
    updated: str
    # The reviews so far: "approved", "changes_requested" or None (no decision yet).
    decision: str | None = None
    # Whether it conflicts with its target branch; None when the host does not say.
    conflicts: bool | None = None
    # The user's own review of someone else's pull request: None (not reviewed), "approved",
    # "reviewed" (commented or asked for changes) or "outdated" (new commits since).
    your_review: str | None = None


class CodeHost(Provider):
    async def pull_requests(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any]
    ) -> dict[str, list[PullRequest]]:
        """{"review": waiting for the user's review, "mine": opened by the user}"""
        raise NotImplementedError


# GitHub

GITHUB_API = "https://api.github.com"
GITHUB_STATES = {"SUCCESS": "success", "FAILURE": "failure", "ERROR": "failure"}
GITHUB_QUERY = """
query($n: Int!) {
  review: search(
    query: "is:open is:pr review-requested:@me archived:false", type: ISSUE, first: $n
  ) { nodes { ...pr } }
  mine: search(query: "is:open is:pr author:@me archived:false", type: ISSUE, first: $n) {
    nodes { ...pr }
  }
}
fragment pr on PullRequest {
  number title url isDraft updatedAt reviewDecision mergeable
  repository { nameWithOwner }
  viewerLatestReview { state submittedAt }
  commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state } } } }
}
"""


class GitHub(CodeHost):
    id = "github"
    name = "GitHub"
    logo = "GH"
    summary = "Pull requests waiting for your review, and yours, with their checks."
    instructions = "github.html"
    fields = [
        Field("token", "Personal access token", "password", "ghp_… or github_pat_…"),
    ]

    @staticmethod
    def _headers(credentials: dict[str, Any]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credentials['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        user = await request_json(
            http, "GET", f"{GITHUB_API}/user", "GitHub", headers=self._headers(values)
        )
        capability = {"id": "pulls", "label": "Read pull requests", "ok": True}
        try:
            await self.pull_requests(http, values)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        return Verified(user["login"], {"token": values["token"]}, [capability])

    async def pull_requests(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any]
    ) -> dict[str, list[PullRequest]]:
        data = await request_json(
            http,
            "POST",
            f"{GITHUB_API}/graphql",
            "GitHub",
            headers=self._headers(credentials),
            json={"query": GITHUB_QUERY, "variables": {"n": ITEMS}},
        )
        if data.get("errors") or "data" not in data:
            raise ProviderError("GitHub did not allow searching pull requests with this token.")

        def convert(node: dict[str, Any]) -> PullRequest:
            commits = node.get("commits", {}).get("nodes") or [{}]
            commit = commits[0].get("commit") or {}
            rollup = commit.get("statusCheckRollup") or {}
            state = rollup.get("state")
            mine = node.get("viewerLatestReview") or {}
            your_review = None
            if mine.get("state") == "APPROVED":
                your_review = "approved"
            elif mine.get("state"):
                # Asked for changes or commented: outdated once new commits came after it.
                newer = (commit.get("committedDate") or "") > (mine.get("submittedAt") or "")
                your_review = "outdated" if newer else "reviewed"
            return PullRequest(
                ref=f"#{node['number']}",
                title=node["title"],
                repo=node["repository"]["nameWithOwner"],
                url=node["url"],
                ci=GITHUB_STATES.get(state, "pending" if state else None),
                draft=node.get("isDraft", False),
                updated=node.get("updatedAt", ""),
                decision={"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}.get(
                    node.get("reviewDecision") or ""
                ),
                conflicts={"CONFLICTING": True, "MERGEABLE": False}.get(node.get("mergeable")),
                your_review=your_review,
            )

        return {
            key: [convert(n) for n in data["data"][key]["nodes"] if n] for key in ("review", "mine")
        }


# GitLab

GITLAB_STATES = {"SUCCESS": "success", "FAILED": "failure", "CANCELED": "failure"}
GITLAB_QUERY = """
query($n: Int!) {
  currentUser {
    username
    review: reviewRequestedMergeRequests(state: opened, first: $n) { nodes { ...mr } }
    mine: authoredMergeRequests(state: opened, first: $n) { nodes { ...mr } }
  }
}
fragment mr on MergeRequest {
  iid title webUrl draft updatedAt approved conflicts detailedMergeStatus
  project { fullPath }
  headPipeline { status }
  reviewers { nodes { username mergeRequestInteraction { reviewState } } }
}
"""


class GitLab(CodeHost):
    id = "gitlab"
    name = "GitLab"
    logo = "GL"
    summary = "Merge requests waiting for your review, and yours, with their pipelines."
    instructions = "gitlab.html"
    fields = [
        Field(
            "url",
            "GitLab address",
            "url",
            "https://gitlab.com",
            "For a self-managed GitLab, its address.",
        ),
        Field("token", "Personal access token", "password", "glpat-…"),
    ]

    @staticmethod
    def _base(values: dict[str, Any]) -> str:
        parts = urlsplit(values["url"].strip().rstrip("/"))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ProviderError("Enter the GitLab address, e.g. https://gitlab.com.")
        return f"{parts.scheme}://{parts.netloc}{parts.path}"

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        base = self._base(values)
        user = await request_json(
            http, "GET", f"{base}/api/v4/user", "GitLab", headers={"PRIVATE-TOKEN": values["token"]}
        )
        credentials = {"url": base, "token": values["token"]}
        capability = {"id": "merge_requests", "label": "Read merge requests", "ok": True}
        try:
            await self.pull_requests(http, credentials)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        host = urlsplit(base).netloc
        return Verified(f"{user['username']} · {host}", credentials, [capability])

    async def pull_requests(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any]
    ) -> dict[str, list[PullRequest]]:
        data = await request_json(
            http,
            "POST",
            f"{credentials['url']}/api/graphql",
            "GitLab",
            headers={"Authorization": f"Bearer {credentials['token']}"},
            json={"query": GITLAB_QUERY, "variables": {"n": ITEMS}},
        )
        user = (data.get("data") or {}).get("currentUser")
        if data.get("errors") or not user:
            raise ProviderError("GitLab did not allow reading merge requests with this token.")

        def convert(node: dict[str, Any]) -> PullRequest:
            status = (node.get("headPipeline") or {}).get("status")
            reviewers = (node.get("reviewers") or {}).get("nodes") or []
            mine = next((r for r in reviewers if r.get("username") == user.get("username")), {})
            state = (mine.get("mergeRequestInteraction") or {}).get("reviewState")
            decision = None
            if node.get("detailedMergeStatus") == "REQUESTED_CHANGES" or any(
                (r.get("mergeRequestInteraction") or {}).get("reviewState") == "REQUESTED_CHANGES"
                for r in reviewers
            ):
                decision = "changes_requested"
            elif node.get("approved"):
                decision = "approved"
            return PullRequest(
                ref=f"!{node['iid']}",
                title=node["title"],
                repo=node["project"]["fullPath"],
                url=node["webUrl"],
                ci=GITLAB_STATES.get(status, "pending" if status else None),
                draft=node.get("draft", False),
                updated=node.get("updatedAt", ""),
                decision=decision,
                conflicts=node.get("conflicts"),
                # GitLab marks a review as unreviewed again when the author asks for one.
                your_review={
                    "APPROVED": "approved",
                    "REVIEWED": "reviewed",
                    "REQUESTED_CHANGES": "reviewed",
                }.get(state or ""),
            )

        return {key: [convert(n) for n in user[key]["nodes"] if n] for key in ("review", "mine")}


# Bitbucket

BITBUCKET_API = "https://api.bitbucket.org/2.0"
BITBUCKET_REPOS = 30
BITBUCKET_PARALLEL = 5


class Bitbucket(CodeHost):
    id = "bitbucket"
    name = "Bitbucket"
    logo = "BB"
    summary = "Pull requests waiting for your review, and yours, with their builds."
    instructions = "bitbucket.html"
    fields = [
        Field("email", "Atlassian account email", "email"),
        Field("token", "API token", "password"),
        Field(
            "workspace",
            "Workspace",
            "text",
            "e.g. mycompany",
            "The workspace ID from bitbucket.org/<workspace>.",
        ),
    ]

    @staticmethod
    def _auth(credentials: dict[str, Any]) -> httpx2.BasicAuth:
        return httpx2.BasicAuth(credentials["email"], credentials["token"])

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        auth = self._auth(values)
        user = await request_json(http, "GET", f"{BITBUCKET_API}/user", "Bitbucket", auth=auth)
        workspace = values["workspace"].strip().strip("/").lower()
        await request_json(
            http, "GET", f"{BITBUCKET_API}/workspaces/{quote(workspace)}", "Bitbucket", auth=auth
        )
        credentials = {**values, "workspace": workspace, "uuid": user["uuid"]}
        capability = {"id": "pulls", "label": "Read pull requests", "ok": True}
        try:
            await self.pull_requests(http, credentials)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        return Verified(
            f"{user.get('display_name', values['email'])} · {workspace}", credentials, [capability]
        )

    async def pull_requests(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any]
    ) -> dict[str, list[PullRequest]]:
        auth = self._auth(credentials)
        workspace = quote(credentials["workspace"])
        uuid = credentials["uuid"]
        limit = asyncio.Semaphore(BITBUCKET_PARALLEL)

        async def get(url: str, **params: Any) -> Any:
            async with limit:
                return await request_json(http, "GET", url, "Bitbucket", auth=auth, params=params)

        mine_page, repos = await asyncio.gather(
            get(
                f"{BITBUCKET_API}/workspaces/{workspace}/pullrequests/{quote(uuid)}",
                state="OPEN",
                pagelen=ITEMS,
            ),
            get(
                f"{BITBUCKET_API}/repositories/{workspace}",
                sort="-updated_on",
                pagelen=BITBUCKET_REPOS,
            ),
        )
        # Bitbucket has no search for "waiting for my review", so the recently updated
        # repositories are asked one by one.
        review_pages = await asyncio.gather(
            *(
                get(
                    f"{BITBUCKET_API}/repositories/{workspace}/{quote(repo['slug'])}/pullrequests",
                    # With `q`, Bitbucket ignores the `state` parameter: the state goes in `q`.
                    q=f'state="OPEN" AND reviewers.uuid="{uuid}"',
                    pagelen=10,
                )
                for repo in repos.get("values", [])
            )
        )
        # Only open pull requests, even if Bitbucket returns merged or declined ones.
        review = [
            pr
            for page in review_pages
            for pr in page.get("values", [])
            if pr.get("state", "OPEN") == "OPEN"
        ][:ITEMS]
        mine = [pr for pr in mine_page.get("values", []) if pr.get("state", "OPEN") == "OPEN"][
            :ITEMS
        ]

        async def ci(pr: dict[str, Any]) -> str | None:
            url = pr.get("links", {}).get("statuses", {}).get("href")
            if not url:
                return None
            states = {s.get("state") for s in (await get(url, pagelen=20)).get("values", [])}
            if states & {"FAILED", "STOPPED"}:
                return "failure"
            if "INPROGRESS" in states:
                return "pending"
            return "success" if states else None

        async def details(pr: dict[str, Any]) -> dict[str, Any]:
            # The lists leave out the reviewers' decisions; the pull request itself has them.
            url = pr.get("links", {}).get("self", {}).get("href")
            return (await get(url)) if url else pr

        async def convert(pr: dict[str, Any]) -> PullRequest:
            full, status = await asyncio.gather(details(pr), ci(pr))
            participants = full.get("participants") or []
            states = [p.get("state") for p in participants if p.get("role") == "REVIEWER"]
            mine = next((p for p in participants if (p.get("user") or {}).get("uuid") == uuid), {})
            decision = None
            if "changes_requested" in states:
                decision = "changes_requested"
            elif states and all(state == "approved" for state in states):
                decision = "approved"
            return PullRequest(
                ref=f"#{pr['id']}",
                title=pr["title"],
                repo=pr.get("destination", {}).get("repository", {}).get("full_name", ""),
                url=pr.get("links", {}).get("html", {}).get("href", ""),
                ci=status,
                draft=pr.get("draft", False),
                updated=pr.get("updated_on", ""),
                decision=decision,
                your_review={"approved": "approved", "changes_requested": "reviewed"}.get(
                    mine.get("state") or ""
                ),
            )

        return {
            "review": list(await asyncio.gather(*(convert(pr) for pr in review))),
            "mine": list(await asyncio.gather(*(convert(pr) for pr in mine))),
        }
