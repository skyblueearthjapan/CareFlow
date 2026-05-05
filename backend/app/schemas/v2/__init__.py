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

from app.schemas.v2.course import (
    CourseCodeV2,
    CourseV2Base,
    CourseV2Create,
    CourseV2Read,
    CourseV2Update,
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
    # enums
    "AiContextType",
    "CourseCodeV2",
    "CourseStatus",
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
    # pending_request
    "PendingRequestApprove",
    "PendingRequestReject",
    "PendingRequestV2Base",
    "PendingRequestV2Create",
    "PendingRequestV2Read",
    "PendingRequestV2Update",
]
