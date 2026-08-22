from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables and the local .env file.

    Every value has a safe development default so a fresh clone runs immediately;
    production overrides everything through real environment variables (Render).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    github_webhook_secret: str = "dev-only-secret"
    database_path: Path = Path("data/driftguard.db")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
