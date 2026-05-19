"""SpecialWeek (一時的な訪問パターン変更) schemas — W3-A.

`SpecialWeek` is the parent header (週単位) and `SpecialWeekItem` is the
per-visit child. Read shapes are returned by the upcoming W3-B REST surface.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# ---------- SpecialWeekItem ----------


class SpecialWeekItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    visit_date: date
    weekday: int  # 0 = Mon .. 6 = Sun
    row_label: str | None = None
    time_type: str = "時間帯"
    start_time: time | None = None
    end_time: time | None = None
    preferred_earliest: time | None = None
    preferred_latest: time | None = None
    service_minutes: int = 30
    required_staff_count: int = 1
    individual_change_handling: str | None = None  # merge / override / ignore
    note: str | None = None


class SpecialWeekItemCreate(SpecialWeekItemBase):
    pass


class SpecialWeekItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: UUID | None = None
    visit_date: date | None = None
    weekday: int | None = None
    row_label: str | None = None
    time_type: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    preferred_earliest: time | None = None
    preferred_latest: time | None = None
    service_minutes: int | None = None
    required_staff_count: int | None = None
    individual_change_handling: str | None = None
    note: str | None = None


class SpecialWeekItemRead(SpecialWeekItemBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    special_week_id: UUID
    created_at: datetime
    updated_at: datetime


# ---------- SpecialWeek ----------


class SpecialWeekBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    week_start: date
    week_end: date
    apply_mode: str = "ADD"  # ADD or REPLACE
    reason: str | None = None
    status: str = "draft"  # draft / applied / cancelled


class SpecialWeekCreate(SpecialWeekBase):
    items: list[SpecialWeekItemCreate] = []


class SpecialWeekUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None
    week_end: date | None = None
    apply_mode: str | None = None
    reason: str | None = None
    status: str | None = None
    items: list[SpecialWeekItemCreate] | None = None


class SpecialWeekRead(SpecialWeekBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    registered_at: datetime
    created_at: datetime
    updated_at: datetime
    items: list[SpecialWeekItemRead] = []
