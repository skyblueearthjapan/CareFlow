"""CourseTemplate v2 schemas (Wave 15 Phase 1 / W15-BE1).

永続コーステンプレート (`course_templates` テーブル) の API 入出力型。

- 拠点 (office) 単位で A/B/C/D/E などのラベルを保持
- 月〜日の各曜日の定員 (capacity) を 0..50 の整数で保持
- 論理削除 (`deleted_at`) を採用
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CourseTemplateBase(BaseModel):
    """コーステンプレート基底スキーマ."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=8, description="ラベル例: 'A', 'B', 'C', 'D', 'E'")
    capacity_mon: int = Field(default=0, ge=0, le=50, description="月曜の定員 (0-50)")
    capacity_tue: int = Field(default=0, ge=0, le=50, description="火曜の定員 (0-50)")
    capacity_wed: int = Field(default=0, ge=0, le=50, description="水曜の定員 (0-50)")
    capacity_thu: int = Field(default=0, ge=0, le=50, description="木曜の定員 (0-50)")
    capacity_fri: int = Field(default=0, ge=0, le=50, description="金曜の定員 (0-50)")
    capacity_sat: int = Field(default=0, ge=0, le=50, description="土曜の定員 (0-50)")
    capacity_sun: int = Field(default=0, ge=0, le=50, description="日曜の定員 (0-50)")
    notes: str | None = Field(default=None, description="自由記述")


class CourseTemplateCreate(CourseTemplateBase):
    """POST /api/v1/course-templates リクエスト."""

    office_id: UUID = Field(description="拠点 (offices.id)")


class CourseTemplateUpdate(BaseModel):
    """PATCH /api/v1/course-templates/{id} リクエスト.

    全フィールド任意 (PATCH セマンティクス)。office_id は変更不可。
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=8)
    capacity_mon: int | None = Field(default=None, ge=0, le=50)
    capacity_tue: int | None = Field(default=None, ge=0, le=50)
    capacity_wed: int | None = Field(default=None, ge=0, le=50)
    capacity_thu: int | None = Field(default=None, ge=0, le=50)
    capacity_fri: int | None = Field(default=None, ge=0, le=50)
    capacity_sat: int | None = Field(default=None, ge=0, le=50)
    capacity_sun: int | None = Field(default=None, ge=0, le=50)
    notes: str | None = None


class CourseTemplateRead(CourseTemplateBase):
    """GET /api/v1/course-templates レスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    office_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
