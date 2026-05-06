"""StaffCompanionAssignment v2 schemas (W10-BE1).

同行スタッフ割当 API のリクエスト / レスポンス型定義。

曜日 (weekday: 0=Mon ... 6=Sun) × 午前/午後/終日 (part: am/pm/full) の
単位で新人スタッフ (trainee) への同行スタッフ (companion) を管理する。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 同行パート
StaffCompanionPart = Literal["am", "pm", "full"]


class StaffCompanionAssignmentV2Base(BaseModel):
    """同行スタッフ割当 基底スキーマ."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="曜日 (0=月 ... 6=日)")
    part: StaffCompanionPart = Field(description="午前 (am) / 午後 (pm) / 終日 (full)")
    companion_staff_id: UUID = Field(description="同行スタッフの staff.id")


class StaffCompanionAssignmentV2Read(StaffCompanionAssignmentV2Base):
    """GET レスポンス用スキーマ."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    trainee_staff_id: UUID
    created_at: datetime
    updated_at: datetime


class StaffCompanionAssignmentsBulkPut(BaseModel):
    """PUT /staff/{id}/companion-assignments リクエスト.

    1 TX で当該 trainee_staff_id の全既存削除 → items を INSERT。

    バリデーション (API 層で実施):
      - 同一 (weekday, part) 重複 → 422
      - companion_staff_id が trainee 自身 → 422
      - items は 0..14 件 (7 曜日 × 3 part)
    """

    model_config = ConfigDict(extra="forbid")

    items: list[StaffCompanionAssignmentV2Base] = Field(
        default_factory=list,
        description="同行スタッフ割当リスト (0..14 件)",
    )
