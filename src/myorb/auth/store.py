import asyncio
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.auth.models import AuthSettings, Passkey
from myorb.auth.passwords import hash_password, verify_password
from myorb.auth.state import AuthState

SETTINGS_ID = 1


async def load_auth_state(db: AsyncSession) -> AuthState:
    state = AuthState()
    row = await db.get(AuthSettings, SETTINGS_ID)
    if row is not None:
        state.password_hash = row.password_hash
        state.session_key = row.session_key
    return state


async def check_password(state: AuthState, password: str) -> bool:
    if state.password_hash is None:
        return False
    # scrypt takes tens of milliseconds; keep it off the event loop.
    return await asyncio.to_thread(verify_password, password, state.password_hash)


async def set_password(db: AsyncSession, state: AuthState, password: str) -> None:
    """Store a new password and rotate the session key, signing out all devices."""
    password_hash = await asyncio.to_thread(hash_password, password)
    session_key = secrets.token_bytes(32)
    row = await db.get(AuthSettings, SETTINGS_ID)
    if row is None:
        row = AuthSettings(id=SETTINGS_ID)
        db.add(row)
    row.password_hash = password_hash
    row.session_key = session_key
    row.password_changed_at = datetime.now(UTC)
    await db.commit()
    state.password_hash = password_hash
    state.session_key = session_key


async def list_passkeys(db: AsyncSession) -> list[Passkey]:
    return list(await db.scalars(select(Passkey).order_by(Passkey.created_at)))


async def find_passkey(db: AsyncSession, credential_id: bytes) -> Passkey | None:
    return await db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))


async def add_passkey(
    db: AsyncSession, name: str, credential_id: bytes, public_key: bytes, sign_count: int
) -> None:
    db.add(
        Passkey(
            name=name,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=sign_count,
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def record_passkey_use(db: AsyncSession, passkey: Passkey, sign_count: int) -> None:
    passkey.sign_count = sign_count
    passkey.last_used_at = datetime.now(UTC)
    await db.commit()


async def delete_passkey(db: AsyncSession, passkey_id: int) -> None:
    passkey = await db.get(Passkey, passkey_id)
    if passkey is not None:
        await db.delete(passkey)
        await db.commit()
