from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Jarvis configuration, read from environment variables prefixed with JARVIS_."""

    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    # Public address of Jarvis, e.g. https://jarvis.example.com. Used for the CSRF origin check
    # and passkeys; when unset, the address of each request is used.
    public_url: str | None = None
    # Reverse proxies whose X-Forwarded-For and X-Forwarded-Proto headers are trusted:
    # comma-separated IP addresses or networks, or * to trust any client.
    trusted_proxies: str = "127.0.0.1"
    # Key that encrypts stored credentials (OAuth tokens, API keys). When unset, a random key is
    # generated in data_dir/secret.key on first start.
    secret_key: str | None = None
    # The user's time zone, e.g. Europe/Bratislava. Defaults to the system time zone.
    timezone: str | None = None

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'jarvis.db'}"
