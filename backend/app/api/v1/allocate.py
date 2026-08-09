"""Allocate endpoint — run the allocation engine for a given ISO week.

Phase W1-D (continuation). Loads the relevant patients/staff/visits/shifts
from the DB, builds engine inputs, and returns the AssignmentResult list as
:class:`AllocateResponse`. Pure-compute is delegated to
:mod:`app.services.allocation`; this router handles auth, payload validation,
and DB orchestration.

When no visit requests exist for the week the endpoint returns an empty
assignment list rather than invoking the engine. Patient ``weekly_pattern``
JSONB is unpacked into the engine's per-patient and per-request fields by
``_build_inputs``.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import date, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.core.rate_limit import limiter
from app.models.patient import Patient as PatientORM
from app.models.staff import Staff as StaffORM
from app.models.user import User
from app.models.visit import Visit as VisitORM
from app.schemas.allocation import (
    AllocateRequest,
    AllocateResponse,
    AllocateSummary,
    AssignmentItem,
)
from app.services.allocation import (
    AllocationEngine,
    Patient,
    Staff,
    VisitRequest,
)

router = APIRouter()


_WEEKDAY_CODES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MAX_VISITS_PER_RUN = 5000
_ALLOCATE_TIMEOUT_SEC = 30.0


def _to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _parse_hhmm(value: object) -> int | None:
    """Parse 'HH:MM' string into minutes-from-midnight; tolerate junk."""
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hh, mm = value.split(":", 1)
        h = int(hh)
        m = int(mm)
    except (ValueError, TypeError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def _safe_pattern(raw: object) -> dict:
    """Coerce ``patient.weekly_pattern`` JSONB into a plain dict.

    Defends against the column being ``None``, an empty dict, or — in
    pathological data — a non-dict value (string / list). Always returns
    something ``.get()`` works on.
    """
    return raw if isinstance(raw, dict) else {}


def _coerce_positive_int(value: object) -> int | None:
    """Accept JSONB int-or-float values; reject ``bool`` and non-numerics.

    Background: JSON has no distinct int type, so values importer-side may
    arrive as ``float`` (``60.0``) when round-tripped through openpyxl.
    Earlier code used ``isinstance(value, int)`` which silently rejected
    those rows AND silently accepted ``True`` (Python bool is a subclass of
    int) — Claude review M2.
    """
    if isinstance(value, bool):  # bool ⊂ int — exclude explicitly.
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_str_list(value: object) -> list[str]:
    """Coerce JSONB list-ish values into ``list[str]``; empty on junk."""
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if x is not None]


def _build_inputs(
    patients_db: list[PatientORM],
    staff_db: list[StaffORM],
    visits_db: list[VisitORM],
) -> tuple[list[Staff], dict[str, Patient], list[VisitRequest]]:
    """Map ORM rows to the engine's pure dataclasses.

    Translates each ``Patient`` / ``Staff`` / ``Visit`` row into the
    allocation engine's dataclass surface. ``patient.weekly_pattern`` (a
    JSONB column produced by the weekly-pattern editor) is unpacked into
    the engine's per-patient time / weekday / service-length fields, and
    each ``VisitRequest`` inherits the patient's ``time_type`` /
    ``need_staff`` / NG-staff / sex restriction / specified-staff
    constraints so the engine can score candidates correctly. All
    pattern keys are read defensively — missing or malformed entries
    fall back to safe defaults rather than raising.
    """
    patient_map: dict[str, Patient] = {}
    # Keep a parallel lookup for per-visit field resolution below.
    pattern_by_pid: dict[str, dict] = {}
    for p in patients_db:
        pid = str(p.id)
        wp = _safe_pattern(p.weekly_pattern)
        pattern_by_pid[pid] = wp

        time_type = wp.get("time_type") or "固定"
        service_minutes = _coerce_positive_int(wp.get("service_minutes"))
        if service_minutes is None or service_minutes <= 0:
            service_minutes = 60
        weekly_count = _coerce_positive_int(wp.get("frequency_per_week"))
        if weekly_count is None or weekly_count < 0:
            weekly_count = 0

        # W1-BE1: ng_staff_ids / preferred_staff_ids / area / specified_type /
        # continuous_request / required_staff_count are dropped at the DB
        # level (0009_v2_patient_master_cleanup). Read defensively via
        # ``getattr`` with safe defaults so this v1 engine entry-point keeps
        # functioning until the v2 layered allocator (Wave 4) replaces it.
        ng_staff_ids = [str(x) for x in (getattr(p, "ng_staff_ids", None) or [])]
        preferred_staff_ids = [str(x) for x in (getattr(p, "preferred_staff_ids", None) or [])]

        patient_map[pid] = Patient(
            pid=pid,
            name=p.name,
            area=getattr(p, "area", None) or "",
            lat=float(p.lat) if p.lat is not None else None,
            lng=float(p.lng) if p.lng is not None else None,
            service_minutes=service_minutes,
            weekly_count=weekly_count,
            need_staff=getattr(p, "required_staff_count", None) or 1,
            sex_limit=p.sex_restriction or "",
            cont_pref=("同じ人希望" if getattr(p, "continuous_request", False) else ""),
            fixed_staff_ids=preferred_staff_ids,
            fixed_type=getattr(p, "specified_type", None) or "",
            ng_staff_ids=ng_staff_ids,
            pref_days=_coerce_str_list(wp.get("preferred_weekdays")),
            ng_days=_coerce_str_list(wp.get("ng_weekdays")),
            time_type=time_type,
            start_pref_min=_parse_hhmm(wp.get("preferred_start")),
            end_pref_min=_parse_hhmm(wp.get("preferred_end")),
        )

    engine_staff: list[Staff] = []
    for s in staff_db:
        engine_staff.append(
            Staff(
                sid=str(s.id),
                name=s.name,
                gender=s.sex or "",
                work_days=list(_WEEKDAY_CODES),
            )
        )

    requests: list[VisitRequest] = []
    for v in visits_db:
        if v.start_time is None or v.end_time is None:
            # Visit.start_time / end_time are NOT NULL at the DB level
            # (`models/visit.py`). Hitting a NULL here means schema/data
            # corruption — fail loudly rather than mask with a 60-min default.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Visit time data corrupted",
            )
        weekday = _WEEKDAY_CODES[v.visit_date.weekday()]
        date_str = v.visit_date.strftime("%Y/%m/%d")
        start_min = _to_minutes(v.start_time)
        end_min = _to_minutes(v.end_time)
        service_min = end_min - start_min
        pid = str(v.patient_id)
        patient = patient_map.get(pid)
        wp = pattern_by_pid.get(pid, {})

        # Inherit per-patient constraints onto the request so the engine
        # scoring loop (which reads from VisitRequest, not Patient, for
        # most decisions) sees them.
        if patient is not None:
            req_time_type = patient.time_type or "固定"
            req_need_staff = patient.need_staff or 1
            req_sex_limit = patient.sex_limit
            req_cont_pref = patient.cont_pref
            req_ng_staff = list(patient.ng_staff_ids)
            req_specified_staff = list(patient.fixed_staff_ids)
            req_specified_type = patient.fixed_type
            pname = patient.name
            area = patient.area
        else:
            req_time_type = "固定"
            req_need_staff = 1
            req_sex_limit = ""
            req_cont_pref = ""
            req_ng_staff = []
            req_specified_staff = []
            req_specified_type = ""
            pname = ""
            area = ""

        # earliest/latest mirror the patient's preferred window when the
        # request itself has no explicit constraint; the engine's
        # ``get_effective_window`` falls back to TIME_TYPE_DEFAULTS when
        # both are None, so leaving them unset is safe.
        earliest_min = _parse_hhmm(wp.get("preferred_start"))
        latest_min = _parse_hhmm(wp.get("preferred_end"))

        requests.append(
            VisitRequest(
                request_id=str(v.id),
                date_str=date_str,
                weekday=weekday,
                pid=pid,
                pname=pname,
                area=area,
                start_min=start_min,
                end_min=end_min,
                service_min=service_min,
                need_staff=req_need_staff,
                specified_staff_ids=req_specified_staff,
                specified_type=req_specified_type,
                ng_staff_ids=req_ng_staff,
                sex_limit=req_sex_limit,
                cont_pref=req_cont_pref,
                time_type=req_time_type,
                earliest_min=earliest_min,
                latest_min=latest_min,
            )
        )

    return engine_staff, patient_map, requests


@router.post(
    "/run",
    response_model=AllocateResponse,
    summary="Run the allocation engine for a single ISO week",
)
@limiter.limit("3/minute")
async def run_allocate(
    request: Request,
    payload: AllocateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> AllocateResponse:
    # ``request`` is required by slowapi to extract the client IP for the
    # per-IP 3/min ceiling on this CPU-heavy endpoint.
    if payload.week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="week_start must be a Monday",
        )

    week_end: date = payload.week_start + timedelta(days=6)

    patients_db = list(
        (await db.scalars(select(PatientORM).where(PatientORM.deleted_at.is_(None)))).all()
    )
    staff_db = list((await db.scalars(select(StaffORM).where(StaffORM.deleted_at.is_(None)))).all())
    visits_db = list(
        (
            await db.scalars(
                select(VisitORM).where(
                    VisitORM.visit_date >= payload.week_start,
                    VisitORM.visit_date <= week_end,
                    VisitORM.deleted_at.is_(None),
                )
            )
        ).all()
    )

    if len(visits_db) > _MAX_VISITS_PER_RUN:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Too many visits for a single allocation run: "
                f"{len(visits_db)} > {_MAX_VISITS_PER_RUN}"
            ),
        )

    engine_staff, patient_map, requests = _build_inputs(patients_db, staff_db, visits_db)

    engine = AllocationEngine(
        staff_list=engine_staff,
        patient_map=patient_map,
        events=[],
        staff_changes=[],
    )

    if not requests:
        return AllocateResponse(
            week_start=payload.week_start,
            summary=AllocateSummary(
                total=0,
                assigned=0,
                unassigned=0,
                mapping_phase="minimal",
            ),
            assignments=[],
        )

    # CPU-bound engine; offload to default thread executor so the asyncio
    # event loop keeps serving other requests on the same worker. Bounded
    # by a hard timeout to surface runaway runs as 504 instead of stalling
    # the worker indefinitely.
    loop = asyncio.get_running_loop()
    try:
        out = await asyncio.wait_for(
            loop.run_in_executor(None, engine.allocate, requests),
            timeout=_ALLOCATE_TIMEOUT_SEC,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Allocation timed out (>{_ALLOCATE_TIMEOUT_SEC:.0f}s)",
        ) from exc

    results = out.get("results", [])

    items = [AssignmentItem(**asdict(r)) for r in results]
    assigned = sum(1 for r in results if r.staff_id and not r.is_event)
    unassigned = sum(1 for r in results if not r.staff_id and not r.is_event)

    return AllocateResponse(
        week_start=payload.week_start,
        summary=AllocateSummary(
            total=len(results),
            assigned=assigned,
            unassigned=unassigned,
            mapping_phase="minimal",
        ),
        assignments=items,
    )
