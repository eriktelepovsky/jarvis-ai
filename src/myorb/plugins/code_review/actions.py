"""What Claude may do with pull requests from Ask, each after the user confirms it: approve a
GitHub pull request or comment on it (a comment can be undone, an approval cannot)."""

import re
from typing import Any

from myorb.connections.providers import ConnectedAccount, ProviderError, request_json
from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.code_review.providers import GITHUB_API, GitHub

PULL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/pull/(\d+)")
SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The pull request's ref: its GitHub address."},
        "comment": {
            "type": "string",
            "description": "In the user's words; may be empty to approve.",
        },
    },
    "required": ["url", "comment"],
    "additionalProperties": False,
}


def _pull(url: str) -> tuple[str, str]:
    match = PULL.match(url.strip())
    if not match:
        raise ActionError("Only GitHub pull requests can be approved or commented on from here.")
    return match.group(1), match.group(2)


def ask_actions(github: GitHub) -> list[AskAction]:
    async def call(
        context: WidgetContext, account: ConnectedAccount, method: str, path: str, **kwargs: Any
    ) -> Any:
        try:
            return await request_json(
                context.http,
                method,
                f"{GITHUB_API}/{path}",
                "GitHub",
                headers=github._headers(account.credentials),
                **kwargs,
            )
        except ProviderError as error:
            raise ActionError(str(error)) from error

    async def account(context: WidgetContext) -> ConnectedAccount:
        accounts = await context.connections.list(github.id)
        if not accounts:
            raise ActionError("No GitHub account is connected.")
        return accounts[0]

    def fresh(context: WidgetContext, connected: ConnectedAccount) -> None:
        context.cache.invalidate(f"{github.id}:{connected.id}:")

    async def approve_summary(context: WidgetContext, data: dict[str, Any]) -> str:
        repo, number = _pull(data["url"])
        await account(context)
        note = f" with “{data['comment']}”" if data["comment"] else ""
        return f"Approve {repo}#{number}{note}"

    async def approve(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        repo, number = _pull(data["url"])
        connected = await account(context)
        review = {"event": "APPROVE", **({"body": data["comment"]} if data["comment"] else {})}
        await call(context, connected, "POST", f"repos/{repo}/pulls/{number}/reviews", json=review)
        fresh(context, connected)
        return ActionResult(f"Approved {repo}#{number}.")

    async def comment_summary(context: WidgetContext, data: dict[str, Any]) -> str:
        repo, number = _pull(data["url"])
        await account(context)
        if not data["comment"].strip():
            raise ActionError("Write the comment.")
        return f"Comment on {repo}#{number}: “{data['comment']}”"

    async def comment(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        repo, number = _pull(data["url"])
        connected = await account(context)
        created = await call(
            context,
            connected,
            "POST",
            f"repos/{repo}/issues/{number}/comments",
            json={"body": data["comment"]},
        )
        comment_id = (created or {}).get("id")

        async def undo(context: WidgetContext) -> str:
            await call(context, connected, "DELETE", f"repos/{repo}/issues/comments/{comment_id}")
            return f"Removed the comment on {repo}#{number}."

        return ActionResult(f"Commented on {repo}#{number}.", undo if comment_id else None)

    return [
        AskAction(
            "approve_pull_request",
            "Approve a GitHub pull request the user was asked to review, as the user, "
            "optionally with a comment. It cannot be undone from here.",
            SCHEMA,
            approve_summary,
            approve,
        ),
        AskAction(
            "comment_pull_request",
            "Comment on a GitHub pull request, as the user, in their words and language.",
            SCHEMA,
            comment_summary,
            comment,
        ),
    ]
