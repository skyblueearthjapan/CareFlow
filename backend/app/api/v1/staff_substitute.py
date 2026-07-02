"""P3-① 当日欠勤の代替スタッフ提案 API (admin/manager).

設計書: docs/plans/p3-1-staff-substitute-design.md §1

エンドポイント:
    - GET  /api/v1/schedule/staff-substitute/candidates?absent_staff_id&target_date
      欠勤スタッフの当日 planned visit ごとに代替候補をランキングして返す (read-only).
    - POST /api/v1/schedule/staff-substitute/apply
      差替えを all-or-nothing で適用する (N-4 再検証 → VSA 差替え + 同期).

RBAC: 全 endpoint admin / manager のみ (staff は 403).
トランザクション: POST は 1 リクエスト = 1 TX. サービス層は flush のみ、router が commit.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.v2.staff_substitute import (
    SubstituteApplyRequest,
    SubstituteApplyResponse,
    SubstituteCandidatesResponse,
)
from app.services.scheduling.config import load_scheduling_config
from app.services.scheduling.staff_substitute import (
    StaffSubstituteError,
    apply_substitutions,
    find_substitute_candidates,
)

router = APIRouter()


@router.get(
    "/staff-substitute/candidates",
    response_model=SubstituteCandidatesResponse,
    summary="欠勤スタッフの当日 visit 代替候補を列挙 (read-only)",
)
async def get_substitute_candidates(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    absent_staff_id: Annotated[UUID, Query(description="欠勤スタッフ ID")],
    target_date: Annotated[date, Query(description="対象日 (YYYY-MM-DD)")],
) -> SubstituteCandidatesResponse:
    config = await load_scheduling_config(db)
    try:
        return await find_substitute_candidates(
            db, absent_staff_id, target_date, config=config
        )
    except StaffSubstituteError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post(
    "/staff-substitute/apply",
    response_model=SubstituteApplyResponse,
    summary="代替スタッフ差替えを all-or-nothing で適用",
)
async def post_substitute_apply(
    db: DbDep,
    payload: SubstituteApplyRequest,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> SubstituteApplyResponse:
    config = await load_scheduling_config(db)
    try:
        result = await apply_substitutions(db, payload, config=config)
    except StaffSubstituteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    await db.commit()
    return result
