"""Schemas for `staff_weekly_overrides` (per-week 休み / 時間変更 etc.).

DB storage (unchanged): `iso_year` + `iso_week` + `weekday` triple plus
`override_type` ∈ { 'off', 'custom_time', 'am_off', 'pm_off' } and `reason`
(text). The HTTP API contract however speaks the *Frontend* language:

  - `date`  (YYYY-MM-DD, derived from the iso triple via
    `datetime.date.fromisocalendar(iso_year, iso_week, weekday + 1)`)
  - `type`  (Japanese label: '休み' / '時間変更' / '午前休' / '午後休')
  - `note`  (was `reason` in the DB)

Writes additionally accept legacy English codes ('off' / 'custom_time' / …)
so existing import scripts and CLI tools remain compatible. Reads always
emit the canonical Japanese labels.
"""

from __future__ import annotations

from datetime import date as _Date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.patient import _validate_hhmm

# ---------------------------------------------------------------------------
# Public (request/response) values — Japanese UI labels are canonical.
# ---------------------------------------------------------------------------

OverrideType = Literal[
    "休み", "時間変更", "午前休", "午後休", "off", "custom_time", "am_off", "pm_off"
]

# Canonical (DB) values — keep in sync with import_staff_overrides.py.
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

# Inverse map for output: DB value → Japanese label.
_DB_TO_JP_OVERRIDE = {
    "off": "休み",
    "custom_time": "時間変更",
    "am_off": "午前休",
    "pm_off": "午後休",
}


def _normalise_override(value: str) -> str:
    """Map any accepted input to the DB canonical English code."""
    canon = _CANONICAL_OVERRIDE.get(value)
    if canon is None:
        raise ValueError(f"unknown override_type: {value!r}")
    return canon


def _db_type_to_label(value: str) -> str:
    """Map DB canonical English code to Japanese label for the response."""
    # Unknown values are returned as-is so Frontend zod surfaces a clear error
    # (rather than us silently rewriting bad data).
    return _DB_TO_JP_OVERRIDE.get(value, value)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class OverrideCreate(BaseModel):
    """Caller submits a calendar `date` + Frontend `type` / `note` names."""

    model_config = ConfigDict(extra="forbid")

    date: _Date
    type: OverrideType
    start_time: str | None = Field(default=None, description="HH:MM")
    end_time: str | None = Field(default=None, description="HH:MM")
    note: str | None = Field(default=None, max_length=500)

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return _normalise_override(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)

    @model_validator(mode="after")
    def _check_time_range(self) -> "OverrideCreate":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("start_time must be < end_time")
        if self.type == "custom_time" and (
            self.start_time is None or self.end_time is None
        ):
            raise ValueError("custom_time override requires start_time and end_time")
        return self


class OverrideUpdate(BaseModel):
    """Partial update — every field optional."""

    model_config = ConfigDict(extra="forbid")

    date: _Date | None = None
    type: OverrideType | None = None
    start_time: str | None = None
    end_time: str | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: str | None) -> str | None:
        return None if v is None else _normalise_override(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class OverrideRead(BaseModel):
    """Response contract aligned with Frontend zod `overrideReadSchema`."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    date: _Date
    type: Literal["休み", "時間変更", "午前休", "午後休"]
    start_time: str | None = None
    end_time: str | None = None
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        """Translate ORM `StaffWeeklyOverride` → Frontend-shaped dict.

        Triggered for both ORM objects (via `from_attributes`) and existing
        plain dicts (e.g. when re-validating). For dicts with the new
        Frontend keys already present, this is effectively a no-op.
        """
        if isinstance(data, dict):
            # Already in API shape (or partial API shape) — nothing to do.
            return data

        # Treat anything else as an ORM-like object and project the fields
        # we actually need into a dict. We *intentionally* do not include
        # iso_year/iso_week/weekday/staff_id/override_type/reason/timestamps
        # because the response schema does not expose them.
        iso_year = getattr(data, "iso_year", None)
        iso_week = getattr(data, "iso_week", None)
        weekday = getattr(data, "weekday", None)

        if iso_year is None or iso_week is None or weekday is None:
            # Defensive: let pydantic raise its own validation error below.
            return data

        # `weekday` is 0=Mon..6=Sun in our DB; ISO weekday is 1=Mon..7=Sun.
        date_val = _Date.fromisocalendar(iso_year, iso_week, int(weekday) + 1)

        start_t = getattr(data, "start_time", None)
        end_t = getattr(data, "end_time", None)
        # ORM `Time` → 'HH:MM' string.
        if start_t is not None and not isinstance(start_t, str):
            start_t = start_t.strftime("%H:%M")
        if end_t is not None and not isinstance(end_t, str):
            end_t = end_t.strftime("%H:%M")

        return {
            "id": getattr(data, "id"),
            "date": date_val,
            "type": _db_type_to_label(getattr(data, "override_type", "")),
            "start_time": start_t,
            "end_time": end_t,
            "note": getattr(data, "reason", None),
        }
