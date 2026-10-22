from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.connections.models import Connection, ProviderSettings
from myorb.crypto import SecretBox


async def get_provider_settings(
    db: AsyncSession, box: SecretBox, provider: str
) -> dict[str, Any] | None:
    row = await db.get(ProviderSettings, provider)
    return box.decrypt(row.data) if row else None


async def save_provider_settings(
    db: AsyncSession, box: SecretBox, provider: str, data: dict[str, Any]
) -> None:
    row = await db.get(ProviderSettings, provider)
    if row is None:
        row = ProviderSettings(provider=provider)
        db.add(row)
    row.data = box.encrypt(data)
    row.updated_at = datetime.now(UTC)
    await db.commit()


async def delete_provider_settings(db: AsyncSession, provider: str) -> None:
    row = await db.get(ProviderSettings, provider)
    if row is not None:
        await db.delete(row)
        await db.commit()


async def list_connections(db: AsyncSession, provider: str) -> list[Connection]:
    query = select(Connection).where(Connection.provider == provider).order_by(Connection.account)
    return list(await db.scalars(query))


async def get_connection(db: AsyncSession, provider: str, connection_id: int) -> Connection | None:
    connection = await db.get(Connection, connection_id)
    return connection if connection and connection.provider == provider else None


async def save_connection(
    db: AsyncSession,
    box: SecretBox,
    provider: str,
    account: str,
    credentials: dict[str, Any],
    scopes: str,
) -> Connection:
    """Create the connection, or update it when the same account is connected again."""
    now = datetime.now(UTC)
    query = select(Connection).where(Connection.provider == provider, Connection.account == account)
    connection = await db.scalar(query)
    if connection is None:
        connection = Connection(provider=provider, account=account, created_at=now)
        db.add(connection)
    connection.credentials = box.encrypt(credentials)
    connection.scopes = scopes
    connection.updated_at = now
    await db.commit()
    return connection


async def save_check(
    db: AsyncSession, connection: Connection, capabilities: list[dict[str, Any]]
) -> None:
    connection.capabilities = capabilities
    connection.checked_at = datetime.now(UTC)
    await db.commit()


async def delete_connection(db: AsyncSession, connection: Connection) -> None:
    await db.delete(connection)
    await db.commit()
