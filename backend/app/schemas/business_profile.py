"""Pydantic schemas for business_profile (事業所の情報・mig 0089).

患者 QR カードに載せる事業所名・電話・対応時間・対応日・ロゴ。

* GET: 保存値 (未設定の項目は null)。行が無ければ全項目 null。
* PUT: 部分更新 (省略 = 変更しない / 明示 null・空文字 = 未設定に戻す)。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ロゴに使えるのは、アプリに同梱した画像のパス (英数字と . _ ~ / - だけ・「//」始まり不可) か
# https の URL (空白・バックスラッシュ・制御文字は不可)。FE の isAllowedLogoUrl と同じ規則。
_LOGO_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9._~/-]+$")
_LOGO_HTTPS_RE = re.compile(r"^https://[^\s\\/][^\s\\]*$")


def is_allowed_logo_url(value: str) -> bool:
    """ロゴのパス / URL として受け付けるか (制御文字・バックスラッシュ・「//」始まりは不可)."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return False
    return bool(_LOGO_PATH_RE.match(value) or _LOGO_HTTPS_RE.match(value))


PROFILE_FIELDS: tuple[str, ...] = (
    "station_name",
    "contact_tel",
    "contact_hours",
    "contact_days",
    "logo_url",
)


class BusinessProfileRead(BaseModel):
    """事業所の情報 (未設定の項目は null)。"""

    model_config = ConfigDict(extra="forbid")

    station_name: str | None = None
    contact_tel: str | None = None
    contact_hours: str | None = None
    contact_days: str | None = None
    logo_url: str | None = None


class BusinessProfileUpdate(BaseModel):
    """PUT リクエスト (部分更新)。空文字は null (未設定) として保存する。"""

    model_config = ConfigDict(extra="forbid")

    station_name: str | None = Field(default=None, max_length=120)
    contact_tel: str | None = Field(default=None, max_length=40)
    contact_hours: str | None = Field(default=None, max_length=60)
    contact_days: str | None = Field(default=None, max_length=120)
    logo_url: str | None = Field(default=None, max_length=255)

    @field_validator(*PROFILE_FIELDS)
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("logo_url")
    @classmethod
    def _check_logo_url(cls, value: str | None) -> str | None:
        """ロゴはアプリに同梱した画像のパス (``/...``) か ``https://`` の URL だけ受け付ける."""
        if value is None:
            return None
        if not is_allowed_logo_url(value):
            raise ValueError(
                "ロゴは「/」から始まるパス (英数字と . _ ~ / - だけ) か"
                "「https://」から始まる URL で指定してください"
            )
        return value
