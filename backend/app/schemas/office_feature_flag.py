"""Pydantic schemas for office_feature_flags (Phase G-21).

拠点単位の feature flag (canary 3 phase 用).

* (office_id, feature_key) UNIQUE.
* ``enabled_at`` が NULL の場合は "未有効化" として扱う.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ``feature_key`` は当面 ``g21_new_algorithm`` のみ. Literal で固定することで
# タイポ (= 永久に有効化されない feature_key) を schema レベルで弾く. 新しい
# key を追加する際は本 Literal を拡張する.
FeatureKey = Literal["g21_new_algorithm"]


class OfficeFeatureFlagBase(BaseModel):
    """共通フィールド."""

    model_config = ConfigDict(extra="forbid")

    feature_key: FeatureKey = Field(description="例: 'g21_new_algorithm'")
    enabled_at: datetime | None = None
    note: str | None = None


class OfficeFeatureFlagCreate(OfficeFeatureFlagBase):
    """新規作成リクエスト body."""

    office_id: UUID


class OfficeFeatureFlagRead(OfficeFeatureFlagBase):
    """GET レスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    office_id: UUID
    enabled_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Phase G-21 T2: UPSERT 用 set request schema
# ---------------------------------------------------------------------------


class OfficeFeatureFlagSet(BaseModel):
    """POST /office-feature-flags の UPSERT body.

    既存 (office_id, feature_key) があれば上書き, 無ければ新規作成.
    ``enabled=true`` で ``enabled_at=NOW()``, ``enabled=false`` で ``enabled_at=NULL``.
    """

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    feature_key: FeatureKey = Field(description="例: 'g21_new_algorithm'")
    enabled: bool = Field(
        description="true=有効化 (enabled_at=NOW), false=無効化 (enabled_at=NULL)"
    )
    note: str | None = None
