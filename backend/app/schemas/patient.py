"""Patient schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PatientBase(BaseModel):
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
    weekly_pattern: dict | None = None
    special_week: dict | None = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
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
    weekly_pattern: dict | None = None
    special_week: dict | None = None


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
