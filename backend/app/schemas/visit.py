"""Visit schemas — Phase 2 CRUD payloads."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VisitBase(BaseModel):
    patient_id: UUID
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date
    start_time: time
    end_time: time
    type: str
    status: str = "planned"
    source: str = "manual"
    note: str | None = None
    kaipoke_id: str | None = None


class VisitCreate(VisitBase):
    pass


class VisitUpdate(BaseModel):
    patient_id: UUID | None = None
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    type: str | None = None
    status: str | None = None
    source: str | None = None
    note: str | None = None
    kaipoke_id: str | None = None


class VisitRead(VisitBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # Denormalized display names — populated by selectinload() in the router.
    # Frontend (`schedule/page.tsx`) renders these directly without a join.
    patient_name: str | None = None
    staff_name: str | None = None
