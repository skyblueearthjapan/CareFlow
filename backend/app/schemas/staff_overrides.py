"""Schemas for `staff_weekly_overrides` (per-week 休み / 時間変更 etc.).

The DB stores `iso_year` + `iso_week` + `weekday` keys (legacy from import
script) plus `override_type` ∈ { 'off', 'custom_time' } using English
canonical values. The HTTP API accepts a friendlier `date` field plus
Japanese aliases ("休み"/"時間変更"/"午前休"/"午後休") which are normalised
on input. Reads always echo the canonical English `override_type` and
include both the `date` and the iso week triple for client convenience.
"""

from __future__ import annotations

from datetime import date as _Date
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.patient import _validate_hhmm

# Public (request) values — Japanese UI labels and legacy English codes.
OverrideType = Literal[
    "休み", "時間変更", "午前休", "午後休", "off", "custom_time", "am_off", "pm_off"
]

# Canonical (storage) values — keep in sync with import_staff_overrides.py
_CANONICAL_OVERRIDE = {
    "休み": "off",
    "off": "off",
    "OFF": "off",
    "時間変更": "custom_time",
    "custom_time": "custom_time",
    "午前休": "am_off",
    "am_off": "am_off",
    "午後休": "pm_off",
    "pm_off": "pm_off",
}


def _normalise_override(value: str) -> str:
    canon = _CANONICAL_OVERRIDE.get(value)
    if canon is None:
        raise ValueError(f"unknown override_type: {value!r}")
    return canon


class OverrideBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_type: OverrideType
    start_time: str | None = Field(default=None, description="HH:MM")
    end_time: str | None = Field(default=None, description="HH:MM")
    reason: str | None = None

    @field_validator("override_type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return _normalise_override(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)

    @model_validator(mode="after")
    def _check_time_range(self) -> "OverrideBase":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("start_time must be < end_time")
        if self.override_type == "custom_time" and (
            self.start_time is None or self.end_time is None
        ):
            raise ValueError("custom_time override requires start_time and end_time")
        return self


class OverrideCreate(OverrideBase):
    """Caller submits a calendar `date`; we derive iso_year/week/weekday."""

    date: _Date


class OverrideUpdate(BaseModel):
    """Partial update — every field optional."""

    model_config = ConfigDict(extra="forbid")

    date: _Date | None = None
    override_type: OverrideType | None = None
    start_time: str | None = None
    end_time: str | None = None
    reason: str | None = None

    @field_validator("override_type")
    @classmethod
    def _norm_type(cls, v: str | None) -> str | None:
        return None if v is None else _normalise_override(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)


class OverrideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    staff_id: UUID
    iso_year: int
    iso_week: int
    weekday: int
    override_type: str
    start_time: str | None = None
    end_time: str | None = None
    reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _coerce_time(cls, v: object) -> str | None:
        # Accept datetime.time from ORM and serialise as HH:MM.
        if v is None:
            return None
        if isinstance(v, str):
            return v
        # datetime.time
        return v.strftime("%H:%M")  # type: ignore[union-attr]
