"""Schemas for `staff_shift_confirmations` (月次出勤カレンダー確定).

正典 = docs/plans/staff-shift-confirmation-design.md §2-a。
month は月初日 (YYYY-MM-01) のみ受理する。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class ShiftConfirmationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: date

    @field_validator("month")
    @classmethod
    def _must_be_first_of_month(cls, v: date) -> date:
        if v.day != 1:
            raise ValueError("month は月初日 (YYYY-MM-01) を指定してください")
        return v


class ShiftConfirmationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    staff_id: UUID
    month: date
    confirmed_by: UUID | None = None
    confirmed_at: datetime
