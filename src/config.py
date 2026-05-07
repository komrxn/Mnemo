from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram
    telegram_bot_token: str
    allowed_user_ids: list[int]

    # OpenAI
    openai_api_key: str
    openai_model_main: str = "gpt-5.4"
    openai_model_fast: str = "gpt-5.4-mini"
    openai_model_vision: str = "gpt-5.4"
    openai_embed_model: str = "text-embedding-3-large"
    openai_whisper_model: str = "whisper-1"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Vault
    vault_path: str = "/data/vault"
    vault_git_remote: str = ""
    vault_git_ssh_key: str = "/run/secrets/vault_ssh_key"
    vault_git_user_name: str = "mnemo-bot"
    vault_git_user_email: str = "mnemo-bot@localhost"

    # LightRAG
    lightrag_storage: str = "/data/lightrag"

    # Misc
    tz: str = "UTC"
    log_level: str = "INFO"
    sentry_dsn: str = ""
    session_idle_timeout_min: int = 15

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, v: object) -> list[int]:
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        raise ValueError(f"cannot parse allowed_user_ids from {v!r}")


settings = Settings()
