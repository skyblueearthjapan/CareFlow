"""Office schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OfficeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str | None = None
    prefecture: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None


class OfficeCreate(OfficeBase):
    allowed_cities: list[UUID] = []


class OfficeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    code: str | None = None
    prefecture: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None
    allowed_cities: list[UUID] | None = None


class OfficeRead(OfficeBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    allowed_cities: list[UUID] = []
