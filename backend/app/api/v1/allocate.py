"""Allocate endpoint — run the allocation engine for a given ISO week.

Phase W1-D (continuation). Loads the relevant patients/staff/visits/shifts
from the DB, builds engine inputs, and returns the AssignmentResult list as
:class:`AllocateResponse`. Pure-compute is delegated to
:mod:`app.services.allocation`; this router handles auth, payload validation,
and DB orchestration.

The current slice intentionally returns an empty assignment list when no
visit requests exist for the week — this is the smallest viable wiring of
the engine into the HTTP surface. Richer DB→model mapping lands in W1-G.
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


def _build_inputs(
    patients_db: list[PatientORM],
    staff_db: list[StaffORM],
    visits_db: list[VisitORM],
) -> tuple[list[Staff], dict[str, Patient], list[VisitRequest]]:
    """Map ORM rows to the engine's pure dataclasses.

    Minimal mapping for the W1-D continuation slice — only the fields the
    engine actually reads at the entry point are populated. Additional
    fields (weekly patterns, NG staff, etc.) are wired in W1-G when the
    JSONB columns get their own marshallers.
    """
    patient_map: dict[str, Patient] = {}
    for p in patients_db:
        pid = str(p.id)
        patient_map[pid] = Patient(
            pid=pid,
            name=p.name,
            area="",
            lat=float(p.lat) if p.lat is not None else None,
            lng=float(p.lng) if p.lng is not None else None,
            need_staff=p.required_staff_count,
            sex_limit=p.sex_restriction or "",
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
        requests.append(
            VisitRequest(
                request_id=str(v.id),
                date_str=date_str,
                weekday=weekday,
                pid=str(v.patient_id),
                pname=patient_map.get(str(v.patient_id), Patient(pid="", name="")).name,
                start_min=start_min,
                end_min=end_min,
                service_min=service_min,
                need_staff=1,
                time_type="固定",
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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
    staff_db = list(
        (await db.scalars(select(StaffORM).where(StaffORM.deleted_at.is_(None)))).all()
    )
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

    engine_staff, patient_map, requests = _build_inputs(
        patients_db, staff_db, visits_db
    )

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
    except asyncio.TimeoutError as exc:
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
