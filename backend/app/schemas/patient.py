"""Patient schemas — v2 thin wrapper over ``app.schemas.v2.patient``.

W1-BE1 (v2 master cleanup, §4.1):
  - The legacy v1 schemas (``PatientBase`` / ``PatientCreate`` /
    ``PatientUpdate`` / ``PatientRead`` / ``WeeklyPatternSchema`` /
    ``SpecialWeekDataSchema``) are replaced by their v2 counterparts in
    ``app.schemas.v2.patient``.
  - For backward compatibility (existing imports across the codebase, eg.
    ``from app.schemas.patient import PatientCreate``) we re-export the
    v2 types under the v1 names. The shared ``_validate_hhmm`` helper is
    also kept here because ``app.schemas.staff_overrides`` and
    ``app.schemas.staff_shifts`` depend on it.
  - Old fields (``age``, ``ng_time_start`` / ``ng_time_end``,
    ``required_staff_count``, ``area``, ``ng_staff_ids``,
    ``preferred_staff_ids``, ``specified_type``, ``continuous_request``)
    are intentionally dropped from the v2 schemas; ``PatientCreate`` /
    ``PatientUpdate`` use ``extra="ignore"`` so legacy callers still
    sending those keys are accepted (the values are silently dropped).
"""

from __future__ import annotations

from pydantic import ConfigDict

# Keep ``_validate_hhmm`` exported here — it is imported by
# ``app.schemas.staff_overrides`` and ``app.schemas.staff_shifts``.
from app.schemas.v2.patient import (
    PatientV2Base,
    PatientV2Create,
    PatientV2Read,
    PatientV2Update,
    SpecialWeekRefV2,
    WeeklyPatternEntryV2,
    WeeklyPatternV2,
    _validate_hhmm,  # noqa: F401
)


class PatientBase(PatientV2Base):
    """v1 → v2 alias. ``extra='ignore'`` accepts legacy fields silently."""

    model_config = ConfigDict(extra="ignore")


class PatientCreate(PatientV2Create):
    """v1 → v2 alias. ``extra='ignore'`` accepts legacy fields silently.

    Old keys (``age``, ``ng_time_start``, ``ng_time_end``,
    ``required_staff_count``, ``area``, ``ng_staff_ids``,
    ``preferred_staff_ids``, ``specified_type``, ``continuous_request``)
    are dropped on input — the v2 schema has no place for them.
    """

    model_config = ConfigDict(extra="ignore")


class PatientUpdate(PatientV2Update):
    """v1 → v2 alias. ``extra='ignore'`` accepts legacy fields silently."""

    model_config = ConfigDict(extra="ignore")


class PatientRead(PatientV2Read):
    """v1 → v2 alias for the read response model."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")


# Legacy aliases (kept for any external import that still references them).
WeeklyPatternSchema = WeeklyPatternV2
SpecialWeekDataSchema = WeeklyPatternV2

__all__ = [
    "PatientBase",
    "PatientCreate",
    "PatientRead",
    "PatientUpdate",
    "PatientV2Base",
    "PatientV2Create",
    "PatientV2Read",
    "PatientV2Update",
    "SpecialWeekDataSchema",
    "SpecialWeekRefV2",
    "WeeklyPatternEntryV2",
    "WeeklyPatternSchema",
    "WeeklyPatternV2",
    "_validate_hhmm",
]
