"""Patient schemas — Phase 2 CRUD payloads (W3-A: master extension fields)."""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

# 7 weekday short codes used in JSONB (Mon..Sun, English short form)
Weekday = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TimeType = Literal["固定", "時間帯", "午前", "午後", "終日"]
WeekdayPriority = Literal["低", "中", "高"]

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise ValueError(f"invalid HH:MM time string: {value!r}")
    return value


class WeeklyPatternSchema(BaseModel):
    """Structured shape for ``patients.weekly_pattern`` JSONB column.

    Mirrors the dict produced by ``scripts/import_weekly_pattern.py`` and
    consumed by the W3-C WeeklyPatternEditor frontend. ``extra='allow'``
    keeps forward compatibility with future optional keys.
    """

    model_config = ConfigDict(extra="allow")

    frequency_per_week: int | None = None
    preferred_weekdays: list[Weekday] | None = None
    weekday_priority: WeekdayPriority | None = None
    service_minutes: int | None = None
    time_type: TimeType | None = None
    preferred_start: str | None = None
    preferred_end: str | None = None
    ng_weekdays: list[Weekday] | None = None

    @field_validator("frequency_per_week")
    @classmethod
    def _check_frequency(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if not 1 <= v <= 7:
            raise ValueError("frequency_per_week must be 1..7")
        return v

    @field_validator("service_minutes")
    @classmethod
    def _check_service_minutes(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if not 0 <= v <= 300:
            raise ValueError("service_minutes must be 0..300")
        return v

    @field_validator("preferred_start", "preferred_end")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)


class SpecialWeekDataSchema(BaseModel):
    """Minimal structured shape for ``patients.special_week`` JSONB column.

    The richer per-week / per-day plan is modelled by the dedicated
    ``special_weeks`` + ``special_week_items`` tables (see
    ``app.models.special_week``). This JSONB column is currently used only as
    a lightweight per-patient hint, so we keep validation permissive
    (``extra='allow'``) and add proper fields incrementally.

    TODO: tighten once the editor UI freezes its payload contract.
    """

    model_config = ConfigDict(extra="allow")


class PatientBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    kana: str | None = None
    sex: str | None = None
    age: int | None = None
    status: str = "active"
    insurance: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    primary_office_id: UUID | None = None
    required_staff_count: int = 1
    sex_restriction: str | None = None
    ng_time_start: time | None = None
    ng_time_end: time | None = None
    note: str | None = None
    weekly_pattern: WeeklyPatternSchema | None = None
    special_week: SpecialWeekDataSchema | None = None
    # W3-A additions
    area: str | None = None
    ng_staff_ids: list[UUID] | None = None
    preferred_staff_ids: list[UUID] | None = None
    specified_type: str | None = None  # 必須 / 同じ人希望 / 最初は希望
    continuous_request: bool = False


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    name: str | None = None
    kana: str | None = None
    sex: str | None = None
    age: int | None = None
    status: str | None = None
    insurance: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    primary_office_id: UUID | None = None
    required_staff_count: int | None = None
    sex_restriction: str | None = None
    ng_time_start: time | None = None
    ng_time_end: time | None = None
    note: str | None = None
    weekly_pattern: WeeklyPatternSchema | None = None
    special_week: SpecialWeekDataSchema | None = None
    # W3-A additions
    area: str | None = None
    ng_staff_ids: list[UUID] | None = None
    preferred_staff_ids: list[UUID] | None = None
    specified_type: str | None = None
    continuous_request: bool | None = None


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
