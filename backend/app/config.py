from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {"change-me-to-a-long-random-string", "dev-insecure-change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    provider_mode: Literal["mock", "live"] = "mock"
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+asyncpg://mockmate:mockmate@localhost:5432/mockmate"
    )

    secret_key: SecretStr = SecretStr("dev-insecure-change-me")
    session_cookie_name: str = "mockmate_session"
    session_ttl_hours: int = 168

    apinex_base_url: str = "https://api.apinex.bond/v1"
    apinex_model: str = ""
    apinex_api_keys: str = ""   # comma-separated
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_api_keys: str = ""   # comma-separated

    max_upload_mb: int = 5
    max_pdf_pages: int = 15
    min_resume_chars: int = 200

    @model_validator(mode="after")
    def _validate_for_prod(self) -> "Settings":
        if self.app_env == "prod":
            if self.provider_mode == "mock":
                raise ValueError("PROVIDER_MODE=mock is not allowed when APP_ENV=prod")
            key = self.secret_key.get_secret_value()
            if key in _INSECURE_DEFAULTS or len(key) < 32:
                raise ValueError("SECRET_KEY must be set to 32+ random chars in prod")
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.app_env == "prod"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def secret_values(self) -> list[str]:
        """Every secret the logger must never print."""
        values = [self.secret_key.get_secret_value()]
        for csv in (self.sarvam_api_keys, self.apinex_api_keys):
            values += [k.strip() for k in csv.split(",") if k.strip()]
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()