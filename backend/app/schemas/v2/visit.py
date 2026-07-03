"""VisitV2 schemas (Wave 0-C).

設計仕様書 v0.9 §3.3 / §4.5 に基づく訪問インスタンス v2。

v1 → v2 の追加:
  - `course_id` FK NULL (コースに属する訪問)
  - `required_staff_count` (1 or 2)  — §3.3 2 名体制対応
  - `visit_group_id` UUID NULL — 2 名体制の訪問をグルーピング

v1 の `primary_staff_id` / `secondary_staff_id` は v2 でも残す
(visit_staff_assignments テーブルとの併用、移行期間の互換のため)。
2 名体制の表現は **visit_staff_assignments (visit × staff M2M)** で行うのが正規。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 訪問種別 (v1 と同じ):
#   regular (定期) / spot (スポット) / training (研修) など
VisitTypeV2 = Literal["regular", "spot", "training", "other"]

# 訪問状態:
#   planned (予定) / completed (完了) / cancelled (キャンセル) / no_show (不在)
VisitStatusV2 = Literal["planned", "completed", "cancelled", "no_show"]

# 入力チャネル (v1 と同じ):
#   Wave U-1 (§2.2): "manual_week" = 「この週だけ反映」(B) で書いた visit.
#   週生成・固定枠戻の両方で保護される (models/visit.py VISIT_SOURCE_MANUAL_WEEK).
VisitSourceV2 = Literal["manual", "auto", "import", "ai", "manual_week"]


class VisitV2Base(BaseModel):
    """訪問インスタンス v2 (§3.3 / §4.5)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID = Field(description="訪問対象の患者")
    visit_date: date
    start_time: time
    end_time: time
    type: VisitTypeV2 = Field(default="regular")
    status: VisitStatusV2 = Field(default="planned")
    source: VisitSourceV2 = Field(default="manual")
    note: str | None = None
    kaipoke_id: str | None = Field(
        default=None,
        max_length=64,
        description="カイポケ側 ID (連携時)",
    )

    # v2 追加 (§3.3 / §4.5)
    course_id: UUID | None = Field(
        default=None,
        description="所属コース (Layer 2 で決定)",
    )
    required_staff_count: int = Field(
        default=1,
        ge=1,
        le=2,
        description="必要スタッフ数 (§3.3). 1 = 通常, 2 = 2 名体制",
    )
    visit_group_id: UUID | None = Field(
        default=None,
        description=(
            "2 名体制の訪問をグルーピングする論理キー (§3.3). "
            "required_staff_count=2 のとき、同じ visit_group_id を持つ visit が 2 行存在する"
        ),
    )

    # 移行期互換のため v1 の primary/secondary は残す（visit_staff_assignments と併用）
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None


class VisitV2Create(VisitV2Base):
    """POST /api/v1/visits リクエスト (W2-BE4)."""


class VisitV2Update(BaseModel):
    """PATCH /api/v1/visits/{id} リクエスト (W2-BE4)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID | None = None
    visit_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    type: VisitTypeV2 | None = None
    status: VisitStatusV2 | None = None
    source: VisitSourceV2 | None = None
    note: str | None = None
    kaipoke_id: str | None = Field(default=None, max_length=64)
    course_id: UUID | None = None
    required_staff_count: int | None = Field(default=None, ge=1, le=2)
    visit_group_id: UUID | None = None
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None


class VisitStaffAssignmentV2Read(BaseModel):
    """visit_staff_assignments テーブルの 1 行 (§4.5).

    1 visit に対して 1 or 2 行 (`required_staff_count` に応じて)。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    visit_id: UUID
    staff_id: UUID
    assigned_at: datetime


class VisitV2Read(VisitV2Base):
    """GET /api/v1/visits/{id} レスポンス (W2-BE4)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # 表示用の denormalized 名 (v1 と同じ)
    patient_name: str | None = None
    staff_name: str | None = None
    # visit_staff_assignments 経由で割り当てられたスタッフ全員 (§4.5)
    staff_assignments: list[VisitStaffAssignmentV2Read] = Field(default_factory=list)
