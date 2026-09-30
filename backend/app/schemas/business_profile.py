"""Pydantic schemas for business_profile (事業所の情報・mig 0089).

患者 QR カードに載せる事業所名・電話・対応時間・対応日・ロゴ。

* GET: 保存値 (未設定の項目は null)。行が無ければ全項目 null。
* PUT: 部分更新 (省略 = 変更しない / 明示 null・空文字 = 未設定に戻す)。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        if value.startswith("//") or not (value.startswith("/") or value.startswith("https://")):
            raise ValueError(
                "ロゴは「/」から始まるパスか「https://」から始まる URL で指定してください"
            )
        return value
