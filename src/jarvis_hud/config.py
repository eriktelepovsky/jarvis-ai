from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Jarvis configuration, read from environment variables prefixed with JARVIS_."""

    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'jarvis.db'}"
