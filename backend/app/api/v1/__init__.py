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
    offices,
    patients,
    special_weeks,
    staff,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
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
