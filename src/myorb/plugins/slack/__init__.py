"""Slack mentions of the user, found with Slack search through the user's own Slack app."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

API = "https://slack.com/api"
ICON = "M16 12a4 4 0 1 1-8 0a4 4 0 1 1 8 0M16 12v1.5a2.5 2.5 0 0 0 5 0V12a9 9 0 1 0-3.5 7.1"
MANIFEST = {
    "display_information": {
        "name": "myOrb",
        "description": "Shows your Slack mentions on your myOrb dashboard.",
    },
    "oauth_config": {"scopes": {"user": ["search:read"]}},
    "settings": {
        "org_deploy_enabled": False,
        "socket_mode_enabled": False,
        "token_rotation_enabled": False,
    },
}


@dataclass(frozen=True)
class Mention:
    channel: str
    author: str
    text: str
    sent: datetime
    url: str


def plain_text(text: str) -> str:
    """Slack markup to readable text.

    <url|label> becomes label, <@U123> becomes @someone and <#C1|dev> becomes #dev.
    """
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<@[A-Z0-9]+(?:\|([^>]+))?>", lambda m: f"@{m.group(1) or 'someone'}", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"<!(here|channel|everyone)[^>]*>", r"@\1", text)
    return " ".join(text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").split())


async def slack(http: httpx2.AsyncClient, method: str, token: str, **params: Any) -> dict[str, Any]:
    data = await request_json(
        http,
        "POST",
        f"{API}/{method}",
        "Slack",
        headers={"Authorization": f"Bearer {token}"},
        data=params,
    )
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        if error in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
            raise ProviderError(
                "Slack rejected the token. Install the app again and copy the new token."
            )
        if error == "missing_scope":
            raise ProviderError(
                "The token lacks the search:read permission. Use the manifest myOrb shows."
            )
        if error == "not_allowed_token_type":
            raise ProviderError("Use the User OAuth Token (xoxp-…), not the bot token.")
        raise ProviderError(f"Slack returned an error ({error}).")
    return data


class SlackWorkspace(Provider):
    icon = "hash"  # a channel's "#": Slack's own logo is not available to use
    id = "slack"
    name = "Slack"
    logo = "S"
    summary = "Messages that mention you. Connect each workspace separately."
    instructions = "slack.html"
    fields = [Field("token", "User OAuth Token", "password", "xoxp-…")]
    api_hosts = ("slack.com",)

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        token = values["token"]
        if not token.startswith("xoxp-"):
            raise ProviderError("Use the User OAuth Token, which starts with xoxp-.")
        identity = await slack(http, "auth.test", token)
        credentials = {
            "token": token,
            "user_id": identity["user_id"],
            "team": identity.get("team", ""),
            "url": identity.get("url", ""),
        }
        capability = {"id": "search", "label": "Search messages", "ok": True}
        try:
            await slack(http, "search.messages", token, query="test", count=1)
        except ProviderError as error:
            capability |= {"ok": False, "detail": str(error)}
        return Verified(
            identity.get("team") or identity.get("url", "Slack"), credentials, [capability]
        )

    async def mentions(
        self, http: httpx2.AsyncClient, credentials: dict[str, Any], now: datetime
    ) -> list[Mention]:
        since = now - timedelta(days=1)
        # Slack's after: is a date and exclusive; results are filtered to the last 24 hours.
        after = (since - timedelta(days=1)).date().isoformat()
        data = await slack(
            http,
            "search.messages",
            credentials["token"],
            query=f"<@{credentials['user_id']}> after:{after}",
            sort="timestamp",
            sort_dir="desc",
            count=50,
        )
        mentions = []
        for match in data.get("messages", {}).get("matches", []):
            if match.get("user") == credentials["user_id"]:
                continue
            sent = datetime.fromtimestamp(float(match.get("ts", 0)), UTC)
            if sent < since:
                continue
            channel = match.get("channel") or {}
            name = channel.get("name", "")
            mentions.append(
                Mention(
                    channel="Direct message" if channel.get("is_im") else f"#{name}",
                    author=match.get("username") or "someone",
                    text=plain_text(match.get("text", "")),
                    sent=sent,
                    url=match.get("permalink", ""),
                )
            )
        return mentions


def ago(sent: datetime, now: datetime) -> str:
    minutes = int((now - sent).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m ago"
    return f"{minutes // 60}h ago"


class SlackPlugin(Plugin):
    """Slack mentions from one or more workspaces."""

    id = "slack"
    name = "Slack"
    templates_dir = Path(__file__).parent / "templates"
    position = 45

    def __init__(self) -> None:
        self.provider = SlackWorkspace()

    def connections(self) -> list[Provider]:
        return [self.provider]

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="mentions",
                label="Mentions",
                title="Slack · mentions",
                icon=ICON,
                load=self._widget,
                weight=0.9,
                refresh=3 * 60,
            )
        ]

    async def _load(self, context: WidgetContext) -> list[AccountResult]:
        async def fetch(provider: Provider, account: ConnectedAccount) -> list[Mention]:
            return await self.provider.mentions(context.http, account.credentials, context.now)

        return await load_connected(
            context, [self.provider], "mentions", fetch, fresh=3 * 60, max_age=60 * 60
        )

    async def _widget(self, context: WidgetContext) -> WidgetData:
        results = await self._load(context)
        status = connected_status(
            results, "Slack", Link("Connect Slack", "/integrations/slack/connect")
        )
        loaded = [r for r in results if r.data is not None]
        if not loaded:
            return WidgetData(value=None, **status)
        several = len(loaded) > 1
        mentions = sorted(
            ((r.account, m) for r in loaded for m in r.data),
            key=lambda item: item[1].sent,
            reverse=True,
        )
        return WidgetData(
            value=len(mentions),
            summary="mentions in the last 24 hours",
            stats=[
                Stat(len(mentions), "Last 24 h"),
                Stat(len({m.channel for _, m in mentions}), "Channels"),
                Stat(len(loaded), "Workspaces"),
            ],
            list_title="Latest mentions",
            rows=[
                Row(
                    m.text[:140] or "(no text)",
                    " · ".join(p for p in (team if several else "", m.channel, m.author) if p),
                    ago(m.sent, context.now),
                    url=m.url,
                    group=team,
                )
                for team, m in mentions
            ],
            group_label="workspaces",
            **status,
        )

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        loaded = [r for r in await self._load(context) if r.data is not None]
        if not loaded:
            return None
        lines = [
            f"{m.author} in {m.channel} "
            f"({m.sent.astimezone(context.now.tzinfo):%H:%M}): {m.text[:200]}"
            for r in loaded
            for m in r.data
        ]
        return BriefSection("Slack mentions (last 24 hours)", lines or ["No new Slack mentions."])

    @staticmethod
    def manifest() -> str:
        return json.dumps(MANIFEST, indent=2)
