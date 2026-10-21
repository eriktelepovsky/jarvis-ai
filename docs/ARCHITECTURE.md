# Architecture

This document describes how Jarvis is built and why. It covers the decisions made before the MVP (see [Phase 0 in the roadmap](../ROADMAP.md#phase-0-mvp)).

## Overview

Jarvis is a single Python application that serves a web dashboard. Each data source is a plugin that contributes widgets. The browser loads the dashboard page, then fetches every widget separately, so a slow or failing integration never blocks the rest of the page.

```
Browser ── GET / ───────────────────────────▶ dashboard page with empty widget slots
        ── GET /widgets/<plugin>/<widget> ──▶ plugin loads data, renders its template
                                              (HTMX swaps the HTML into the slot)
```

## Decisions

| Area | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Rich ecosystem of API clients; the package is already on PyPI. |
| Web framework | FastAPI | Async I/O for many parallel third-party API calls; built-in support for streaming responses (chat) and WebSockets (voice). |
| Frontend | Server-rendered Jinja2 templates with HTMX | One codebase without a Node.js build. Plugins ship their widgets as plain templates. Small JavaScript islands are added only where needed (e.g. voice). |
| Database | SQLite through SQLAlchemy 2 (async) and Alembic | A single file in the data volume: no extra container and trivial backups. SQLAlchemy keeps PostgreSQL possible later. |
| Users | Single user per installation | Simpler data model, login and security. Each person runs their own Jarvis. |
| Plugins | Python entry points (`jarvis_hud.plugins`) | Standard packaging mechanism: built-in and third-party plugins are installed with `pip` and registered the same way. |
| AI | Claude via the Anthropic SDK | Chosen in the roadmap. |
| Deployment | Docker image with a data volume | Self-hosted on a VPS or NAS, reachable from a phone. |

## Project layout

```
src/jarvis_hud/
├── app.py            # FastAPI application factory (create_app)
├── cli.py            # `jarvis` command: `jarvis serve`
├── config.py         # Settings from JARVIS_* environment variables
├── db.py             # SQLAlchemy base, engine and migrations
├── auth/             # Login: passwords, passkeys, sessions, access gate
├── migrations/       # Alembic database migrations
├── plugins/
│   ├── base.py       # Plugin and Widget interfaces
│   ├── registry.py   # Entry point discovery
│   └── core/         # Built-in plugin (greeting and date)
└── web/
    ├── routes.py     # Dashboard and widget endpoints
    ├── templates/    # Base layout and dashboard
    └── static/       # CSS and vendored htmx
```

## Plugins

A plugin subclasses `Plugin` and returns a list of `Widget`s. Each widget has a template (relative to the plugin's `templates_dir`) and an async `load()` function that returns the template context.

```python
class WeatherPlugin(Plugin):
    id = "weather"
    name = "Weather"
    templates_dir = Path(__file__).parent / "templates"

    def widgets(self) -> list[Widget]:
        return [Widget(id="today", title="Weather", template="today.html", load=self.load_today)]

    async def load_today(self) -> dict[str, Any]:
        return {"temperature": 21}
```

The plugin is registered in its package's `pyproject.toml`:

```toml
[project.entry-points."jarvis_hud.plugins"]
weather = "jarvis_weather:WeatherPlugin"
```

Plugin templates are available under the plugin id prefix (`weather/today.html`), so plugins cannot override each other's or the app's templates. A plugin that fails to load is logged and skipped.

The interface will grow during the MVP with what the first real integrations need: connections (see below), background refresh and data for the AI brief.

## Integrations

Adding an integration has two steps: **installing** the plugin and **connecting** an account. Most users only do the second step, entirely in the Jarvis UI.

### Installing plugins

- **Built-in integrations** (Google, Jira, Slack, GitHub, Strava, weather and others) ship with Jarvis. Nothing to install.
- **Custom plugins** are Python packages. In Docker they are installed at container start from the `JARVIS_PLUGINS` environment variable (e.g. `JARVIS_PLUGINS="jarvis-wallet jarvis-invoices"`), or baked into a custom image built on top of the official one.

### Connecting accounts

Users find out what to do in three places:

1. **Onboarding** after the first login asks what the user wants on the dashboard (email, calendar, tasks…) and walks them through connecting it. It then becomes a **setup checklist**: each step's state (done, to do, blocked, skipped) is detected from the actual state of Jarvis, never ticked by hand, and links to the place where the step is completed.
2. **Integrations page** lists available integrations with a description, status per capability (see below), last sync and a **Connect** button.
3. **Empty widgets** show a call to action (e.g. "Connect Gmail") that opens the setup wizard directly.

Every setup wizard follows the same structure:

- numbered steps with links to the exact pages at the provider,
- values to copy with one click (redirect URI, Slack app manifest),
- a **Test connection** button with a clear error message when something is wrong.

| Service | Method | What the user does |
|---|---|---|
| Gmail, Calendar, YouTube | Google OAuth | Creates their own Google Cloud project and OAuth client (see below), then clicks "Sign in with Google". |
| Jira | Email and API token per workspace | Creates an API token in their Atlassian account. Each workspace (e.g. development, support) is a separate connection. |
| GitHub | Fine-grained personal access token | Creates a token via a link with the permissions prefilled. |
| Slack | Own Slack app | Jarvis generates an app manifest; the user pastes it into Slack, installs the app and copies the token. |
| Strava | OAuth with own API application | Same as Google, but simpler. |
| Apple Health | Webhook | Jarvis shows a URL with a secret token; an iPhone Shortcuts automation posts data to it. |
| Custom plugins | Defined by the plugin | The plugin declares its own form, e.g. URL and API key. |

### Google OAuth for self-hosted installations

A self-hosted app cannot share one Google OAuth client across all installations: access to Gmail would require Google's app verification and a paid security assessment. Each user therefore creates **their own Google Cloud project and OAuth client**. For personal use this is free and works without verification; Google only shows an "unverified app" warning at sign-in.

This is the hardest step of the whole setup, so its wizard is the most detailed one: screenshots, exact values to copy and an explanation of the warning.

Google accepts redirect URIs only over **HTTPS with a domain name** or on `localhost`. A Jarvis server therefore needs a domain; a bare IP address is not enough. The installation guide must state this clearly.

The OAuth flow is shared by all OAuth plugins:

- `state` and a PKCE verifier are kept in a short-lived (10 minutes), httpOnly, `SameSite=Lax` cookie scoped to the callback path, and compared in constant time in the callback.
- Only the refresh token is stored (encrypted); access tokens are obtained when needed.
- Query parameters from the provider are never echoed back into the page.

### Connection API

The `Plugin` interface gains a declaration of the connection it needs, and Jarvis generates the setup wizard from it:

- **Connection type:** OAuth2, API token, webhook or a custom form.
- **Form fields:** a Pydantic model, rendered as a form with validation.
- **Instructions:** setup steps in Markdown, shown in the wizard.
- **`test()`:** a check used by the Test connection button.
- **Status per capability:** instead of a single "connected" flag, each capability has its own state, e.g. Gmail "read: OK, create drafts: missing permission". The UI shows exactly what works and what to fix.
- **Multiple connections per plugin:** e.g. several Jira workspaces or two Gmail accounts.
- **Shared connections:** one Google connection is used by the Gmail, Calendar and YouTube plugins. Each requests additional OAuth scopes only when it needs them (incremental authorization).

Credentials are stored encrypted in the database (see below).

## Data refresh and caching

Data from integrations is cached with **stale-while-revalidate**:

- Each data source has its own freshness window (e.g. email 30 seconds, calendar 5 minutes, finances 30 minutes) and a maximum age after which stale data is no longer shown.
- While data is being refreshed, the previous data is shown immediately.
- Concurrent requests for the same data share one call to the provider (single flight).
- Failed calls are not cached; the widget shows the error together with the last good data and its age.
- Saving or changing a connection invalidates its cache.
- Caches are warmed at startup, and an in-process scheduler refreshes them in the background so the dashboard stays fast and API rate limits are respected.

## AI and actions

- **Morning brief:** rules first, AI second. Plugins rank their items with deterministic rules (e.g. a client or a real person ranks above a newsletter; items waiting for a reply carry over with the time they were first seen). Claude then writes the brief from this ranked list instead of from raw data, which makes the brief cheaper, faster and more predictable. The brief is generated on schedule and stored, so opening the dashboard does not call any API.
- **Chat context:** the chat receives a short summary of what is currently on screen, so questions like "what about this one?" work.
- **Safe actions:** actions that change something outside Jarvis (send, create, pay) are disabled until the user enables them in settings. Every action needs explicit confirmation in the UI, and email replies are never sent directly: Jarvis creates a Gmail draft for the user to review and send.
- **Testing without the API:** a stub AI provider returns deterministic responses and can trigger tools, so the brief, chat and actions are tested without calling Claude.

## Principles

- **Honest states:** Jarvis never shows "connected" for a connection that has not been verified, and never draws a chart or number without real data. Empty, loading and error states are explicit. Tests enforce this.
- **Logic separate from the web layer:** decisions (access control, ranking, cache freshness) live in pure functions that are unit-tested without a running server.
- **Sample data mode:** Jarvis can run with a built-in set of fictional data for development, screenshots and demos.

## Login

Jarvis has a single user who signs in with a password or a passkey (WebAuthn). The code is in `src/jarvis_hud/auth/`.

- **First run:** until a password is set, every page redirects to `/setup`. Setup is allowed from the machine Jarvis runs on (loopback address) or anywhere with a one-time token that Jarvis prints to its log at startup (`No password is set. Open …/setup?token=…`). A fresh installation is therefore never open to whoever finds it first, also in Docker where requests do not come from the loopback address.
- **Password:** at least 12 characters, hashed with scrypt (memory-hard, from the Python standard library). Failed sign-ins are limited to 10 per client in 15 minutes.
- **Sessions:** an HMAC-signed, httpOnly, `SameSite=Lax` cookie valid for 30 days, marked `Secure` when Jarvis runs over HTTPS. The signing key is stored in the database and replaced on every password change, which signs out all other devices.
- **Passkeys:** added on the Security page after signing in. They are discoverable credentials with required user verification (Face ID, Touch ID, Windows Hello, PIN), so signing in needs no user name. The WebAuthn challenge is kept in a signed cookie valid for 5 minutes.
- **Access gate:** a middleware calls `decide()` in `auth/gate.py`, a pure function that returns allow, redirect to setup, redirect to login, or reject. State-changing requests (`POST` and others) must carry an `Origin` header matching Jarvis (CSRF protection). If the decision cannot be made, the request is refused (fail closed).
- **Public address:** set `JARVIS_PUBLIC_URL` (e.g. `https://jarvis.example.com`) in production. It is the origin used for the CSRF check and the domain passkeys are bound to; without it, the address of each request is used.

Migrations run automatically at startup. During development, new migrations are created with `uv run alembic revision --autogenerate -m "…"`.

## Planned for the MVP

- **Configuration and secrets:** settings come from `JARVIS_*` environment variables (or a `.env` file). OAuth tokens and API keys entered in the UI are stored in the database, encrypted with a key from the environment.
- **Background refresh:** see [Data refresh and caching](#data-refresh-and-caching).
- **Docker:** a multi-stage image built with `uv`, running as a non-root user, with `/data` as the volume for the database.
