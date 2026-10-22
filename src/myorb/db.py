from collections.abc import AsyncIterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Request
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Base(DeclarativeBase):
    """Base class for all myOrb database models."""


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


async def run_migrations(engine: AsyncEngine) -> None:
    """Upgrade the database to the latest migration."""

    def upgrade(connection: Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async with engine.begin() as connection:
        await connection.run_sync(upgrade)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency providing a database session for the request."""
    async with request.app.state.sessionmaker() as session:
        yield session
