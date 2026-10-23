"""What Claude may do in Asana from Ask, each after the user confirms it: complete a task,
change when it is due, or comment on it. All can be undone."""

from datetime import date
from typing import Any

from myorb.connections.providers import ConnectedAccount, ProviderError, request_json
from myorb.plugins import ActionError, ActionResult, AskAction, WidgetContext
from myorb.plugins.asana import API, Asana

TASK = {"type": "string", "description": "The task's id, from its ref (asana task 1234 → 1234)."}


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def ask_actions(provider: Asana) -> list[AskAction]:
    async def call(
        context: WidgetContext, account: ConnectedAccount, method: str, path: str, **kwargs: Any
    ) -> Any:
        try:
            data = await request_json(
                context.http,
                method,
                f"{API}/{path}",
                "Asana",
                headers=provider._headers(account.credentials),
                **kwargs,
            )
        except ProviderError as error:
            raise ActionError(str(error)) from error
        return (data or {}).get("data")

    async def find(context: WidgetContext, gid: str) -> tuple[ConnectedAccount, dict[str, Any]]:
        """The task and the connected account that can see it (usually the only one)."""
        if not gid.isdigit():
            raise ActionError("Name the task by its ref (asana task …).")
        for account in await context.connections.list("asana"):
            try:
                task = await call(
                    context,
                    account,
                    "GET",
                    f"tasks/{gid}",
                    params={"opt_fields": "name,due_on,completed"},
                )
            except ActionError:
                continue
            if task:
                return account, task
        raise ActionError("No connected Asana account has this task.")

    def fresh(context: WidgetContext, account: ConnectedAccount) -> None:
        context.cache.invalidate(f"asana:{account.id}:")

    async def update(
        context: WidgetContext, account: ConnectedAccount, gid: str, **fields: Any
    ) -> None:
        await call(context, account, "PUT", f"tasks/{gid}", json={"data": fields})
        fresh(context, account)

    async def complete_summary(context: WidgetContext, data: dict[str, Any]) -> str:
        _, task = await find(context, data["task_id"])
        return f"Complete “{task.get('name', '')}” in Asana"

    async def complete(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        account, task = await find(context, data["task_id"])
        await update(context, account, task["gid"], completed=True)

        async def undo(context: WidgetContext) -> str:
            await update(context, account, task["gid"], completed=False)
            return f"“{task['name']}” is open again in Asana."

        return ActionResult(f"Completed “{task['name']}” in Asana.", undo)

    def due(data: dict[str, Any]) -> str | None:
        if not data["due_on"]:
            return None
        try:
            return date.fromisoformat(data["due_on"]).isoformat()
        except ValueError:
            raise ActionError("Give the date as YYYY-MM-DD.") from None

    async def due_summary(context: WidgetContext, data: dict[str, Any]) -> str:
        _, task = await find(context, data["task_id"])
        when = due(data)
        return (
            f"Make “{task.get('name', '')}” due {when}"
            if when
            else f"Remove the due date of “{task.get('name', '')}”"
        )

    async def set_due(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        account, task = await find(context, data["task_id"])
        before = task.get("due_on")
        await update(context, account, task["gid"], due_on=due(data))

        async def undo(context: WidgetContext) -> str:
            await update(context, account, task["gid"], due_on=before)
            return f"“{task['name']}” is due as before."

        when = due(data)
        return ActionResult(
            f"“{task['name']}” is now due {when}."
            if when
            else f"“{task['name']}” has no due date now.",
            undo,
        )

    async def comment_summary(context: WidgetContext, data: dict[str, Any]) -> str:
        _, task = await find(context, data["task_id"])
        return f"Comment on “{task.get('name', '')}” in Asana: “{data['comment']}”"

    async def comment(context: WidgetContext, data: dict[str, Any]) -> ActionResult:
        account, task = await find(context, data["task_id"])
        story = await call(
            context,
            account,
            "POST",
            f"tasks/{task['gid']}/stories",
            json={"data": {"text": data["comment"]}},
        )
        story_id = (story or {}).get("gid")

        async def undo(context: WidgetContext) -> str:
            await call(context, account, "DELETE", f"stories/{story_id}")
            return f"Removed the comment on “{task['name']}”."

        return ActionResult(f"Commented on “{task['name']}” in Asana.", undo if story_id else None)

    return [
        AskAction(
            "complete_task",
            "Mark one of the user's Asana tasks as completed.",
            _schema(task_id=TASK),
            complete_summary,
            complete,
        ),
        AskAction(
            "set_due_date",
            "Change when one of the user's Asana tasks is due (YYYY-MM-DD), or remove its due "
            "date (empty due_on).",
            _schema(task_id=TASK, due_on={"type": "string"}),
            due_summary,
            set_due,
        ),
        AskAction(
            "comment_task",
            "Add a comment to one of the user's Asana tasks, as the user, in their words and "
            "language.",
            _schema(task_id=TASK, comment={"type": "string"}),
            comment_summary,
            comment,
        ),
    ]
