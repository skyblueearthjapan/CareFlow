"""Pydantic schemas for patient_fixed_visits (W9-BE1 / W37 Phase 1).

固定訪問パターン の入力・出力型定義。
フロントエンド zod schema と完全一致を目指す。

W37 Phase 1: 複数スタッフ対応患者は同曜日・同時刻に 2 コース固定するため
slot_index (0/1) を導入。1 名体制では default 0 のみ使う。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PatientFixedVisitMode = Literal["normal", "special"]

# P2-A: 可動域フラグ = 提案の可否 (改善提案 MVP).
# unknown(既定・保守的) / time_flexible(同曜日内の時刻変更可) /
# day_flexible(曜日も変更可) / locked(完全固定).
PatientFixedVisitMovability = Literal[
    "unknown", "time_flexible", "day_flexible", "locked"
]


class PatientFixedVisitV2Base(BaseModel):
    """PUT body / 個別訪問アイテムの共通フィールド."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="0=月 … 6=日")
    start_time: time = Field(description="訪問開始時刻 HH:MM")
    duration_min: int = Field(ge=1, le=480, default=30, description="訪問時間 (分)")
    # W22 Phase A: 固定枠が属するコーステンプレート ID (省略可).
    course_template_id: UUID | None = Field(
        default=None,
        description=(
            "固定枠が属する course_templates.id (W22). "
            "未指定なら Layer 1 の office フォールバックで解決される。"
        ),
    )
    # Phase E-5 (項目 ⑥B): サブ拠点 ID (省略可).
    # 主担当 (patient.primary_office_id) とは別にフォロー時に PFV を配置できる拠点.
    # 指定時は course_template_id も sub_office の template を指す必要がある (API で検証).
    sub_office_id: UUID | None = Field(
        default=None,
        description=(
            "サブ拠点 ID (Phase E-5 ⑥B). 未指定なら従来どおり主担当拠点のみで動作. "
            "指定時は course_template_id も sub_office の template を指すこと."
        ),
    )
    # W37 Phase 1: 同曜日・同時刻に 2 コース固定するためのスロット番号.
    # 1 名体制 (既存) では 0 のみ. 複数スタッフ対応では 0/1 の 2 行を持つ.
    slot_index: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "同 patient × 同 mode × 同 weekday の中のスロット番号 (0 or 1). "
            "1 名体制では 0 のみ. 複数スタッフ対応 (W37) で 0/1 の 2 行を持つ."
        ),
    )
    # Phase G-21: 完全固定フラグ. true = 自動算出で絶対動かさない.
    # 既存全 PFV は migration で true backfill 済 (後方互換).
    # 新規 PFV は default false.
    is_pinned: bool = Field(
        default=False,
        description=(
            "Phase G-21: 完全固定フラグ. true = 自動算出で絶対動かさない. "
            "既存全 PFV は migration で true backfill 済 (後方互換)."
        ),
    )
    # P2-A: 可動域フラグ = 提案の可否 (改善提案 MVP).
    # 送らない旧 FE リクエストは default 'unknown' で保存され従来と同一動作 (既定挙動不変).
    # is_pinned=True かつ movability != 'locked' は pfv_validator V6 が 'locked' に矯正する.
    movability: PatientFixedVisitMovability = Field(
        default="unknown",
        description=(
            "P2-A: 可動域フラグ = 提案の可否. "
            "unknown(既定) / time_flexible / day_flexible / locked. "
            "未指定は 'unknown' で保存 (従来挙動)."
        ),
    )


class PatientFixedVisitV2Read(PatientFixedVisitV2Base):
    """GET レスポンスの単一行."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    patient_id: UUID
    mode: PatientFixedVisitMode
    created_at: datetime
    updated_at: datetime


class PatientFixedVisitsBulkPut(BaseModel):
    """PUT /patients/{id}/fixed-visits の一括上書きリクエスト body.

    items 内で (weekday, slot_index) が重複している場合は 422 を返す。
    W37 Phase 1: 1 名体制では従来どおり weekday ごとに 1 件のみ (slot_index=0).
                 複数スタッフ対応では同 weekday に slot_index 0/1 の 2 件まで許容.
    """

    model_config = ConfigDict(extra="forbid")

    mode: PatientFixedVisitMode
    items: list[PatientFixedVisitV2Base] = Field(
        default_factory=list,
        # W37 Phase 1: 7 曜日 × slot 0/1 = 最大 14 件.
        max_length=14,
        description=("0〜14 件 (7 曜日 × slot 0/1)。(weekday, slot_index) 重複は不可。"),
    )

    @model_validator(mode="after")
    def _no_duplicate_weekday_slot(self) -> PatientFixedVisitsBulkPut:
        keys = [(item.weekday, item.slot_index) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("items に (weekday, slot_index) の重複があります")
        return self


# ---------------------------------------------------------------------------
# P0-2: PUT レスポンスのエンベロープ化 (再検証警告の同梱)
# ---------------------------------------------------------------------------


class PfvValidationWarningOut(BaseModel):
    """PUT /fixed-visits の再検証で検出した 1 件の警告.

    ``severity="warning"`` のみをレスポンスに載せる (error は 422 で返す).
    code は pfv_validator の 4 種 (patient_time_conflict / lunch_break_overlap /
    course_capacity_exceeded / pinned_protection).
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="警告種別コード (pfv_validator 準拠).")
    message: str = Field(description="現場向け日本語メッセージ.")
    weekday: int = Field(ge=0, le=6, description="0=月 … 6=日.")
    severity: str = Field(description='"warning" (続行可) / "error" (422 対象).')


class PatientFixedVisitsBulkPutResponse(BaseModel):
    """PUT /patients/{id}/fixed-visits のエンベロープレスポンス (P0-2).

    ``items`` は従来どおり確定後の全 PFV 行. ``warnings`` は再検証で検出した
    非致命の指摘 (患者間衝突 / 昼休み / 容量). FE 未パースのため BE 先行デプロイ安全.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[PatientFixedVisitV2Read]
    warnings: list[PfvValidationWarningOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase G-21 T2: is_pinned 単独切替用 schema
# ---------------------------------------------------------------------------


class PatientFixedVisitPinUpdate(BaseModel):
    """PATCH /patients/.../fixed-visits/{pfv_id}/pin の body.

    既存 PFV 行の ``is_pinned`` のみ単独切替する用途.
    """

    model_config = ConfigDict(extra="forbid")

    is_pinned: bool = Field(description="Phase G-21: true = 自動算出で絶対動かさない完全固定.")


class PatientFixedVisitPinBulkItem(BaseModel):
    """POST /patients/fixed-visits/pin/bulk の items 1 件.

    複数 PFV の is_pinned を一括切替する用途.
    """

    model_config = ConfigDict(extra="forbid")

    pfv_id: UUID = Field(description="対象 patient_fixed_visits.id")
    is_pinned: bool = Field(description="Phase G-21: true = 自動算出で絶対動かさない完全固定.")
