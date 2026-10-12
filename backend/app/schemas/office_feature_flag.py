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

# ``feature_key`` は Literal で固定することで タイポ (= 永久に有効化されない
# feature_key) を schema レベルで弾く. 新しい key を追加する際は本 Literal を拡張する.
#   - ``g21_new_algorithm``: Phase G-21 auto_allocator_v2 新ロジックの canary 切替.
#   - ``l3_fix_primary_staff``: Wave N-1 — Layer 3 で当該拠点の primary staff
#     (active role=staff の code 昇順先頭 1 名) を A コースに固定割当する
#     (旧「都賀」拠点名ハードコードの設定化. 拠点名判定を廃し、フラグ enabled で発動).
#   - ``rakusuke_agent``: らく助エージェント（右下の小窓・相談タブ）を出す拠点.
#   - ``template_workspace``: コースの作業場（/schedule/template）を出す拠点.
#     ※ 2026-10-13: この 2 つを Literal に入れ忘れ、一覧 GET が 500 になって小窓が出なかった
#   - ``karte_via_seat``: 訪問の音声記録のカルテ下書きを、決めた管理者のサインインした AI の席で作る拠点
#     (失敗したら今の Gemini の要約に戻る。docs/plans/karte-via-seat-2026-10-13.md)。
FeatureKey = Literal[
    "g21_new_algorithm",
    "l3_fix_primary_staff",
    "rakusuke_agent",
    "template_workspace",
    "karte_via_seat",
]


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
