"""Pydantic schemas for patient_fixed_visits (W9-BE1).

週間訪問パターン (固定枠) の入力・出力型定義。
フロントエンド zod schema と完全一致を目指す。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PatientFixedVisitMode = Literal["normal", "special"]


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

    items 内で weekday が重複している場合は 422 を返す。
    """

    model_config = ConfigDict(extra="forbid")

    mode: PatientFixedVisitMode
    items: list[PatientFixedVisitV2Base] = Field(
        default_factory=list,
        max_length=7,
        description="0〜7 件 (7 曜日分)。weekday 重複は不可。",
    )

    @model_validator(mode="after")
    def _no_duplicate_weekdays(self) -> PatientFixedVisitsBulkPut:
        weekdays = [item.weekday for item in self.items]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("items に weekday の重複があります")
        return self
