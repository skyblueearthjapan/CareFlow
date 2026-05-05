"""SQLAlchemy ORM models.

Importing this package registers all tables on `Base.metadata` so that
Alembic autogenerate can see them.
"""

from app.models.ai_interpret_log import AiInterpretLog
from app.models.audit_log import AuditLog
from app.models.city import City
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.course import Course
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.notification import Notification
from app.models.office import Office, OfficeCity
from app.models.patient import Patient, PatientAllowedOffice
from app.models.shift_request import ShiftRequest
from app.models.special_week import SpecialWeek, SpecialWeekItem
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
from app.models.visit_photo import VisitPhoto
from app.models.visit_staff_assignment import VisitStaffAssignment

__all__ = [
    "AiInterpretLog",
    "AuditLog",
    "City",
    "CorrectionSheet",
    "CorrectionSheetItem",
    "Course",
    "GeocodingCache",
    "KaipokeJob",
    "KaipokeJobItem",
    "MentorAssignment",
    "Notification",
    "Office",
    "OfficeCity",
    "Patient",
    "PatientAllowedOffice",
    "ShiftRequest",
    "SpecialWeek",
    "SpecialWeekItem",
    "Staff",
    "StaffEvent",
    "StaffSecondaryOffice",
    "StaffShift",
    "StaffWeeklyOverride",
    "User",
    "Visit",
    "VisitPhoto",
    "VisitStaffAssignment",
]
