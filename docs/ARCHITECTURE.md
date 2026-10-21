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
├── db.py             # SQLAlchemy base and engine
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

The interface will grow during the MVP with what the first real integrations need: per-plugin settings, OAuth connections, background refresh and data for the AI brief.

## Planned for the MVP

- **Configuration and secrets:** settings come from `JARVIS_*` environment variables (or a `.env` file). OAuth tokens and API keys entered in the UI are stored in the database, encrypted with a key from the environment.
- **Login:** password and passkeys (WebAuthn) with a session cookie. Every route except login and static files requires authentication.
- **Background refresh:** an in-process scheduler periodically fetches data from integrations and caches it in the database. Widgets render from the cache, so the dashboard stays fast and API rate limits are respected.
- **Database migrations:** Alembic is set up together with the first database model.
- **Docker:** a multi-stage image built with `uv`, running as a non-root user, with `/data` as the volume for the database.
