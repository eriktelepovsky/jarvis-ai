# Architecture

This document describes how myOrb is built and why. It covers the decisions made before the MVP (see [Phase 0 in the roadmap](../ROADMAP.md#phase-0-mvp)).

## Overview

myOrb is a single Python application that serves a web app. After signing in, the user sees the **welcome screen**: an animated orb with numbers around it (unread emails, today's events…), today's schedule in a side column and the next event in the header. Each data source is a plugin that contributes these numbers (widgets) and side or header sections (panels). The browser loads the page, then fetches the widgets and each panel separately, so a slow or failing integration never blocks the rest of the page.

```
Browser ── GET / ─────────────────────────▶ welcome screen with empty slots
        ── GET /welcome/widgets ──────────▶ every widget's number and details dialog
        ── GET /panels/<plugin>/<panel> ──▶ plugin loads data, renders its template
                                              (HTMX swaps the HTML into the slot)
```

## Decisions

| Area | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Rich ecosystem of API clients; the package is already on PyPI. |
| Web server | Granian | Fast ASGI server written in Rust, with HTTP/2 and WebSockets (voice). It also handles `X-Forwarded-*` headers from trusted reverse proxies. |
| Web framework | FastAPI | Async I/O for many parallel third-party API calls; built-in support for streaming responses (chat) and WebSockets (voice). |
| Frontend | Server-rendered Jinja2 templates with HTMX | One codebase without a Node.js build. Plugins ship their widgets as plain templates. Small JavaScript islands are added only where needed (e.g. voice). |
| Visual design | Dark HUD theme from the "myOrb Dashboard Design" canvas in Claude Design | Tokens (colors, type, spacing, radii) are CSS custom properties in `web/static/style.css`; components never use raw colors. Fonts (IBM Plex Sans, JetBrains Mono, Barlow Condensed, Chakra Petch, OFL) are bundled, so myOrb makes no requests to Google Fonts. The welcome screen's orb, clock, sounds and ambient music are one JavaScript file (`welcome.js`) without libraries; sounds and music are synthesized with Web Audio. The command palette (`palette.js`, ⌘K) needs no server. It searches the rows already on the page, in every number's details dialog, and its commands click what the page has (a quick action, the music button). |
| Database | SQLite through SQLAlchemy 2 (async) and Alembic | A single file in the data volume: no extra container and trivial backups. SQLAlchemy keeps PostgreSQL possible later. |
| Users | Single user per installation | Simpler data model, login and security. Each person runs their own myOrb. |
| Plugins | Python entry points (`myorb.plugins`) | Standard packaging mechanism: built-in and third-party plugins are installed with `pip` and registered the same way. |
| AI | Claude via the Anthropic SDK | Chosen in the roadmap. |
| Deployment | Docker image with a data volume | Self-hosted on a VPS or NAS, reachable from a phone. |

## Project layout

```
src/myorb/
├── app.py            # FastAPI application factory (create_app)
├── cli.py            # `myorb` command: `myorb serve`
├── config.py         # Settings from MYORB_* environment variables
├── db.py             # SQLAlchemy base, engine and migrations
├── crypto.py         # Encryption of stored credentials
├── connections/      # Connected accounts: Google OAuth, integrations page
├── tasks/            # Built-in to-do list: parsing due dates, page, widget, panel
├── brief/            # Daily AI brief: facts from plugins, Claude, schedule, report
├── cache.py          # Stale-while-revalidate cache for integration data
├── auth/             # Login: passwords, passkeys, sessions, access gate
├── migrations/       # Alembic database migrations
├── plugins/
│   ├── base.py       # Plugin, Widget and Panel interfaces
│   ├── registry.py   # Entry point discovery
│   ├── gmail/        # Unread and today's emails
│   ├── code_review/  # Pull requests from GitHub, GitLab and Bitbucket
│   ├── jira/         # Jira issues assigned to you
│   ├── asana/        # Asana tasks assigned to you
│   ├── custom/       # The user's own apps, answering with JSON in myOrb's format
│   ├── weather/      # Forecast from Open-Meteo (no API key)
│   ├── youtube/      # New videos from YouTube subscriptions
│   ├── slack/        # Slack mentions (own Slack app with search:read)
│   ├── health/       # Apple Health webhook, Strava OAuth
│   ├── uptime/       # Own website checks every minute, outages, TLS expiry
│   └── calendar/     # Today's and upcoming events
└── web/
    ├── routes.py     # Welcome screen, widget and panel endpoints
    ├── templates/    # Layouts, welcome screen, settings pages
    └── static/       # CSS and vendored htmx
```

## Plugins

A plugin subclasses `Plugin` and adds two kinds of content to the welcome screen:

- **Widgets** are the numbers around the orb. `load(context)` returns structured `WidgetData`: the number, a summary, three stats, a list of rows (title, subtitle, badge, link) and the main action. myOrb renders the node and its details dialog from it, so every widget looks the same and plugins ship no HTML. When there is no data, `value` is `None` and `message` explains why (not connected, failed, stale) with an action to fix it.
- **Panels** are sections in the side column or the header line, rendered from the plugin's own template (relative to its `templates_dir`). The Calendar plugin uses them for today's schedule and the next event.

The `WidgetContext` carries the current time in the user's time zone and the services a plugin may use: the cache and the connected Google accounts. `position` orders plugins on the screen and `weight` makes a widget's node bigger.

```python
class WeatherPlugin(Plugin):
    id = "weather"
    name = "Weather"

    def widgets(self) -> list[Widget]:
        return [Widget(id="now", label="°C", title="Weather", icon="M3 12h18", load=self.load_now)]

    async def load_now(self, context: WidgetContext) -> WidgetData:
        return WidgetData(value=21, summary="°C · overcast", source="Open-Meteo")
```

The plugin is registered in its package's `pyproject.toml`:

```toml
[project.entry-points."myorb.plugins"]
weather = "myorb_weather:WeatherPlugin"
```

Panel templates are available under the plugin id prefix (`calendar/today.html`), so plugins cannot override each other's or the app's templates. A plugin that fails to load is logged and skipped.

The interface will grow during the MVP with what the first real integrations need: connections (see below), background refresh and data for the AI brief.

Widget rows can carry **actions** (buttons that POST to a URL, e.g. "Done") and a widget can offer an **input** (a one-line form, e.g. "Add a task"). They are sent with HTMX; the endpoint answers with the `HX-Trigger: myorb:refresh` header, and the welcome screen reloads its widgets and panels, reopening the dialog the user was in.

## Tasks

The to-do list is built into myOrb (`src/myorb/tasks/`) and appears as the "Tasks" widget, a side panel with what is overdue or due today, and the `/tasks` page for editing, deleting and the last week's done tasks.

Due dates are written in plain language and recognized by rules, not AI (`tasks/parse.py`), in Slovak and English: today/dnes, tomorrow/zajtra, pozajtra, weekdays (Friday, v piatok), dates (24.10., 24.10.2026, 2026-10-24) and times (10:00, 3pm, o 10). Only full weekday names count, and only valid dates are taken out of the title, so "verzia 2.0" or "the sun" stay as written. A task is overdue when its date has passed, or when its time today has.

## Integrations

Adding an integration has two steps: **installing** the plugin and **connecting** an account. Most users only do the second step, entirely in the myOrb UI.

### Installing plugins

- **Built-in integrations** (Google, Jira, Slack, GitHub, Strava, weather and others) ship with myOrb. Nothing to install.
- **Other AI services** (`brief/compatible.py`): the AI settings (`ClaudeSettings.service`, `base_url`) can choose an OpenAI-compatible API instead of Claude. `BriefService.client()` then returns a `CompatibleClient`, which answers the Anthropic SDK calls myOrb makes (`messages.create`, `beta.messages.create`, `beta.messages.stream`). Anthropic's messages and tools go out as Chat Completions, and the answers come back as content blocks, so the brief, Ask and email features need no change. JSON schemas fall back to JSON mode when a service rejects them. Claude-only parts are skipped: model and effort, the cache warm-up, cost counting. Saving checks the address, the key and the model against the service's `/models`.
- **Work** (`plugins/work`) is added when both Jira and Asana are installed. With the user's choice (provider setting `work`), its one number replaces theirs on the welcome screen (`web.routes.shown_widgets`). It loads both widgets and merges their rows, grouped by workspace (named with its service) and then project. Their brief sections and Ask's rows are unchanged.
- **Demo mode** (`MYORB_DEMO=1`, `demo.py`) swaps the plugins for ones with a made-up day and the brief service for one that writes the same morning brief; Ask streams a prewritten plan of what is left of the day. The real plugins' templates and the dashboard's code render it, so it also produces the README's screenshots (`scripts/screenshots.py`). `MYORB_PUBLIC_DEMO=1` opens it to anyone. The access gate allows every request without a session, sends `/setup` and `/login` to the dashboard, and turns down every change except Ask with a 403 marked `X-Myorb-Demo`. `app.js` then leaves the page as it is and flashes the header's note. Each morning the tasks, activity and briefs are seeded again.
- **Custom plugins** are Python packages. `MYORB_PLUGINS` lists them as pip would (names with versions, wheel URLs, local paths); `myorb serve` installs them with `uv` into `plugins/` in the data directory, which goes last on `sys.path`, so they survive image updates and cannot shadow myOrb's own packages. A custom image built on top of the official one works too. See [writing plugins](PLUGINS.md) and the [example plugin](../examples/myorb-plugin-example).

### Connecting accounts

Users find out what to do in three places:

1. **Get started**, a setup checklist on top of the Integrations page (`connections/checklist.py`), lists the steps in order: Claude, Google's OAuth client and account (needed), then the weather, a source of work and a passkey (optional). Each step's state (done, to do, waiting for an earlier one) is detected from the actual state of myOrb, never ticked by hand, and links to the place where the step is completed. The welcome screen's header shows its progress until every step is done or the list is hidden.
2. **Integrations page** lists available integrations with a description, status per capability (see below), last sync and a **Connect** button.
3. **Widgets without data** show "—" around the orb, and their details explain why with a call to action (e.g. "Connect Google").

Every setup wizard follows the same structure:

- numbered steps with links to the exact pages at the provider,
- values to copy with one click (redirect URI, Slack app manifest),
- a **Test connection** button with a clear error message when something is wrong.

| Service | Method | What the user does |
|---|---|---|
| Gmail, Calendar, YouTube | Google OAuth | Creates their own Google Cloud project and OAuth client (see below), then clicks "Sign in with Google". |
| Custom source | Address and token per source | Adds an address to their own app that answers with JSON in myOrb's format ([docs/CUSTOM_SOURCES.md](CUSTOM_SOURCES.md)). |
| Asana | Personal access token | Creates a token under My apps in Asana. One token covers all their workspaces. |
| Jira | Email and API token per workspace | Creates an API token in their Atlassian account. Each workspace (e.g. development, support) is a separate connection. |
| GitHub | Fine-grained personal access token | Creates a token via a link with the permissions prefilled. |
| Slack | Own Slack app | myOrb generates an app manifest; the user pastes it into Slack, installs the app and copies the token. |
| Strava | OAuth with own API application | Same as Google, but simpler. |
| Uptime Kuma | Address and API key | Creates an API key in Kuma (Settings → API Keys). myOrb reads its monitors' state from `/metrics` every minute and keeps them with its own monitors, without checking them again; outages, uptime, the brief and the activity work the same for both. |
| Apple Health | Webhook | myOrb shows a URL with a secret token; an iPhone Shortcuts automation posts data to it. |
| Custom plugins | Defined by the plugin | The plugin declares its own form, e.g. URL and API key. |

### Google OAuth for self-hosted installations

A self-hosted app cannot share one Google OAuth client across all installations: access to Gmail would require Google's app verification and a paid security assessment. Each user therefore creates **their own Google Cloud project and OAuth client**. For personal use this is free and works without verification; Google only shows an "unverified app" warning at sign-in.

This is the hardest step of the whole setup, so its wizard is the most detailed one: screenshots, exact values to copy and an explanation of the warning.

Google accepts redirect URIs only over **HTTPS with a domain name** or on `localhost`. A myOrb server therefore needs a domain; a bare IP address is not enough. The installation guide must state this clearly.

The OAuth flow is shared by all OAuth plugins:

- `state` and a PKCE verifier are kept in a short-lived (10 minutes), httpOnly, `SameSite=Lax` cookie scoped to the callback path, and compared in constant time in the callback.
- Only the refresh token is stored (encrypted); access tokens are obtained when needed.
- Query parameters from the provider are never echoed back into the page.

### Connection API

The `Plugin` interface gains a declaration of the connection it needs, and myOrb generates the setup wizard from it:

- **Connection type:** OAuth2, API token, webhook or a custom form.
- **Form fields:** a Pydantic model, rendered as a form with validation.
- **Instructions:** setup steps in Markdown, shown in the wizard.
- **`test()`:** a check used by the Test connection button.
- **Status per capability:** instead of a single "connected" flag, each capability has its own state, e.g. Gmail "read: OK, create drafts: missing permission". The UI shows exactly what works and what to fix.
- **Multiple connections per plugin:** e.g. several Jira workspaces or two Gmail accounts.
- **Shared connections:** one Google connection is used by the Gmail, Calendar and YouTube plugins. Each requests additional OAuth scopes only when it needs them (incremental authorization).

### Form-based connections

Services that use an API token, a URL or a place name instead of OAuth are declared by plugins as `Provider`s (`connections/providers.py`): the form fields, the setup instructions (a template of the plugin) and `verify()`, which checks the values with the service and returns the account's name, what to store and which capabilities work. myOrb then renders the connect form, stores the values encrypted in `connections`, tests them again on request and lists them on the Integrations page, all through generic routes (`connections/form_routes.py`). Plugins read their accounts through `context.connections` and load data per account through the cache (`plugins/connection_data.py`).

One plugin can declare several providers: the Pull requests plugin connects GitHub (GraphQL search), GitLab (GraphQL, also self-managed) and Bitbucket (REST; it has no "waiting for my review" search, so the 30 most recently updated repositories of the workspace are queried), and shows them as one number. Jira connects each site (workspace) separately with an Atlassian API token. Slack uses the user's own Slack app, created from a manifest myOrb shows, with only the `search:read` user scope; mentions come from Slack search.

### Health

Apple Health data never leaves the iPhone on its own, so a Shortcut automation sends sleep, steps and optionally resting heart rate and active energy every morning to `/hooks/health/<token>`, a webhook with a secret token myOrb generates (new token = old address stops working). Values are stored per day; fields the iPhone did not send keep their earlier value. `/hooks/` paths skip the session and Origin checks; each webhook authenticates itself. Strava uses OAuth with the user's own Strava API application; Strava rotates refresh tokens, so the new one is stored after every refresh.

### Uptime

The Uptime plugin runs a background loop (`Plugin.background()`) that requests each monitored address every minute (up: an answer below HTTP 400 within 10 seconds) and reads https certificates once an hour. A monitor is down after two failed checks in a row; going down opens an incident and coming back closes it, and 30-day uptime is computed from the incidents. Network checks run in parallel, database updates one at a time.

### Stored credentials

OAuth client secrets and tokens are stored in the database encrypted with Fernet (AES with an HMAC, from the `cryptography` package) and are never sent to the browser. The key comes from `MYORB_SECRET_KEY`; when it is unset, myOrb generates a random key in `secret.key` in the data directory (readable only by its owner) on first start.

The generated key protects copies of the database on their own, e.g. in a backup. Anyone with the whole data directory also has the key; setting `MYORB_SECRET_KEY` from a secret store keeps the two apart. Losing the key means connecting all accounts again.

### Google implementation

The first connection, Google, lives in `src/myorb/connections/`; the generic Connection API above will be extracted from it when the second provider arrives.

- The setup wizard (`/integrations/google/setup`) walks through creating the Google Cloud project, enabling the Gmail and Calendar APIs, publishing the app (apps in *Testing* lose access every 7 days) and creating the OAuth client. It shows the exact redirect URI to copy.
- One connection is stored per Google account. Connecting requests the scopes of all capabilities (`gmail.readonly`, `gmail.modify`, `calendar.readonly`, `calendar.events`, `youtube.readonly`) with offline access; accounts connected before a capability was added are asked to connect again.
- YouTube: the subscriptions come from the YouTube Data API (cached for 12 hours, a few quota units a day) and the videos from each channel's public RSS feed, which costs no quota.
- After connecting and on **Test connection**, myOrb refreshes the access token and makes a cheap read request per capability. The result tells apart a revoked connection, a permission that was not granted and an API that is not enabled in the Google project (with a link to enable it).
- Disconnecting revokes the token at Google and deletes it.
- Tests run the whole flow against a fake Google that verifies the PKCE challenge, client credentials and redirect URI.

## Data refresh and caching

Data from integrations is cached with **stale-while-revalidate**:

- Each data source has its own freshness window (e.g. email 30 seconds, calendar 5 minutes, finances 30 minutes) and a maximum age after which stale data is no longer shown.
- While data is being refreshed, the previous data is shown immediately.
- Concurrent requests for the same data share one call to the provider (single flight).
- Failed calls are not cached; the widget shows the error together with the last good data and its age.
- Saving or changing a connection invalidates its cache.
- Planned: caches are warmed at startup, and an in-process scheduler refreshes them in the background so the welcome screen stays fast and API rate limits are respected.

The cache is `myorb/cache.py`, kept in memory. The Gmail widget uses a 1-minute freshness window and the Calendar widget 5 minutes. Google access tokens are cached until shortly before they expire, so loading the welcome screen costs no token refresh. The browser refreshes the widgets every 5 minutes, but not while a details dialog is open.

Times are shown in the user's time zone: `MYORB_TIMEZONE` (e.g. `Europe/Bratislava`), else the system time zone. Docker containers usually run in UTC, so set it there.

## AI and actions

- **Morning brief:** rules first, AI second. Each plugin's `brief(context)` returns a `BriefSection`: plain-text facts in the order the plugin ranks them (today's events with times, unread senders and subjects, overdue and due tasks). Claude writes the brief from these facts instead of from raw data, which makes it cheaper, faster and more predictable; ranking rules such as "a real person above a newsletter" go into the plugins over time. The brief is written once a day at the time the user sets (and on request), stored in the database, and shown as a headline in the orb's core and a full report dialog with "Read aloud" (browser speech synthesis), so opening myOrb does not call any API.
- **Claude request:** the model chosen in the Claude settings (Claude Sonnet 5.5 by default; Opus 5.5 or Haiku 5.5 at effort `low`) with effort `medium` and a JSON schema (`output_config.format`) for the headline, summary paragraphs and up to five priorities; server-side refusal fallback (`fallbacks: "default"`; Haiku has none, so its requests go without it). The system prompt forbids inventing facts. The user's own API key is stored encrypted like other credentials and checked with a free Models API call when saved. The code is in `src/myorb/brief/`; tests use a fake Anthropic API that checks the request.
- **Chat context:** the chat receives a short summary of what is currently on screen, so questions like "what about this one?" work.
- **Safe actions:** actions that change something outside myOrb (send, create, pay) are disabled until the user enables them in settings. Every action needs explicit confirmation in the UI, and email replies are never sent directly: myOrb creates a Gmail draft for the user to review and send.
- **Testing without the API:** a stub AI provider returns deterministic responses and can trigger tools, so the brief, chat and actions are tested without calling Claude.

## Principles

- **Honest states:** myOrb never shows "connected" for a connection that has not been verified, and never draws a chart or number without real data. Empty, loading and error states are explicit. Tests enforce this.
- **Logic separate from the web layer:** decisions (access control, ranking, cache freshness) live in pure functions that are unit-tested without a running server.
- **Sample data mode:** myOrb can run with a built-in set of fictional data for development, screenshots and demos.

## Login

myOrb has a single user who signs in with a password or a passkey (WebAuthn). The code is in `src/myorb/auth/`.

- **First run:** until a password is set, every page redirects to `/setup`. Setup is allowed from the machine myOrb runs on (loopback address) or anywhere with a one-time token that myOrb prints to its log at startup (`No password is set. Open …/setup?token=…`). A fresh installation is therefore never open to whoever finds it first, also in Docker where requests do not come from the loopback address.
- **Password:** at least 12 characters, hashed with scrypt (memory-hard, from the Python standard library). Failed sign-ins are limited to 10 per client in 15 minutes.
- **Sessions:** an HMAC-signed, httpOnly, `SameSite=Lax` cookie valid for 30 days, marked `Secure` when myOrb runs over HTTPS. The signing key is stored in the database and replaced on every password change, which signs out all other devices.
- **Passkeys:** added on the Security page after signing in. They are discoverable credentials with required user verification (Face ID, Touch ID, Windows Hello, PIN), so signing in needs no user name. The WebAuthn challenge is kept in a signed cookie valid for 5 minutes.
- **Passkeys replace the password:** once a passkey is added, the password no longer signs anyone in. A passkey with user verification is two factors in one (the device that holds the key, the face, finger or PIN that unlocks it), the password only one, so keeping it as a way in would undo that. The password then only confirms changing it. Lost every passkey? `myorb remove-passkeys`, run on the server (e.g. `docker compose exec myorb myorb remove-passkeys`), removes them, and the password signs in again.
- **Access gate:** a middleware calls `decide()` in `auth/gate.py`, a pure function that returns allow, redirect to setup, redirect to login, or reject. State-changing requests (`POST` and others) must carry an `Origin` header matching myOrb (CSRF protection). If the decision cannot be made, the request is refused (fail closed).
- **Public address:** set `MYORB_PUBLIC_URL` (e.g. `https://myorb.example.com`) in production. It is the origin used for the CSRF check and the domain passkeys are bound to; without it, the address of each request is used.

Migrations run automatically at startup. During development, new migrations are created with `uv run alembic revision --autogenerate -m "…"`.

## Deployment

myOrb is distributed as a Docker image, `ghcr.io/eriktelepovsky/myorb`, built for `linux/amd64` and `linux/arm64` and published on every GitHub release (`latest`, `X.Y.Z` and `X.Y` tags). `compose.yaml` in the repository is a ready-to-use example.

- **Image:** a multi-stage build with `uv`. The final stage contains only Python and the virtual environment, runs as the non-root user `myorb` (UID 1000) and stores the database in the `/data` volume.
- **Health check:** `GET /healthz` returns `ok` without signing in. The image's `HEALTHCHECK` uses it, and so can uptime monitors.
- **First run:** requests reach the container from the Docker network, not from the loopback address, so setup always needs the token from `docker logs myorb`.
- **Reverse proxy:** set `MYORB_PUBLIC_URL` to the public HTTPS address. It is used for the CSRF origin check, passkeys and `Secure` cookies, so myOrb does not have to trust the `Host` and `X-Forwarded-Proto` headers. Set `MYORB_TRUSTED_PROXIES` to the proxy's address or network (e.g. the Docker network `172.16.0.0/12`) so that myOrb sees real client IPs; otherwise all clients share the proxy's IP and the sign-in throttle applies to all of them together.
- **CI:** every pull request builds the image and checks that the container becomes healthy and redirects to setup.
- **Plugins:** installing custom plugins with `MYORB_PLUGINS` comes with the plugin system (phase 1).

## Background music

The music button on the welcome screen plays either the built-in ambient pad (synthesized with Web Audio in `welcome.js`) or a YouTube video, playlist or live stream chosen on Integrations → Music (`music.py`). YouTube's terms require its player to stay visible, so YouTube music plays in a small player in the corner (at least 200 px tall) through the official IFrame Player API. The API loads only when the music starts, from `youtube-nocookie.com`; volume follows myOrb's slider.

## Installable app (PWA)

myOrb can be installed on a phone or desktop like an app: `/manifest.webmanifest` describes it (name, colors, icons in `web/static/icons/`), and `/sw.js` is a small service worker served from the root so it controls every page. The service worker never caches pages or data, only static files (network first, so updates arrive at once) and an offline page shown when the server cannot be reached. Both URLs and the icons are public, like `/healthz`. Pages respect the phone's safe areas (`viewport-fit=cover`, `env(safe-area-inset-*)`), since an installed app runs under the status bar.

Browsers install apps and run service workers only over HTTPS or on `localhost`, so on a phone myOrb needs its HTTPS address (`MYORB_PUBLIC_URL`); iOS can still add a plain-HTTP myOrb to the home screen, without the offline page.

## Planned for the MVP

- **Configuration:** settings come from `MYORB_*` environment variables (or a `.env` file).
- **Background refresh:** see [Data refresh and caching](#data-refresh-and-caching).
