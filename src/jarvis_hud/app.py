import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from granian.utils.proxies import wrap_asgi_with_proxy_headers
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader, select_autoescape

from jarvis_hud import __version__
from jarvis_hud.auth import passkeys
from jarvis_hud.auth import routes as auth_routes
from jarvis_hud.auth.middleware import auth_middleware
from jarvis_hud.auth.store import load_auth_state
from jarvis_hud.config import Settings
from jarvis_hud.db import create_engine, create_sessionmaker, run_migrations
from jarvis_hud.plugins import Plugin, discover_plugins
from jarvis_hud.web.routes import router

WEB_DIR = Path(__file__).parent / "web"

logger = logging.getLogger(__name__)


def create_templates(plugins: dict[str, Plugin]) -> Jinja2Templates:
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
    return Jinja2Templates(env=env)


def create_app(
    settings: Settings | None = None, plugins: dict[str, Plugin] | None = None
) -> FastAPI:
    settings = settings or Settings()
    plugins = discover_plugins() if plugins is None else plugins

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(settings.database_url)
        await run_migrations(engine)
        app.state.sessionmaker = create_sessionmaker(engine)
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
        await engine.dispose()

    app = FastAPI(title="Jarvis", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.plugins = plugins
    app.state.templates = create_templates(plugins)
    app.middleware("http")(auth_middleware)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(auth_routes.router)
    app.include_router(passkeys.router)
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
