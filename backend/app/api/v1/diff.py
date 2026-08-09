"""Diff endpoint — compute correction list between current/optimized CSV.

Phase 4-6/4-7/4-16 (W1-E). Pure compute is delegated to
:mod:`app.services.diff`; this router handles auth, payload validation,
and translating engine errors to HTTP 422.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.deps import require_role
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.diff import DiffEdit, DiffRequest, DiffResponse, DiffSummary
from app.services.diff import compare_schedules_from_content

router = APIRouter()


@router.post(
    "/compute",
    response_model=DiffResponse,
    summary="Compute correction list between current and optimized CSV",
)
@limiter.limit("5/minute")
async def compute_diff(
    request: Request,
    payload: DiffRequest,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> DiffResponse:
    try:
        corrections = compare_schedules_from_content(
            current_content=payload.current_csv,
            optimized_content=payload.optimized_csv,
            target_users=payload.target_users,
            target_week_start=payload.target_week_start,
            target_week_end=payload.target_week_end,
        )
    except (ValueError, csv.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid CSV payload: {exc}",
        ) from exc

    edits = [DiffEdit(**asdict(c)) for c in corrections]

    summary = DiffSummary(
        total=len(corrections),
        time_changes=sum(1 for c in corrections if c.has_time_change()),
        staff_changes=sum(1 for c in corrections if c.has_staff_change()),
        date_changes=sum(1 for c in corrections if c.has_date_change()),
        additions=sum(1 for c in corrections if c.action == "add"),
        deletions=sum(1 for c in corrections if c.action == "delete"),
    )
    return DiffResponse(summary=summary, edits=edits)
