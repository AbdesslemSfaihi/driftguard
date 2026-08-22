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
    github_token: str = ""  # fine-grained PAT; empty = archive deliveries, skip analysis
    database_path: Path = Path("data/driftguard.db")
    log_level: str = "INFO"

    # Documentation index
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    index_dir: Path = Path("data/index")

    # Agent (judgment + writing)
    anthropic_api_key: str = ""  # empty = stop after fact extraction
    judge_model: str = "claude-opus-5"
    max_fix_attempts: int = 2  # fixer retries per finding before it is dropped
    open_docs_prs: bool = True  # False = store verified proposals, never push/PR


@lru_cache
def get_settings() -> Settings:
    return Settings()
