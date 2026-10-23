"""What Claude may do in Jira from Ask, each after the user confirms it: move an issue to
another status, or comment on it. Both can be undone."""

from typing import Any

import httpx2

from myorb.connections.providers import ConnectedAccount, ProviderError, request_json
from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.jira import workspace_name

WHERE = {
    "workspace": {
        "type": "string",
        "description": "The workspace from the ref (AR-16 in archivis).",
    },
    "issue_key": {"type": "string", "description": "The issue key, e.g. AR-16."},
}


def _schema(**more: dict[str, Any]) -> dict[str, Any]:
    properties = {**WHERE, **more}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


async def _account(context: WidgetContext, workspace: str) -> ConnectedAccount:
    for account in await context.connections.list("jira"):
        if workspace_name(account.credentials["site"]) == workspace.strip().lower():
            return account
    raise ActionError(f"No Jira workspace {workspace} is connected.")


async def _call(
    context: WidgetContext, account: ConnectedAccount, method: str, path: str, **kwargs: Any
) -> Any:
    credentials = account.credentials
    try:
        return await request_json(
            context.http,
            method,
            f"{credentials['site']}/rest/api/3/{path}",
            "Jira",
            auth=httpx2.BasicAuth(credentials["email"], credentials["token"]),
            **kwargs,
        )
    except ProviderError as error:
        raise ActionError(str(error)) from error


def _fresh(context: WidgetContext, account: ConnectedAccount) -> None:
    """The issues are read again, so the dashboard shows the change."""
    context.cache.invalidate(f"jira:{account.id}:")


async def _transitions(context: WidgetContext, account: ConnectedAccount, key: str) -> list[dict]:
    data = await _call(context, account, "GET", f"issue/{key}/transitions")
    return (data or {}).get("transitions", [])


def _matching(transitions: list[dict], status: str) -> dict:
    wanted = status.strip().lower()
    for transition in transitions:
        names = {
            transition.get("name", "").lower(),
            (transition.get("to") or {}).get("name", "").lower(),
        }
        if wanted in names:
            return transition
    offered = ", ".join(
        sorted({(t.get("to") or {}).get("name", t.get("name", "")) for t in transitions})
    )
    raise ActionError(
        f"The issue cannot move to “{status}”. It can move to: {offered or 'nothing'}."
    )


async def _move_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    account = await _account(context, data["workspace"])
    transition = _matching(await _transitions(context, account, data["issue_key"]), data["status"])
    target = (transition.get("to") or {}).get("name", data["status"])
    return f"Move {data['issue_key']} ({data['workspace']}) to {target}"


async def _status(context: WidgetContext, account: ConnectedAccount, key: str) -> str:
    issue = await _call(context, account, "GET", f"issue/{key}", params={"fields": "status"})
    return ((issue or {}).get("fields", {}).get("status") or {}).get("name", "")


async def _move(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account = await _account(context, data["workspace"])
    key = data["issue_key"]
    before = await _status(context, account, key)
    transition = _matching(await _transitions(context, account, key), data["status"])
    await _call(
        context,
        account,
        "POST",
        f"issue/{key}/transitions",
        json={"transition": {"id": transition["id"]}},
    )
    _fresh(context, account)
    target = (transition.get("to") or {}).get("name", data["status"])

    async def undo(context: WidgetContext) -> str:
        back = _matching(await _transitions(context, account, key), before)
        await _call(
            context,
            account,
            "POST",
            f"issue/{key}/transitions",
            json={"transition": {"id": back["id"]}},
        )
        _fresh(context, account)
        return f"{key} is back in {before}."

    return ActionResult(f"Moved {key} from {before} to {target}.", undo)


def _document(text: str) -> dict[str, Any]:
    """Plain text as Jira's document format: a paragraph per paragraph."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


async def _comment_summary(context: WidgetContext, data: dict[str, Any]) -> str:
    await _account(context, data["workspace"])
    return f"Comment on {data['issue_key']} ({data['workspace']}): “{data['comment']}”"


async def _comment(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
    account = await _account(context, data["workspace"])
    key = data["issue_key"]
    comment = await _call(
        context, account, "POST", f"issue/{key}/comment", json={"body": _document(data["comment"])}
    )
    comment_id = (comment or {}).get("id")

    async def undo(context: WidgetContext) -> str:
        await _call(context, account, "DELETE", f"issue/{key}/comment/{comment_id}")
        return f"Removed the comment on {key}."

    return ActionResult(f"Commented on {key}.", undo if comment_id else None)


def ask_actions() -> list[AskAction]:
    return [
        AskAction(
            "move_issue",
            "Move one of the user's Jira issues to another status, e.g. In Progress or Done. "
            "status is the name of the status (or of the transition) as Jira shows it.",
            _schema(status={"type": "string"}),
            _move_summary,
            _move,
        ),
        AskAction(
            "comment_issue",
            "Add a comment to one of the user's Jira issues, as the user. Write it as they "
            "would, in their words and language.",
            _schema(comment={"type": "string"}),
            _comment_summary,
            _comment,
        ),
    ]
