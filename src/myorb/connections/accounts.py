from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.connections import store
from myorb.connections.models import Connection
from myorb.connections.providers import ConnectedAccount
from myorb.crypto import SecretBox


class Connections:
    """Connected accounts of form-based providers, with their decrypted credentials."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    async def settings(self, provider: str) -> dict | None:
        """A provider's stored settings, e.g. the user's own OAuth application."""
        async with self._sessionmaker() as db:
            return await store.get_provider_settings(db, self._box, provider)

    async def update_credentials(self, connection_id: int, credentials: dict) -> None:
        """Store new credentials, e.g. a rotated OAuth refresh token."""
        async with self._sessionmaker() as db:
            connection = await db.get(Connection, connection_id)
            if connection is not None:
                connection.credentials = self._box.encrypt(credentials)
                await db.commit()

    async def list(self, provider: str) -> list[ConnectedAccount]:
        async with self._sessionmaker() as db:
            rows = await store.list_connections(db, provider)
        return [ConnectedAccount(r.id, r.account, self._box.decrypt(r.credentials)) for r in rows]
