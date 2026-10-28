from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """myOrb configuration, read from environment variables prefixed with MYORB_."""

    model_config = SettingsConfigDict(env_prefix="MYORB_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    # Public address of myOrb, e.g. https://myorb.example.com. Used for the CSRF origin check
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
    # Custom plugins to install at startup, as for pip: names, versions, URLs or local paths.
    plugins: str | None = None
    # Demo mode: a made-up day instead of the services, with the brief and Ask's answer written
    # in advance, so nothing is sent anywhere (see demo.py). Use a data_dir of its own.
    demo: bool = False
    # The demo on a public address: open to everyone without signing in, with nothing to change
    # but Ask, and the made-up day back each morning. Turns demo on too.
    public_demo: bool = False

    @model_validator(mode="after")
    def _public_demo_is_a_demo(self) -> "Settings":
        if self.public_demo:
            self.demo = True
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'myorb.db'}"


def move_legacy_database(data_dir: Path) -> None:
    old, new = data_dir / "jarvis.db", data_dir / "myorb.db"
    if old.exists() and not new.exists():
        old.rename(new)
