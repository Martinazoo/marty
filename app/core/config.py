"""Application settings, loaded from environment variables and the `.env` file."""

from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

AIProviderName = Literal["gemini", "groq", "ollama"]


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    timezone: str = "Europe/Madrid"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "marty"
    postgres_user: str = "marty"
    postgres_password: SecretStr = SecretStr("")

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = []

    ai_provider_order: Annotated[list[AIProviderName], NoDecode] = ["gemini", "groq"]
    gemini_api_key: SecretStr | None = None
    # Comma-separated; each model is a separate entry in the fallback chain.
    gemini_model: Annotated[list[str], NoDecode] = []
    groq_api_key: SecretStr | None = None
    groq_model: Annotated[list[str], NoDecode] = []
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: Annotated[list[str], NoDecode] = []
    llm_timeout_seconds: float = 60.0
    # Reasoning models spend part of this budget thinking before they answer.
    llm_max_output_tokens: int = 4096
    llm_max_tool_iterations: int = 4

    conversation_idle_hours: float = 6.0
    pending_action_ttl_hours: float = 24.0

    @field_validator(
        "telegram_allowed_user_ids",
        "ai_provider_order",
        "gemini_model",
        "groq_model",
        "ollama_model",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator("telegram_bot_token", "gemini_api_key", "groq_api_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _validate_telegram(self) -> "Settings":
        if self.telegram_enabled:
            if self.telegram_bot_token is None:
                raise ValueError("TELEGRAM_ENABLED=true requires TELEGRAM_BOT_TOKEN")
            if not self.telegram_allowed_user_ids:
                raise ValueError("TELEGRAM_ENABLED=true requires TELEGRAM_ALLOWED_USER_IDS")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value() or None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def secret_values(self) -> list[str]:
        """Plain-text values of every secret setting, used for log redaction."""
        values = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr) and value.get_secret_value():
                values.append(value.get_secret_value())
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()
