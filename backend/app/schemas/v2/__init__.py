"""v2 共有 Pydantic schemas (Wave 0-C).

設計仕様書 `docs/plans/v2-allocation-redesign.md` v0.9 に対応する型定義。
フロントエンド `frontend/lib/schemas/v2/` の zod schema と **完全一致** させる。

実装手順書 §1 0-C で要求される 11 種の必須型:
  PatientV2 (Base/Create/Update/Read) - 設計書 §4.1
  WeeklyPatternV2                     - 設計書 §3.3 / §4.1
  StaffV2 (Base/Create/Update/Read)   - 設計書 §4.2
  OfficeV2 (Base/Create/Update/Read)  - 設計書 §4.3
  CourseV2 (Base/Create/Update/Read)  - 設計書 §4.5
  VisitV2 (Base/Create/Update/Read)   - 設計書 §3.3 / §4.5
  PendingRequestV2 (Base/Create/Update/Read) - 設計書 §3.5 / §4.4
  CourseStatus enum                    - 設計書 §4.5
  RequestType enum                     - 設計書 §4.4
  RequestStatus enum                   - 設計書 §3.5.4
  AiContextType enum                   - 設計書 §3.5.2
"""

from __future__ import annotations

from app.schemas.v2.acceptance import (
    AcceptanceCalendarBulkUpsert,
    AcceptanceCalendarEntry,
    AcceptanceCalendarRead,
    AcceptanceStatus,
)
from app.schemas.v2.auto_allocate import (
    AutoAllocateRequest,
    AutoAllocateResponse,
    KpiResponse,
    ProposalApplyResponse,
    ProposalDiscardResponse,
    ProposedCourseSummary,
)
from app.schemas.v2.auto_schedule_v2 import (
    AutoScheduleV2ApplyIndividualRequest,
    AutoScheduleV2ApplyIndividualResponse,
    AutoScheduleV2DiffAddRequest,
    AutoScheduleV2DiffAddResponse,
    AutoScheduleV2FullOptimizeRequest,
    AutoScheduleV2FullOptimizeResponse,
    AutoScheduleV2ResetToFixedRequest,
    AutoScheduleV2ResetToFixedResponse,
    V2BeforeAfterSummary,
    V2CourseContainer,
    V2CourseSummary,
    V2DiffAddProposal,
    V2IndividualProposal,
    V2KpiOverall,
    V2ProposalDelta,
    V2VisitForUI,
    V2VisitPlan,
    V2WeekdayBeforeAfter,
)
from app.schemas.v2.course import (
    CourseCodeV2,
    CourseV2Base,
    CourseV2Create,
    CourseV2Read,
    CourseV2Update,
)
from app.schemas.v2.course_template import (
    CourseTemplateBase,
    CourseTemplateCreate,
    CourseTemplateRead,
    CourseTemplateUpdate,
)
from app.schemas.v2.enums import (
    AiContextType,
    CourseStatus,
    RequestScope,
    RequestStatus,
    RequestType,
)
from app.schemas.v2.office import (
    OfficeV2Base,
    OfficeV2Create,
    OfficeV2Read,
    OfficeV2Update,
)
from app.schemas.v2.patient import (
    InsuranceV2,
    PatientStatusV2,
    PatientV2Base,
    PatientV2Create,
    PatientV2Read,
    PatientV2Update,
    SexRestrictionV2,
    SexV2,
    SpecialWeekRefV2,
    TimeTypeV2,
    VisitFrequencyV2,
    WeekdayV2,
    WeeklyPatternEntryV2,
    WeeklyPatternV2,
)
from app.schemas.v2.patient_fixed_visit import (
    PatientFixedVisitMode,
    PatientFixedVisitsBulkPut,
    PatientFixedVisitV2Base,
    PatientFixedVisitV2Read,
)
from app.schemas.v2.pending_request import (
    PendingRequestApprove,
    PendingRequestReject,
    PendingRequestV2Base,
    PendingRequestV2Create,
    PendingRequestV2Read,
    PendingRequestV2Update,
)
from app.schemas.v2.staff import (
    RoleV2,
    StaffStatusV2,
    StaffV2Base,
    StaffV2Create,
    StaffV2Read,
    StaffV2Update,
)
from app.schemas.v2.staff_companion_assignment import (
    StaffCompanionAssignmentsBulkPut,
    StaffCompanionAssignmentV2Base,
    StaffCompanionAssignmentV2Read,
    StaffCompanionPart,
)
from app.schemas.v2.visit import (
    VisitSourceV2,
    VisitStaffAssignmentV2Read,
    VisitStatusV2,
    VisitTypeV2,
    VisitV2Base,
    VisitV2Create,
    VisitV2Read,
    VisitV2Update,
)

__all__ = [
    # acceptance calendar (W15-BE1)
    "AcceptanceCalendarBulkUpsert",
    "AcceptanceCalendarEntry",
    "AcceptanceCalendarRead",
    "AcceptanceStatus",
    # auto-allocate (W41 v1.0)
    "AutoAllocateRequest",
    "AutoAllocateResponse",
    "KpiResponse",
    "ProposalApplyResponse",
    "ProposalDiscardResponse",
    "ProposedCourseSummary",
    # auto-schedule v2 (W41 v2.0)
    "AutoScheduleV2ApplyIndividualRequest",
    "AutoScheduleV2ApplyIndividualResponse",
    "AutoScheduleV2DiffAddRequest",
    "AutoScheduleV2DiffAddResponse",
    "AutoScheduleV2FullOptimizeRequest",
    "AutoScheduleV2FullOptimizeResponse",
    "AutoScheduleV2ResetToFixedRequest",
    "AutoScheduleV2ResetToFixedResponse",
    "V2BeforeAfterSummary",
    "V2CourseContainer",
    "V2CourseSummary",
    "V2DiffAddProposal",
    "V2IndividualProposal",
    "V2KpiOverall",
    "V2ProposalDelta",
    "V2VisitForUI",
    "V2VisitPlan",
    "V2WeekdayBeforeAfter",
    # enums
    "AiContextType",
    "CourseCodeV2",
    "CourseStatus",
    "CourseTemplateBase",
    "CourseTemplateCreate",
    "CourseTemplateRead",
    "CourseTemplateUpdate",
    "RequestScope",
    "RequestStatus",
    "RequestType",
    # patient
    "InsuranceV2",
    "PatientStatusV2",
    "PatientV2Base",
    "PatientV2Create",
    "PatientV2Read",
    "PatientV2Update",
    "SexRestrictionV2",
    "SexV2",
    "SpecialWeekRefV2",
    "TimeTypeV2",
    "VisitFrequencyV2",
    "WeekdayV2",
    "WeeklyPatternEntryV2",
    "WeeklyPatternV2",
    # staff
    "RoleV2",
    "StaffStatusV2",
    "StaffV2Base",
    "StaffV2Create",
    "StaffV2Read",
    "StaffV2Update",
    # staff companion assignment
    "StaffCompanionAssignmentsBulkPut",
    "StaffCompanionAssignmentV2Base",
    "StaffCompanionAssignmentV2Read",
    "StaffCompanionPart",
    # office
    "OfficeV2Base",
    "OfficeV2Create",
    "OfficeV2Read",
    "OfficeV2Update",
    # course
    "CourseV2Base",
    "CourseV2Create",
    "CourseV2Read",
    "CourseV2Update",
    # visit
    "VisitSourceV2",
    "VisitStaffAssignmentV2Read",
    "VisitStatusV2",
    "VisitTypeV2",
    "VisitV2Base",
    "VisitV2Create",
    "VisitV2Read",
    "VisitV2Update",
    # patient_fixed_visit
    "PatientFixedVisitMode",
    "PatientFixedVisitsBulkPut",
    "PatientFixedVisitV2Base",
    "PatientFixedVisitV2Read",
    # pending_request
    "PendingRequestApprove",
    "PendingRequestReject",
    "PendingRequestV2Base",
    "PendingRequestV2Create",
    "PendingRequestV2Read",
    "PendingRequestV2Update",
]
