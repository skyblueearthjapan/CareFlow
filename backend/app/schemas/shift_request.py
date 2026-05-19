"""Pydantic schemas for ``shift_requests`` (シフト希望提出)."""

from __future__ import annotations

from datetime import date as _Date  # noqa: N812
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ShiftRequestStatusLit = Literal["submitted", "reviewed"]


def _coerce_to_first_of_month(value: _Date) -> _Date:
    """Snap any date in a month to the first day so target_month is canonical."""
    return value.replace(day=1)


class ShiftRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_month: _Date
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("target_month")
    @classmethod
    def _normalize_month(cls, v: _Date) -> _Date:
        return _coerce_to_first_of_month(v)


class ShiftRequestStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ShiftRequestStatusLit


class ShiftRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    staff_id: UUID
    target_month: _Date
    content: str
    submitted_at: datetime
    status: ShiftRequestStatusLit
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
