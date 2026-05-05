"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    ai,
    allocate,
    audit_logs,
    auth,
    cities,
    courses,
    dashboard,
    diff,
    geocoding,
    health,
    integrations,
    mentor,
    notifications,
    offices,
    patients,
    shift_requests,
    special_weeks,
    staff,
    staff_events,
    staff_overrides,
    staff_shifts,
    visit_photos,
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
api_router.include_router(staff_overrides.router, prefix="/staff", tags=["staff-overrides"])
api_router.include_router(staff_events.router, prefix="/staff", tags=["staff-events"])
api_router.include_router(mentor.router, prefix="/staff", tags=["mentor"])
# Wave 4-D: shift-request sub-resource (/staff/{id}/shift-requests).
api_router.include_router(shift_requests.router, prefix="/staff", tags=["shift-requests"])
api_router.include_router(staff.router, prefix="/staff", tags=["staff"])
# Wave 4-D: visit photo sub-resource (/visits/{id}/photos[...]) — registered
# before visits.router so its routes are matched before the generic
# /visits/{visit_id} catch-all.
api_router.include_router(visit_photos.router, prefix="/visits", tags=["visit-photos"])
api_router.include_router(visits.router, prefix="/visits", tags=["visits"])
# W2-BE4: Course CRUD (generate / fix / assign-staff は Wave 4 で追加).
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(offices.router, prefix="/offices", tags=["offices"])
api_router.include_router(cities.router, prefix="/cities", tags=["cities"])
api_router.include_router(diff.router, prefix="/diff", tags=["diff"])
api_router.include_router(allocate.router, prefix="/allocate", tags=["allocate"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
# Geocoding relay: POST /geocode + GET /geocoding/cache (Wave 4-C).
api_router.include_router(geocoding.router, tags=["geocoding"])
# Gemini-backed AI interpret + audit logs (Wave 4-B / D4 Phase E).
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(special_weeks.router, prefix="/special-weeks", tags=["special-weeks"])
# Wave 4-F: HTTP audit-log read API (admin only).
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
# Wave 4-D: notifications inbox + admin-create (W6 will add producer side).
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
# Wave 4-D: cross-staff shift-request status update (admin/manager only).
api_router.include_router(
    shift_requests.status_router, prefix="/shift-requests", tags=["shift-requests"]
)

__all__ = ["api_router"]
