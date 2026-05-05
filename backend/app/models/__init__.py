"""SQLAlchemy ORM models.

Importing this package registers all tables on `Base.metadata` so that
Alembic autogenerate can see them.
"""

from app.models.ai_interpret_log import AiInterpretLog
from app.models.audit_log import AuditLog
from app.models.city import City
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.office import Office, OfficeCity
from app.models.patient import Patient, PatientAllowedOffice
from app.models.staff import (
    MentorAssignment,
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.models.user import User
from app.models.visit import Visit

__all__ = [
    "AiInterpretLog",
    "AuditLog",
    "City",
    "CorrectionSheet",
    "CorrectionSheetItem",
    "GeocodingCache",
    "KaipokeJob",
    "KaipokeJobItem",
    "MentorAssignment",
    "Office",
    "OfficeCity",
    "Patient",
    "PatientAllowedOffice",
    "Staff",
    "StaffEvent",
    "StaffSecondaryOffice",
    "StaffShift",
    "StaffWeeklyOverride",
    "User",
    "Visit",
]
