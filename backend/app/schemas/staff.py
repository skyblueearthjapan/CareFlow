"""Staff schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StaffBase(BaseModel):
    code: str | None = None
    name: str
    kana: str | None = None
    sex: str | None = None
    status: str = "active"
    role: str = "staff"
    primary_office_id: UUID | None = None
    can_double_team: bool = False
    mentor_id: UUID | None = None
    note: str | None = None


class StaffCreate(StaffBase):
    pass


class StaffUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    kana: str | None = None
    sex: str | None = None
    status: str | None = None
    role: str | None = None
    primary_office_id: UUID | None = None
    can_double_team: bool | None = None
    mentor_id: UUID | None = None
    note: str | None = None


class StaffRead(StaffBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
