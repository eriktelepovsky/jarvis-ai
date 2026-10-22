import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import tzinfo
from pathlib import Path

import httpx2
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from granian.utils.proxies import wrap_asgi_with_proxy_headers
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader, select_autoescape

from jarvis_hud import __version__, music, profile, voice
from jarvis_hud.auth import passkeys
from jarvis_hud.auth import routes as auth_routes
from jarvis_hud.auth.middleware import auth_middleware
from jarvis_hud.auth.store import load_auth_state
from jarvis_hud.brief import routes as brief_routes
from jarvis_hud.brief.service import BriefService
from jarvis_hud.cache import Cache
from jarvis_hud.config import Settings
from jarvis_hud.connections import form_routes
from jarvis_hud.connections import routes as connection_routes
from jarvis_hud.connections.accounts import Connections
from jarvis_hud.connections.google_accounts import GoogleAccounts
from jarvis_hud.crypto import SecretBox, load_secret_key
from jarvis_hud.db import create_engine, create_sessionmaker, run_migrations
from jarvis_hud.plugins import Plugin, discover_plugins
from jarvis_hud.plugins.install import add_to_path, plugins_dir
from jarvis_hud.tasks import routes as task_routes
from jarvis_hud.templating import register_filters
from jarvis_hud.timezone import resolve_timezone
from jarvis_hud.web.routes import app_context, router

WEB_DIR = Path(__file__).parent / "web"

logger = logging.getLogger(__name__)


def create_templates(plugins: dict[str, Plugin], timezone: tzinfo) -> Jinja2Templates:
    """Jinja environment with app templates and each plugin's templates under its id prefix."""
    plugin_loaders = {
        plugin.id: FileSystemLoader(plugin.templates_dir)
        for plugin in plugins.values()
        if plugin.templates_dir is not None
    }
    env = Environment(
        loader=ChoiceLoader(
            [FileSystemLoader(WEB_DIR / "templates"), PrefixLoader(plugin_loaders)]
        ),
        autoescape=select_autoescape(),
    )
    env.globals["version"] = __version__
    register_filters(env, timezone)
    return Jinja2Templates(env=env)


def create_app(
    settings: Settings | None = None,
    plugins: dict[str, Plugin] | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create the Jarvis app. `http_transport` replaces outgoing HTTP calls in tests."""
    settings = settings or Settings()
    add_to_path(plugins_dir(settings.data_dir))
    plugins = discover_plugins() if plugins is None else plugins

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.secret_box = SecretBox(load_secret_key(settings.secret_key, settings.data_dir))
        app.state.http = httpx2.AsyncClient(transport=http_transport, timeout=10)
        engine = create_engine(settings.database_url)
        await run_migrations(engine)
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.google = GoogleAccounts(
            app.state.sessionmaker, app.state.secret_box, app.state.http
        )
        app.state.connections = Connections(app.state.sessionmaker, app.state.secret_box)
        app.state.brief = BriefService(
            app.state.sessionmaker,
            app.state.secret_box,
            plugins=lambda: list(app.state.plugins.values()),
            context=lambda: app_context(app),
            transport=http_transport,
        )
        scheduler = asyncio.create_task(app.state.brief.run_scheduler())
        background = [
            asyncio.create_task(job)
            for plugin in plugins.values()
            if (job := plugin.background(lambda: app_context(app))) is not None
        ]
        async with app.state.sessionmaker() as db:
            app.state.auth = await load_auth_state(db)
        if not app.state.auth.password_set:
            setup_url = f"{(settings.public_url or '').rstrip('/')}/setup"
            logger.warning(
                "No password is set. Open %s on this machine, or from anywhere with %s?token=%s",
                setup_url,
                setup_url,
                app.state.auth.setup_token,
            )
        yield
        scheduler.cancel()
        for job in background:
            job.cancel()
        await app.state.http.aclose()
        await engine.dispose()

    app = FastAPI(title="Jarvis", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.plugins = plugins
    app.state.timezone = resolve_timezone(settings.timezone)
    app.state.cache = Cache()
    app.state.templates = create_templates(plugins, app.state.timezone)
    app.middleware("http")(auth_middleware)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(auth_routes.router)
    app.include_router(passkeys.router)
    app.include_router(connection_routes.router)
    app.include_router(task_routes.router)
    app.include_router(brief_routes.router)
    app.include_router(music.router)
    app.include_router(profile.router)
    app.include_router(voice.router)
    for plugin in plugins.values():
        if (plugin_router := plugin.router()) is not None:
            app.include_router(plugin_router)
    # Last: its /integrations/{provider}/... paths would otherwise shadow more specific ones.
    app.include_router(form_routes.router)
    app.include_router(router)
    return app


def create_server_app():
    """Entry point for `jarvis serve`: the app behind trusted reverse proxy header handling."""
    settings = Settings()
    wrapped = wrap_asgi_with_proxy_headers(create_app(settings), settings.trusted_proxies)

    # Granian's wrapper is a plain function returning a coroutine; an async function makes it
    # recognizable as an ASGI 3 app to any server or test client.
    async def app(scope, receive, send):
        await wrapped(scope, receive, send)

    return app
