import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY_FILE = "secret.key"

__all__ = ["InvalidToken", "SecretBox", "load_secret_key"]


def load_secret_key(secret_key: str | None, data_dir: Path) -> bytes:
    """Return the configured secret key, or a random key generated once in the data directory."""
    if secret_key:
        return secret_key.encode()
    path = data_dir / SECRET_KEY_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "w") as file:
        file.write(secrets.token_hex(32))
    return path.read_bytes().strip()


class SecretBox:
    """Authenticated encryption of JSON values stored in the database (Fernet)."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))

    def encrypt(self, data: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(data).encode())

    def decrypt(self, token: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(token))
