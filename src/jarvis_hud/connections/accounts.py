from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_hud.connections import store
from jarvis_hud.connections.providers import ConnectedAccount
from jarvis_hud.crypto import SecretBox


class Connections:
    """Connected accounts of form-based providers, with their decrypted credentials."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    async def list(self, provider: str) -> list[ConnectedAccount]:
        async with self._sessionmaker() as db:
            rows = await store.list_connections(db, provider)
        return [ConnectedAccount(r.id, r.account, self._box.decrypt(r.credentials)) for r in rows]
