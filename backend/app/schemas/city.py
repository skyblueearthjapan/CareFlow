"""City schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CityBase(BaseModel):
    prefecture: str
    name: str
    jis_code: str | None = None


class CityCreate(CityBase):
    pass


class CityUpdate(BaseModel):
    prefecture: str | None = None
    name: str | None = None
    jis_code: str | None = None


class CityRead(CityBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
