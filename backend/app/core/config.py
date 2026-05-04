"""Application settings loaded from environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    All values can be overridden via environment variables (case-insensitive)
    or a `.env` file at backend root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: str = Field(default="development")
    app_name: str = Field(default="CareLink Backend")
    log_level: str = Field(default="INFO")
    api_v1_prefix: str = Field(default="/api/v1")
    cors_origins: str = Field(default="http://localhost:3000")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://carelink:carelink@localhost:5432/carelink"
    )
    database_echo: bool = Field(default=False)

    # --- JWT ---
    jwt_secret: str = Field(default="dev-only-secret-change-me-please-32chars")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_ttl_seconds: int = Field(default=3600)
    jwt_refresh_ttl_seconds: int = Field(default=60 * 60 * 24 * 30)

    # --- External APIs ---
    google_maps_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")

    # --- Integrations forward target ---
    integration_base_url: str = Field(default="http://integrations:8001")

    @field_validator("cors_origins")
    @classmethod
    def _normalize_origins(cls, value: str) -> str:
        # Allow comma-separated string; we expose helper below.
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed list of CORS origins (commas, whitespace tolerant)."""
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (call this everywhere instead of constructing Settings)."""
    return Settings()
