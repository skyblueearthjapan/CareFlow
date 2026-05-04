"""Office schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OfficeBase(BaseModel):
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None


class OfficeCreate(OfficeBase):
    pass


class OfficeUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str | None = None


class OfficeRead(OfficeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
