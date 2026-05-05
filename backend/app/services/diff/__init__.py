"""Diff engine package — pure CSV diff calculation.

Ported from PlaywrightTest1/lib/diff_engine.py (W1-E, Phase 4-6/4-7/4-16).
DB integration is owned by the API layer (`app.api.v1.diff`); this package
stays a pure-compute module so it can be unit-tested without I/O.
"""

from app.services.diff.engine import (
    Correction,
    ScheduleEntry,
    compare_schedules,
    compare_schedules_from_content,
    generate_correction_sheet,
    load_correction_sheet,
    parse_csv_from_content,
    parse_kaipoke_csv,
    parse_optimized_csv,
    validate_correction_data,
)

__all__ = [
    "Correction",
    "ScheduleEntry",
    "compare_schedules",
    "compare_schedules_from_content",
    "generate_correction_sheet",
    "load_correction_sheet",
    "parse_csv_from_content",
    "parse_kaipoke_csv",
    "parse_optimized_csv",
    "validate_correction_data",
]
