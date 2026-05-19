"""Schemas for staff weekly fixed shift (`StaffShift`).

Mirrors the 7-row weekday × on/off × HH:MM model. The bulk PUT endpoint
expects exactly 7 entries (one per weekday 0..6 = Mon..Sun) and fully
replaces existing rows in a single transaction.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.patient import _validate_hhmm


class StaffShiftItem(BaseModel):
    """One weekday entry in the fixed weekly shift."""

    model_config = ConfigDict(extra="forbid")

    weekday: Annotated[int, Field(ge=0, le=6, description="0=Mon ... 6=Sun")]
    is_on: bool = True
    start_time: str | None = Field(default=None, description="HH:MM, 24h")
    end_time: str | None = Field(default=None, description="HH:MM, 24h")

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)

    @model_validator(mode="after")
    def _check_range(self) -> StaffShiftItem:
        if self.is_on:
            if self.start_time is None or self.end_time is None:
                # On-day with no times is allowed (treated as full-day default).
                return self
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be < end_time")
        return self


class ShiftsBulkUpdate(BaseModel):
    """Full replacement payload — must include all 7 weekdays exactly once."""

    model_config = ConfigDict(extra="forbid")

    shifts: list[StaffShiftItem] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _check_unique_weekdays(self) -> ShiftsBulkUpdate:
        seen = {item.weekday for item in self.shifts}
        if len(seen) != 7 or seen != {0, 1, 2, 3, 4, 5, 6}:
            raise ValueError("shifts must cover weekdays 0..6 exactly once")
        return self


class ShiftsResponse(BaseModel):
    """GET / PUT response — always returns 7 entries, sorted by weekday."""

    model_config = ConfigDict(extra="forbid")

    shifts: list[StaffShiftItem]
