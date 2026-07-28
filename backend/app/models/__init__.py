"""SQLAlchemy ORM models.

Importing this package registers all tables on `Base.metadata` so that
Alembic autogenerate can see them.
"""

from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.acceptance_calendar_week import AcceptanceCalendarWeek
from app.models.audit_log import AuditLog
from app.models.checkin_settings import CheckinSettings
from app.models.city import City
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_credential import KaipokeCredential
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.notification import Notification
from app.models.office import Office, OfficeCity
from app.models.office_area_prompt_dismissal import OfficeAreaPromptDismissal
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient, PatientAllowedOffice
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.pending_request import PendingRequest
from app.models.revoked_qr_token import RevokedQrToken
from app.models.schedule_op_log import ScheduleOpLog
from app.models.scheduling_settings import SchedulingSettings
from app.models.shift_request import ShiftRequest
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.special_week import SpecialWeek, SpecialWeekItem
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.trainee_accompaniment import (
    TraineeAccompaniment,
    TraineeAccompanimentDefault,
)
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_photo import VisitPhoto
from app.models.visit_review import VisitReview
from app.models.visit_staff_assignment import VisitStaffAssignment

__all__ = [
    "AcceptanceCalendar",
    "AcceptanceCalendarWeek",
    "AuditLog",
    "CheckinSettings",
    "City",
    "CorrectionSheet",
    "CorrectionSheetItem",
    "KaipokeCredential",
    "Course",
    "CourseTemplate",
    "GeocodingCache",
    "KaipokeJob",
    "KaipokeJobItem",
    "Notification",
    "Office",
    "OfficeAreaPromptDismissal",
    "OfficeCity",
    "OfficeFeatureFlag",
    "Patient",
    "PatientAllowedOffice",
    "PatientFixedVisit",
    "PatientSameAddressLink",
    "PendingRequest",
    "RevokedQrToken",
    "ScheduleOpLog",
    "SchedulingSettings",
    "ShiftRequest",
    "SpecialVisitMark",
    "SpecialVisitPeriod",
    "SpecialWeek",
    "SpecialWeekItem",
    "SuggestionDismissal",
    "Staff",
    "StaffEvent",
    "StaffSecondaryOffice",
    "StaffShift",
    "StaffWeeklyOverride",
    "TraineeAccompaniment",
    "TraineeAccompanimentDefault",
    "User",
    "Visit",
    "VisitCheckin",
    "VisitPhoto",
    "VisitReview",
    "VisitStaffAssignment",
]
