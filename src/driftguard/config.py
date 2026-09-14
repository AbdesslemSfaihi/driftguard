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
    # Measured on the benchmark: Sonnet 5 asks on every retrieved ambiguity at
    # 100% judgment precision; Haiku 4.5 matches it on plain judgment but is
    # inconsistent on asking (see benchmark/reports/ask-*.json).
    judge_model: str = "claude-sonnet-5"
    max_fix_attempts: int = 2  # fixer retries per finding before it is dropped
    open_docs_prs: bool = True  # False = store verified proposals, never push/PR

    # Ask-the-developer flow (human in the loop)
    ask_developer: bool = True  # False = store questions, never comment on the PR
    max_questions_per_run: int = 3  # politeness cap: extra questions are dropped, logged

    # Notifications: one JSON event per finished run, POSTed to a webhook
    # (n8n in this project — docs/n8n-notifications.md). Empty URL = off.
    notify_webhook_url: str = ""
    notify_webhook_token: str = ""  # sent as X-DriftGuard-Token; n8n checks it (Header Auth)
    public_base_url: str = "http://localhost:8000"  # base for story-page links in events


@lru_cache
def get_settings() -> Settings:
    return Settings()
