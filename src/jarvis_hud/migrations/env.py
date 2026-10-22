import asyncio

from alembic import context
from sqlalchemy import Connection

import jarvis_hud.auth.models  # noqa: F401  (registers models on Base.metadata)
import jarvis_hud.connections.models  # noqa: F401
from jarvis_hud.db import Base

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_with_settings() -> None:
    """Used by the alembic CLI: migrate the database configured by JARVIS_* settings."""
    from jarvis_hud.config import Settings
    from jarvis_hud.db import create_engine

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    do_run_migrations(connection)
else:
    asyncio.run(run_with_settings())
