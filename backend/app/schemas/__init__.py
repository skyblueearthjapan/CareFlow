"""Pydantic schemas (auth + Phase 2 entity CRUD)."""

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.schemas.city import CityBase, CityCreate, CityRead, CityUpdate
from app.schemas.office import OfficeBase, OfficeCreate, OfficeRead, OfficeUpdate
from app.schemas.patient import (
    PatientBase,
    PatientCreate,
    PatientRead,
    PatientUpdate,
)
from app.schemas.special_week import (
    SpecialWeekBase,
    SpecialWeekCreate,
    SpecialWeekItemBase,
    SpecialWeekItemCreate,
    SpecialWeekItemRead,
    SpecialWeekItemUpdate,
    SpecialWeekRead,
    SpecialWeekUpdate,
)
from app.schemas.staff import StaffBase, StaffCreate, StaffRead, StaffUpdate
from app.schemas.visit import VisitBase, VisitCreate, VisitRead, VisitUpdate

__all__ = [
    "CityBase",
    "CityCreate",
    "CityRead",
    "CityUpdate",
    "LoginRequest",
    "LoginResponse",
    "OfficeBase",
    "OfficeCreate",
    "OfficeRead",
    "OfficeUpdate",
    "PatientBase",
    "PatientCreate",
    "PatientRead",
    "PatientUpdate",
    "RefreshRequest",
    "SpecialWeekBase",
    "SpecialWeekCreate",
    "SpecialWeekItemBase",
    "SpecialWeekItemCreate",
    "SpecialWeekItemRead",
    "SpecialWeekItemUpdate",
    "SpecialWeekRead",
    "SpecialWeekUpdate",
    "StaffBase",
    "StaffCreate",
    "StaffRead",
    "StaffUpdate",
    "TokenPair",
    "UserOut",
    "VisitBase",
    "VisitCreate",
    "VisitRead",
    "VisitUpdate",
]
