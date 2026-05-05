"""Staff schemas — Phase 2 CRUD payloads (W3-A: master extension fields)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StaffBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    # W3-A additions
    home_address: str | None = None
    home_lat: Decimal | None = None
    home_lng: Decimal | None = None
    areas: list[str] | None = None
    max_per_day: int = 6
    skill_level: str | None = None  # 新人 / 中堅 / ベテラン
    assignment_volume: str | None = None  # 少なめ / 通常 / 多め


class StaffCreate(StaffBase):
    pass


class StaffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    # W3-A additions
    home_address: str | None = None
    home_lat: Decimal | None = None
    home_lng: Decimal | None = None
    areas: list[str] | None = None
    max_per_day: int | None = None
    skill_level: str | None = None
    assignment_volume: str | None = None


class StaffRead(StaffBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
