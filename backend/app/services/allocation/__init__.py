"""CareFlow allocation engine.

Ported from PlaywrightTest1/lib/allocation_engine.py (Apache License 2.0).
Pure in-memory allocation engine: no DB access — receives Patient / Staff /
VisitRequest dataclasses and returns AssignmentResult dataclasses.
"""

from app.services.allocation.engine import AllocationEngine
from app.services.allocation.models import (
    AssignmentResult,
    ConfirmedHistory,
    Event,
    Interval,
    MentorPair,
    Patient,
    PatientChange,
    SpecialWeekDetail,
    SpecialWeekHeader,
    Staff,
    StaffChange,
    VisitRequest,
    WeeklyPattern,
)

__all__ = [
    "AllocationEngine",
    "AssignmentResult",
    "ConfirmedHistory",
    "Event",
    "Interval",
    "MentorPair",
    "Patient",
    "PatientChange",
    "SpecialWeekDetail",
    "SpecialWeekHeader",
    "Staff",
    "StaffChange",
    "VisitRequest",
    "WeeklyPattern",
]
