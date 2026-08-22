"""Application settings loaded from environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, ValidationInfo, field_validator
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

    # --- Integrations forward target ---
    integration_base_url: str = Field(default="http://integrations:8001")

    # --- Kaipoke API (Wave 4-A) ---
    kaipoke_api_base_url: str = Field(default="https://kaipoke-api.net")
    kaipoke_api_token: str = Field(default="")
    # カイポケ ログイン情報の暗号化鍵 (C-1)。未設定時は jwt_secret から導出する
    # (services/kaipoke/credentials.py)。鍵を変えると保存済みパスワードは復号
    # 不能になり UI で再設定が必要。
    kaipoke_cred_secret: str | None = Field(default=None)
    kaipoke_export_dir: str = Field(default="/tmp/carelink/exports")
    kaipoke_export_ttl_seconds: int = Field(default=1800)  # 30 minutes
    # Live noVNC monitor URL. NOTE: this is the *websockify* host (port 6080),
    # NOT the Flask host — kaipoke-api's own /api/kaipoke/vnc-url returns a
    # kaipoke-api.net/novnc/... URL that hits Flask (port 5000) and does not
    # serve noVNC. Both hosts sit behind Cloudflare Access (manager OTP).
    kaipoke_novnc_url: str = Field(default="https://novnc.kaipoke-api.net/vnc.html")
    # RPA (auto_apply) がサービス内容の分岐に対応しているか
    # (docs/plans/kaipoke-service-content-design.md §3 / Phase S3)。
    #
    # False (既定) = RPA は「精神基本療養費Ⅰ・正看」しか登録できない。この間、
    # 准看 / 一般の add は **送信対象から除外**する (送ると RPA が既定の
    # 精神科・看護師等で登録してしまい、カイポケ側が黙って間違った値になる)。
    # S3 (option 文言の採取 → 分岐実装 → 実機 1 件テスト) が終わったら True。
    kaipoke_rpa_service_branch_enabled: bool = Field(default=False)

    @field_validator("cors_origins")
    @classmethod
    def _normalize_origins(cls, value: str) -> str:
        # Allow comma-separated string; we expose helper below.
        return value.strip()

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, value: str, info: ValidationInfo) -> str:
        """Reject short JWT secrets in production.

        In dev/test we keep the lax default to avoid friction; production must
        provide >=32 char secrets to keep HS256 signatures meaningful.
        """
        app_env = (info.data.get("app_env") or "").lower()
        if app_env in {"prod", "production"} and len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters in production")
        return value

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
