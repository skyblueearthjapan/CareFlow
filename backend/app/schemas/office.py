"""Office schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Phase G-45: 拠点稼働曜日 (0=月..6=日). default [0..5] (月-土).
DEFAULT_OPERATING_WEEKDAYS: list[int] = [0, 1, 2, 3, 4, 5]


def _validate_operating_weekdays(value: list[int]) -> list[int]:
    """0..6 の重複なし int 配列か検証 (= 最低 1 個).

    - 全要素 0 <= x <= 6 の整数
    - 重複禁止
    - 空禁止 (= 1 個以上)
    """
    if not isinstance(value, list):
        raise ValueError("operating_weekdays は配列で指定してください")
    if len(value) < 1:
        raise ValueError("operating_weekdays は最低 1 日選択してください")
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("operating_weekdays は 0..6 の整数のみ指定可能です")
        if raw < 0 or raw > 6:
            raise ValueError("operating_weekdays は 0..6 の範囲で指定してください")
        if raw in seen:
            raise ValueError("operating_weekdays に重複する曜日が含まれています")
        seen.add(raw)
        normalized.append(raw)
    return normalized


class OfficeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str | None = None
    prefecture: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None
    # 0059: 拠点マスタ駆動化. sort_order=表示順 (NULL は名前順末尾),
    # short_label=短縮バッジ (NULL は name 先頭 1 文字).
    sort_order: int | None = None
    short_label: str | None = Field(default=None, max_length=8)
    # Phase G-45: 稼働曜日 (0=月..6=日 の int 配列).
    operating_weekdays: list[int] = Field(default_factory=lambda: list(DEFAULT_OPERATING_WEEKDAYS))

    @field_validator("operating_weekdays")
    @classmethod
    def _check_operating_weekdays(cls, value: list[int]) -> list[int]:
        return _validate_operating_weekdays(value)


class OfficeCreate(OfficeBase):
    allowed_cities: list[UUID] = []


class OfficeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    code: str | None = None
    prefecture: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None
    # 0059: 拠点マスタ駆動化 (PATCH では未指定で「触らない」)。
    # nullable 列なので明示的な null 送信でクリア可能 (= 既定=名前順/先頭1文字へ戻す)。
    # operating_weekdays (NOT NULL 列) と違い None ガードは意図的に置かない。
    sort_order: int | None = None
    short_label: str | None = Field(default=None, max_length=8)
    # Phase G-45: 稼働曜日 — PATCH では未指定 (None) で「触らない」.
    operating_weekdays: list[int] | None = None
    allowed_cities: list[UUID] | None = None

    @field_validator("operating_weekdays")
    @classmethod
    def _check_operating_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return _validate_operating_weekdays(value)


class OfficeRead(OfficeBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    allowed_cities: list[UUID] = []
