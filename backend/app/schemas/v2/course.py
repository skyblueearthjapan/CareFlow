"""CourseV2 schemas (Wave 0-C).

設計仕様書 v0.9 §4.5 に基づくコース (Course) マスタ。
Layer 2 (§3.6.3 / §5.3) で生成される患者グループ。

識別子:
  (iso_year, iso_week, weekday, code) UNIQUE

状態遷移 (`course_status` enum):
  proposed → course_fixed → staff_assigned

タイムスタンプ補助カラム:
  course_fixed_at, staff_assigned_at
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.enums import CourseStatus

# コース記号 (§3.6.5):
#   A〜D = 通常コース (4 つ、稼働スタッフ数に応じる)
#   M = マネージャー枠 (4 コース外のオーバーフロー専用)
CourseCodeV2 = Literal["A", "B", "C", "D", "M"]


class CourseV2Base(BaseModel):
    """コース v2 (§4.5).

    対象期間: 1 週間 × 1 曜日。1 コースあたり最大 6 名の患者。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100, description="ISO 年")
    iso_week: int = Field(ge=1, le=53, description="ISO 週")
    weekday: int = Field(ge=0, le=6, description="曜日 (0=月, 6=日)")
    code: CourseCodeV2 = Field(description="A/B/C/D/M (§3.6.5)")
    course_status: CourseStatus = Field(
        default=CourseStatus.PROPOSED,
        description="proposed → course_fixed → staff_assigned (§4.5)",
    )
    assigned_staff_id: UUID | None = Field(
        default=None,
        description="Layer 3 で決定する担当スタッフ (§3.6.4)",
    )
    # W15-BE-FIXPATTERN (Phase 2 / migration 0020):
    # courses.office_id を NOT NULL 化したことに伴い、Create でも必須化する。
    # 既存データは無く、Phase 1 で導入した NULLABLE はテンプレ運用を含めても
    # 全件 NOT NULL に揃える方針。
    office_id: UUID = Field(description="所属拠点 (§4.5 / W15-BE-FIXPATTERN)")
    note: str | None = None


class CourseV2Create(CourseV2Base):
    """POST /api/v1/courses リクエスト (W2-BE4 / W15-BE-FIXPATTERN).

    ``office_id`` は W15-BE-FIXPATTERN (migration 0020) より必須フィールド。
    """


class CourseV2Update(BaseModel):
    """PATCH /api/v1/courses/{id} リクエスト (W2-BE4)."""

    model_config = ConfigDict(extra="forbid")

    course_status: CourseStatus | None = None
    assigned_staff_id: UUID | None = None
    note: str | None = None
    course_fixed_at: datetime | None = None
    staff_assigned_at: datetime | None = None


class CourseV2Read(CourseV2Base):
    """GET /api/v1/courses/{id} レスポンス (W2-BE4)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    course_fixed_at: datetime | None = Field(
        default=None,
        description="構成固定日時 (§4.5)",
    )
    staff_assigned_at: datetime | None = Field(
        default=None,
        description="スタッフ割当日時 (§4.5)",
    )
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
