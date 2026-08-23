"""急休の代替候補 / 「担当なし」への投入提案 API (read-only / admin のみ).

正典 = ``docs/plans/week-cockpit-design.md`` §2-1 (Phase E / BE-1) と
``docs/plans/unassigned-suggestions-design.md`` §2 (Phase 2-A)。

    POST /api/v1/schedule/v2/substitute-candidates
    POST /api/v1/schedule/v2/assign-candidates

「この人がこの日休む」を入力に、その日の担当訪問をコース単位で束ね、束ごとに
代替候補を ◎(ok) / △(warn) / ×(ng) + 理由 + score で返す。**DB は変更しない**。
付替の実行は既存 API が担う:
    - コース丸ごと: ``PATCH /api/v1/courses/{course_id}`` (assigned_staff_id)
    - 訪問 1 件  : ``POST /api/v1/schedule/v2/visit-assign-staff-week``
    - 休みの登録 : ``POST /api/v1/staff/{staff_id}/overrides``
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.substitute import (
    AssignCandidatesRequest,
    SubstituteCandidatesRequest,
    SubstituteCandidatesResponse,
)
from app.services.scheduling.substitute_candidates import (
    SubstituteCandidatesError,
    build_assign_candidates,
    build_substitute_candidates,
)

router = APIRouter()


@router.post(
    "/v2/substitute-candidates",
    response_model=SubstituteCandidatesResponse,
    status_code=status.HTTP_200_OK,
    summary="週空間 Phase E: 急休の代替候補 (read-only)",
)
async def substitute_candidates(
    payload: SubstituteCandidatesRequest,
    db: DbDep,
    _current_user: Annotated[User, Depends(require_role("admin"))],
) -> SubstituteCandidatesResponse:
    """指定スタッフが指定日に抜けたときの代替候補をコース単位で返す.

    判定 (設計書 §2-1):
        - ok   (◎) = ハード制約すべて OK かつ 時間重なり無し
        - warn (△) = 時間重なり / イベント重なり **のみ**
        - ng   (×) = 休み・非勤務日・NG スタッフ・性別・新人・拠点不可
    整列は status (ok→warn→ng) → score desc。対象スタッフ自身と「（担当なし）」は
    候補に含めない。cancelled / deleted 訪問は対象外。
    """
    try:
        return await build_substitute_candidates(
            db,
            staff_id=payload.staff_id,
            target_date=payload.date,
            course_id=payload.course_id,
        )
    except SubstituteCandidatesError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post(
    "/v2/assign-candidates",
    response_model=SubstituteCandidatesResponse,
    status_code=status.HTTP_200_OK,
    summary="「担当なし」への投入提案 (read-only)",
)
async def assign_candidates(
    payload: AssignCandidatesRequest,
    db: DbDep,
    _current_user: Annotated[User, Depends(require_role("admin"))],
) -> SubstituteCandidatesResponse:
    """指定した訪問 (コース丸ごと / 複数コース / 訪問 ID 指定) を誰に入れられるかを返す.

    正典 = ``docs/plans/unassigned-suggestions-design.md`` §2。急休 API の逆操作で、
    **抜けるスタッフは居ない** = 候補は在籍 active 全員 (新人は ×)。判定・整列規則は
    ``substitute-candidates`` と完全に同一 (同じ関数)。

    - ``whole_ok_staff_ids`` = 全対象訪問で ◎ だった人の交差 (= 全部まとめて 1 人で
      引き受けられる人)。対象訪問どうしが時間的にぶつかる場合は空。
    - ``whole_ok_by_course`` = コースごとの同じ交差 (``course_ids`` で複数コースを
      1 回で見積もるときの本命)。

    ``course_id`` / ``course_ids`` (最大 50) / ``visit_ids`` (最大 200) は **いずれか
    1 つだけ** 指定する (複数指定 / 全部なしは 422)。存在しないコース・訪問は 404、
    その日の planned でない訪問は 422。
    """
    try:
        return await build_assign_candidates(
            db,
            target_date=payload.date,
            course_id=payload.course_id,
            course_ids=payload.course_ids,
            visit_ids=payload.visit_ids,
        )
    except SubstituteCandidatesError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
