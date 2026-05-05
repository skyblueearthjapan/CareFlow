"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    allocate,
    auth,
    cities,
    dashboard,
    diff,
    health,
    integrations,
    mentor,
    offices,
    patients,
    special_weeks,
    staff,
    staff_events,
    staff_overrides,
    staff_shifts,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
# Sub-resource routers MUST be included BEFORE staff.router so that the more
# specific paths (/staff/{id}/shifts etc.) are matched before the generic
# /staff/{id} catch-all in staff.router.
api_router.include_router(staff_shifts.router, prefix="/staff", tags=["staff-shifts"])
api_router.include_router(
    staff_overrides.router, prefix="/staff", tags=["staff-overrides"]
)
api_router.include_router(staff_events.router, prefix="/staff", tags=["staff-events"])
api_router.include_router(mentor.router, prefix="/staff", tags=["mentor"])
api_router.include_router(staff.router, prefix="/staff", tags=["staff"])
api_router.include_router(visits.router, prefix="/visits", tags=["visits"])
api_router.include_router(offices.router, prefix="/offices", tags=["offices"])
api_router.include_router(cities.router, prefix="/cities", tags=["cities"])
api_router.include_router(diff.router, prefix="/diff", tags=["diff"])
api_router.include_router(allocate.router, prefix="/allocate", tags=["allocate"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(
    integrations.router, prefix="/integrations", tags=["integrations"]
)
api_router.include_router(
    special_weeks.router, prefix="/special-weeks", tags=["special-weeks"]
)

__all__ = ["api_router"]
