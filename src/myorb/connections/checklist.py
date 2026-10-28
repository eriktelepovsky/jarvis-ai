"""Get started: the steps to set myOrb up, in order, on top of the Integrations page.

Each step's state comes from myOrb itself (a key saved, an account connected), never from
ticking it off. The first three are what the welcome screen needs; the rest make it better.
The list goes once every step is done, or when the user hides it.
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.auth.store import list_passkeys
from myorb.connections import google, store

PROVIDER = "setup"
# The sources of work the step "Connect where you work" means; any one of them does.
WORK = ("github", "gitlab", "bitbucket", "jira", "asana", "slack")

State = Literal["done", "todo", "blocked"]


@dataclass(frozen=True)
class Step:
    title: str
    detail: str
    state: State
    url: str
    label: str
    required: bool = False
    # The button POSTs to `url` (signing in with Google), rather than linking to it.
    post: bool = False


async def _connected(db: AsyncSession, provider: str) -> bool:
    return bool(await store.list_connections(db, provider))


async def steps(request: Request, db: AsyncSession) -> list[Step]:
    box = request.app.state.secret_box
    client = await store.get_provider_settings(db, box, google.PROVIDER)
    claude = await request.app.state.brief.settings()
    google_account = await _connected(db, google.PROVIDER)
    work = [p for p in WORK if await _connected(db, p)]

    def state(done: bool, blocked: bool = False) -> State:
        return "done" if done else "blocked" if blocked else "todo"

    return [
        Step(
            "Connect an AI",
            "Claude, or OpenAI, Gemini, Mistral or a local model, with your own API key: it "
            "writes the daily brief and answers what you ask.",
            state(claude is not None),
            "/integrations/claude",
            "Connect an AI",
            required=True,
        ),
        Step(
            "Set up Google",
            "Your own Google Cloud OAuth client, for Gmail, Calendar and YouTube. It takes "
            "about 10 minutes and is free.",
            state(client is not None),
            "/integrations/google/setup",
            "Set up Google",
            required=True,
        ),
        Step(
            "Connect your Google account",
            "Today's schedule, unread email and new videos."
            if client
            else "Once the OAuth client is set up.",
            state(google_account, blocked=client is None),
            "/integrations/google/connect",
            "Connect Google",
            required=True,
            post=True,
        ),
        Step(
            "Add your place",
            "The weather for the day and when it will rain.",
            state(await _connected(db, "weather")),
            "/integrations/weather/connect",
            "Add place",
        ),
        Step(
            "Connect where you work",
            "Pull requests from GitHub, GitLab or Bitbucket, tasks from Jira or Asana, mentions "
            "in Slack: whichever you use.",
            state(bool(work)),
            "#sources",
            "Choose below",
        ),
        Step(
            "Add a passkey",
            "Sign in with Face ID, Touch ID or a security key. The password alone then no "
            "longer signs you in.",
            state(bool(await list_passkeys(db))),
            "/settings/security",
            "Add passkey",
        ),
    ]


async def hidden(request: Request, db: AsyncSession) -> bool:
    data = await store.get_provider_settings(db, request.app.state.secret_box, PROVIDER)
    return bool(data and data.get("hidden"))


async def hide(request: Request, db: AsyncSession, hide: bool = True) -> None:
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, {"hidden": hide})


async def shown(request: Request, db: AsyncSession) -> list[Step] | None:
    """The steps while there is one left and the list is not hidden, else None. Never in demo
    mode, where nothing is connected on purpose."""
    if request.app.state.settings.demo or await hidden(request, db):
        return None
    found = await steps(request, db)
    return found if any(step.state != "done" for step in found) else None
