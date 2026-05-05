"""Visit schemas — Phase 2 CRUD payloads (W2-BE4 拡張済み).

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.3 / §4.5):
  - ``course_id`` (FK NULL) — Layer 2 で決定するコース所属
  - ``required_staff_count`` (1 or 2) — 2 名体制対応
  - ``visit_group_id`` (UUID NULL) — 2 名体制の訪問グルーピング

CRUD レスポンスは ``visit_staff_assignments`` 経由で割り当てられたスタッフ全員
(``staff_assignments``) を含める。1 visit あたり 1 or 2 行
(``required_staff_count`` に応じる)。

正本の v2 schema (``extra='forbid'``) は ``app.schemas.v2.visit`` にある。
本ファイルは router (旧クライアント互換のため ``extra='ignore'``) 用の薄い拡張。
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.visit import VisitStaffAssignmentV2Read

# v2 visit_staff_assignments の Read 型 (re-export)
VisitStaffAssignmentRead = VisitStaffAssignmentV2Read


class VisitBase(BaseModel):
    """Visit の共通フィールド (v1 互換 + v2 追加)."""

    model_config = ConfigDict(extra="ignore")

    patient_id: UUID
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date
    start_time: time
    end_time: time
    type: str
    status: str = "planned"
    source: str = "manual"
    note: str | None = None
    kaipoke_id: str | None = None

    # ---- W2-BE4 v2 additions (§3.3 / §4.5) ----------------------------------
    course_id: UUID | None = Field(
        default=None,
        description="所属コース (Layer 2 で決定; CRUD では NULL のまま運用しても可)",
    )
    required_staff_count: int = Field(
        default=1,
        ge=1,
        le=2,
        description="必要スタッフ数. 1 = 通常 / 2 = 2 名体制 (§3.3)",
    )
    visit_group_id: UUID | None = Field(
        default=None,
        description=(
            "2 名体制の訪問グルーピングキー (§3.3). "
            "通常 (required_staff_count=1) は NULL, "
            "2 名体制 (required_staff_count=2) では同じ UUID を持つ visit が 2 行存在する"
        ),
    )


class VisitCreate(VisitBase):
    """POST /api/v1/visits リクエスト."""


class VisitUpdate(BaseModel):
    """PATCH /api/v1/visits/{id} リクエスト."""

    model_config = ConfigDict(extra="ignore")

    patient_id: UUID | None = None
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    type: str | None = None
    status: str | None = None
    source: str | None = None
    note: str | None = None
    kaipoke_id: str | None = None

    # W2-BE4 v2 additions
    course_id: UUID | None = None
    required_staff_count: int | None = Field(default=None, ge=1, le=2)
    visit_group_id: UUID | None = None


class VisitRead(VisitBase):
    """GET /api/v1/visits/{id} レスポンス (v2 拡張済み)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # Denormalized display names — populated by selectinload() in the router.
    # Frontend (`schedule/page.tsx`) renders these directly without a join.
    patient_name: str | None = None
    staff_name: str | None = None
    # visit_staff_assignments 経由の割当スタッフ一覧 (§4.5)
    # 1 visit あたり 1 or 2 行 (required_staff_count による)
    staff_assignments: list[VisitStaffAssignmentRead] = Field(default_factory=list)


__all__ = [
    "VisitBase",
    "VisitCreate",
    "VisitRead",
    "VisitStaffAssignmentRead",
    "VisitUpdate",
]
