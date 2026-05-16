"""Schedule v2 endpoints (Wave 41 / auto-schedule v2.0).

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

実装エンドポイント:
    - POST /api/v1/schedule/v2/diff-add        (機能 A: 差分追加)
    - POST /api/v1/schedule/v2/full-optimize   (機能 B: 全面最適化)
    - POST /api/v1/schedule/v2/apply-individual (機能 A/B 共通: 1 件採用)
    - POST /api/v1/schedule/v2/reset-to-fixed  (機能 D: 固定枠に戻す)

RBAC:
    - 全 endpoint admin / manager のみ (staff は 403).

トランザクション:
    - 各 endpoint が 1 リクエスト = 1 TX で commit / rollback する.
    - サービス層 (``auto_allocator_v2``) は ``await db.flush()`` のみ呼ぶ.

採用フロー (§13.5):
    - 1 件ずつ採用が基本. 一括採用 endpoint は提供しない.
    - apply-individual は idempotent (同じ提案を 2 度送っても無害).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.v2.auto_schedule_v2 import (
    AutoScheduleV2ApplyIndividualRequest,
    AutoScheduleV2ApplyIndividualResponse,
    AutoScheduleV2ApplyWeekOnlyRequest,
    AutoScheduleV2ApplyWeekOnlyResponse,
    AutoScheduleV2DiffAddRequest,
    AutoScheduleV2DiffAddResponse,
    AutoScheduleV2FullOptimizeRequest,
    AutoScheduleV2FullOptimizeResponse,
    AutoScheduleV2ResetToFixedRequest,
    AutoScheduleV2ResetToFixedResponse,
    UnassignedPatient,
    V2BeforeAfterSummary,
    V2CourseContainer,
    V2CourseSummary,
    V2DiffAddProposal,
    V2IndividualProposal,
    V2KpiOverall,
    V2ProposalDelta,
    V2VisitForUI,
    V2VisitPlan,
    V2WeekdayBeforeAfter,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_END,
    LUNCH_START,
    V2Visit,
    apply_individual_proposal,
    apply_week_only,
    calc_h_violations,
    calc_total_distance,
    haversine_km,
    reset_visits_to_fixed,
    run_v2_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers — V2Visit → V2VisitPlan dict
# ---------------------------------------------------------------------------


def _v2visit_to_plan(v: V2Visit) -> V2VisitPlan:
    return V2VisitPlan(
        weekday=v.weekday,
        start_time=v.start_time,
        end_time=v.end_time,
        duration_min=v.service_minutes,
        course_code=v.course_code or "M",
        office_id=v.office_id,
        am_pm=v.am_pm,
        assigned_staff_id=v.assigned_staff_id,
    )


def _v2visit_to_ui(v: V2Visit) -> V2VisitForUI:
    """1 visit を UI 表示用の ``V2VisitForUI`` に変換.

    W41 v2 final cross-review (M-Codex-2): 旧 ``_v2visit_to_dict`` は untyped dict
    を返していたが、``V2CourseContainer`` を型付き化したため Pydantic model を返す.

    W41 v2 (Mode 2 UI 拡張): ``V2Visit.address`` / ``V2Visit.area_label`` を
    そのまま流す (auto_allocator_v2 が build 時に Patient.address から抽出済).
    """
    return V2VisitForUI(
        patient_id=v.patient_id,
        patient_name=v.patient_name,
        patient_code=v.patient_code,
        start_time=v.start_time.strftime("%H:%M"),
        end_time=v.end_time.strftime("%H:%M"),
        duration_min=v.service_minutes,
        am_pm=v.am_pm,
        address=v.address,
        area_label=v.area_label,
    )


def _build_kpi_overall(
    before: list[V2Visit],
    after: list[V2Visit],
    *,
    warnings: list[str],
) -> V2KpiOverall:
    bd = calc_total_distance(before)
    ad = calc_total_distance(after)
    reduction = ((bd - ad) / bd * 100.0) if bd > 0 else 0.0
    courses_before = len({(v.office_id, v.weekday, v.course_code) for v in before if v.course_code})
    courses_after = len({(v.office_id, v.weekday, v.course_code) for v in after if v.course_code})
    h_viol = calc_h_violations(after)
    overflows = sum(1 for w in warnings if "超過" in w or "exceeds" in w)
    return V2KpiOverall(
        total_distance_km_before=round(bd, 4),
        total_distance_km_after=round(ad, 4),
        distance_reduction_pct=round(reduction, 2),
        courses_count_before=courses_before,
        courses_count_after=courses_after,
        capacity_overflows=overflows,
        h_violations=h_viol,
    )


def _build_weekday_before_after(
    before: list[V2Visit], after: list[V2Visit]
) -> list[V2WeekdayBeforeAfter]:
    """機能 B: 曜日ごとに Before / After の courses 構造を返す."""
    out: list[V2WeekdayBeforeAfter] = []
    for wd in range(7):
        before_wd = [v for v in before if v.weekday == wd]
        after_wd = [v for v in after if v.weekday == wd]
        before_courses = _group_visits_into_courses(before_wd)
        after_courses = _group_visits_into_courses(after_wd)
        out.append(
            V2WeekdayBeforeAfter(
                weekday=wd,
                before=V2CourseContainer(courses=before_courses),
                after=V2CourseContainer(courses=after_courses),
            )
        )
    return out


def _group_visits_into_courses(visits: list[V2Visit]) -> list[V2CourseSummary]:
    """同 (office_id, weekday, course_code) を 1 コースとして集約."""
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)
    out: list[V2CourseSummary] = []
    for (office_id, _wd, code), gv in groups.items():
        sv = sorted(gv, key=lambda x: x.start_time)
        dist = 0.0
        for i in range(1, len(sv)):
            dist += haversine_km(sv[i - 1].lat, sv[i - 1].lng, sv[i].lat, sv[i].lng)
        out.append(
            V2CourseSummary(
                code=code or "M",
                office_id=office_id,
                visits=[_v2visit_to_ui(v) for v in sv],
                distance_km=round(dist, 4),
                visits_count=len(sv),
                assigned_staff_id=sv[0].assigned_staff_id,
            )
        )
    return out


def _build_individual_proposals(
    before: list[V2Visit],
    after: list[V2Visit],
) -> list[V2IndividualProposal]:
    """機能 B: 患者ごとに current_pfv / proposed_pfv の差分を返す."""
    # patient_id ごとに before / after をグルーピング
    before_by_pid: dict[UUID, list[V2Visit]] = {}
    after_by_pid: dict[UUID, list[V2Visit]] = {}
    for v in before:
        before_by_pid.setdefault(v.patient_id, []).append(v)
    for v in after:
        after_by_pid.setdefault(v.patient_id, []).append(v)
    all_pids = set(before_by_pid.keys()) | set(after_by_pid.keys())
    out: list[V2IndividualProposal] = []
    for pid in all_pids:
        bv = before_by_pid.get(pid, [])
        av = after_by_pid.get(pid, [])
        # 差分なしならスキップ (idempotent な提案を提示しない)
        if bv and av:
            bv_keys = {(v.weekday, v.start_time) for v in bv}
            av_keys = {(v.weekday, v.start_time) for v in av}
            if bv_keys == av_keys:
                continue
        sample = (av or bv)[0]
        before_dist = calc_total_distance(bv)
        after_dist = calc_total_distance(av)
        delta = V2ProposalDelta(
            distance_km=round(after_dist - before_dist, 4),
            capacity=f"{len(bv)}→{len(av)}",
            course_visits_count_before=len(bv),
            course_visits_count_after=len(av),
        )
        out.append(
            V2IndividualProposal(
                proposal_id=uuid.uuid4(),
                patient_id=pid,
                patient_name=sample.patient_name,
                patient_code=sample.patient_code,
                current_pfv=[_v2visit_to_plan(v) for v in bv],
                proposed_pfv=[_v2visit_to_plan(v) for v in av],
                delta=delta,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 1) POST /schedule/v2/diff-add (機能 A: 差分追加)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/diff-add",
    response_model=AutoScheduleV2DiffAddResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2: 差分追加モード (プール患者を既存固定枠に挿入)",
)
async def diff_add_endpoint(
    payload: AutoScheduleV2DiffAddRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2DiffAddResponse:
    """機能 A: プール患者 (固定枠未登録 active) を抽出し、各 1 件ごとの提案を返す.

    本 endpoint は **read-only**: DB を変更しない. 採用は ``/apply-individual``.
    """
    try:
        result = await run_v2_pipeline(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            mode="diff_add",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    before_visits: list[V2Visit] = result["before_visits"]
    after_visits: list[V2Visit] = result["after_visits"]
    pool_visits: list[V2Visit] = result["pool_visits"]
    warnings: list[str] = result["warnings"]

    # プール患者ごとに 1 つの proposal を作る
    by_pid: dict[UUID, list[V2Visit]] = {}
    for v in pool_visits:
        by_pid.setdefault(v.patient_id, []).append(v)

    proposals: list[V2DiffAddProposal] = []
    for pid, pv in by_pid.items():
        pv_sorted = sorted(pv, key=lambda x: (x.weekday, x.start_time))
        primary = pv_sorted[0]
        # その patient の追加で当該コースの容量・距離がどう変わるか
        cc = primary.course_code or "M"
        same_course_before = [
            v
            for v in before_visits
            if v.office_id == primary.office_id
            and v.weekday == primary.weekday
            and v.course_code == cc
        ]
        same_course_after = [
            v
            for v in after_visits
            if v.office_id == primary.office_id
            and v.weekday == primary.weekday
            and v.course_code == cc
        ]
        before_dist = calc_total_distance(same_course_before)
        after_dist = calc_total_distance(same_course_after)
        proposals.append(
            V2DiffAddProposal(
                proposal_id=uuid.uuid4(),
                patient_id=pid,
                patient_name=primary.patient_name,
                patient_code=primary.patient_code,
                suggested=_v2visit_to_plan(primary),
                suggested_visits=[_v2visit_to_plan(v) for v in pv_sorted],
                before_summary=V2BeforeAfterSummary(
                    course_visits_count=len(same_course_before),
                    distance_km=round(before_dist, 4),
                ),
                after_summary=V2BeforeAfterSummary(
                    course_visits_count=len(same_course_after),
                    distance_km=round(after_dist, 4),
                ),
                delta=V2ProposalDelta(
                    distance_km=round(after_dist - before_dist, 4),
                    capacity=f"{len(same_course_before)}→{len(same_course_after)}",
                    course_visits_count_before=len(same_course_before),
                    course_visits_count_after=len(same_course_after),
                ),
            )
        )

    kpi = _build_kpi_overall(before_visits, after_visits, warnings=warnings)

    return AutoScheduleV2DiffAddResponse(
        proposal_batch_id=result["proposal_batch_id"],
        proposals=proposals,
        kpi_overall=kpi,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 2) POST /schedule/v2/full-optimize (機能 B)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/full-optimize",
    response_model=AutoScheduleV2FullOptimizeResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2: 全面最適化モード (全 active 患者で再構築提案)",
)
async def full_optimize_endpoint(
    payload: AutoScheduleV2FullOptimizeRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2FullOptimizeResponse:
    """機能 B: 全 active 患者で再構築し、週単位 Before/After + 個別提案を返す.

    本 endpoint も **read-only**.
    """
    try:
        result = await run_v2_pipeline(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            mode="full_optimize",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    before_visits: list[V2Visit] = result["before_visits"]
    after_visits: list[V2Visit] = result["after_visits"]
    warnings: list[str] = result["warnings"]

    week_proposals = _build_weekday_before_after(before_visits, after_visits)
    individual = _build_individual_proposals(before_visits, after_visits)
    kpi = _build_kpi_overall(before_visits, after_visits, warnings=warnings)

    # W41 v2 (Mode 2 UI 拡張): pool に入れたが after_visits に出てこなかった患者.
    unassigned_raw = result.get("unassigned_patients", []) or []
    unassigned = [
        UnassignedPatient(
            patient_id=u["patient_id"],
            patient_name=u["patient_name"],
            patient_code=u.get("patient_code"),
            reason=u["reason"],
        )
        for u in unassigned_raw
    ]

    return AutoScheduleV2FullOptimizeResponse(
        proposal_batch_id=result["proposal_batch_id"],
        week_proposals=week_proposals,
        individual_proposals=individual,
        kpi_overall=kpi,
        warnings=warnings,
        unassigned_patients=unassigned,
    )


# ---------------------------------------------------------------------------
# 3) POST /schedule/v2/apply-individual (機能 A/B 共通)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/apply-individual",
    response_model=AutoScheduleV2ApplyIndividualResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2: 患者 1 件採用 (patient_fixed_visits を更新)",
)
async def apply_individual_endpoint(
    payload: AutoScheduleV2ApplyIndividualRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2ApplyIndividualResponse:
    """機能 A/B 共通: 1 患者の固定枠 (patient_fixed_visits) を提案で更新する.

    idempotent: 同じ提案を 2 度送っても安全 (no-op + idempotent=true).
    """
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true must be set explicitly",
        )
    if payload.patient_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient_id is required",
        )
    if not payload.visit_plans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visit_plans must not be empty",
        )

    # W41 v2 final cross-review (H-Codex-2): apply 境界での最低限の再検証.
    # クライアントが偽の visit_plans を送る攻撃に対して明らかな違反を弾く.
    # service 層は信頼境界の内側なので weekday / duration_min / start_time の
    # 範囲は Pydantic schema が既に担保しているが、H10 (昼休憩 12:00-13:00 内禁止)
    # は schema にエンコードされていないためここで明示的にチェックする.
    for idx, vp in enumerate(payload.visit_plans):
        # H10: 12:00-13:00 の昼休憩枠に visit を入れない.
        # visit 区間 [start_time, end_time) が [12:00, 13:00) と重なる場合は弾く.
        if vp.start_time < LUNCH_END and vp.end_time > LUNCH_START:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"visit_plans[{idx}]: H10 違反 — 昼休憩 12:00-13:00 に重なる "
                    f"visit は不可 (start={vp.start_time}, end={vp.end_time})"
                ),
            )
        # end_time > start_time (Pydantic では検証されない 0 / 負時間ガード)
        if vp.end_time <= vp.start_time:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"visit_plans[{idx}]: end_time は start_time より後にしてください "
                    f"(start={vp.start_time}, end={vp.end_time})"
                ),
            )

    # Pydantic model → dict
    plans = [vp.model_dump() for vp in payload.visit_plans]
    try:
        result = await apply_individual_proposal(
            db, patient_id=payload.patient_id, visit_plans=plans
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        # H-Codex-1 (W41 v2 final cross-review): 既存 PFV が無い患者では
        # ``with_for_update()`` が 0 行ロックになり、同時初回 apply が
        # 両方とも UPSERT 経路に入って UNIQUE 違反 (23505) になりうる.
        # 409 に変換して UI 側でリトライを促す.
        await db.rollback()
        logger.warning(
            "apply_individual: integrity error (likely concurrent apply): patient=%s err=%s",
            payload.patient_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ患者を採用中です。もう一度実行してください。",
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    return AutoScheduleV2ApplyIndividualResponse(
        patient_id=result["patient_id"],
        applied=bool(result["applied"]),
        fixed_visit_ids=result["fixed_visit_ids"],
        idempotent=bool(result.get("idempotent", False)),
        warnings=list(result.get("warnings", [])),
    )


# ---------------------------------------------------------------------------
# 4) POST /schedule/v2/reset-to-fixed (機能 D)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/reset-to-fixed",
    response_model=AutoScheduleV2ResetToFixedResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2: 対象週の visits を patient_fixed_visits から再生成",
)
async def reset_to_fixed_endpoint(
    payload: AutoScheduleV2ResetToFixedRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2ResetToFixedResponse:
    """機能 D: 対象週の visits を soft-delete → patient_fixed_visits から再生成.

    確認ダイアログは Frontend 側で実施する契約 (ここまで来たら確定 / ``confirm=true`` 必須).
    """
    # W41 v2 final cross-review (M-Codex-1): confirm=True は Literal[True] で
    # schema 段階で検証されるが、defense-in-depth で endpoint でも明示的に確認.
    if payload.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true must be set explicitly",
        )
    try:
        result = await reset_visits_to_fixed(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        # C-Claude-1 (W41 v2 final cross-review): with_for_update() でも 0 行ロック
        # の race は防げないため、UNIQUE 違反等の IntegrityError は 409 へ.
        await db.rollback()
        logger.warning(
            "reset_to_fixed: integrity error (likely concurrent reset): "
            "iso_year=%s iso_week=%s err=%s",
            payload.iso_year,
            payload.iso_week,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    return AutoScheduleV2ResetToFixedResponse(
        visits_regenerated=int(result.get("visits_regenerated", 0)),
        visits_soft_deleted=int(result.get("visits_soft_deleted", 0)),
        courses_used=int(result.get("courses_used", 0)),
        warnings=list(result.get("warnings", [])),
    )


# ---------------------------------------------------------------------------
# 5) POST /schedule/v2/apply-week-only (この週だけ反映)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/apply-week-only",
    response_model=AutoScheduleV2ApplyWeekOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2: 全面最適化結果をその週の visits だけに反映 (固定枠は変更しない)",
)
async def apply_week_only_endpoint(
    payload: AutoScheduleV2ApplyWeekOnlyRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2ApplyWeekOnlyResponse:
    """全面最適化提案を visits のみへ反映する慎重モード.

    - ``patient_fixed_visits`` は **更新しない**
    - 来週からは元の固定枠ベースのスケジュールに戻る
    - 対象週の active visits は soft-delete してから INSERT (uq_visits 衝突回避)
    """
    if payload.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true must be set explicitly",
        )
    if not payload.visit_plans_per_patient:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visit_plans_per_patient must not be empty",
        )

    # H10 を境界で再検証 (apply-individual と同じ防衛深度).
    for pi, pvp in enumerate(payload.visit_plans_per_patient):
        for vi, vp in enumerate(pvp.visit_plans):
            if vp.start_time < LUNCH_END and vp.end_time > LUNCH_START:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"visit_plans_per_patient[{pi}].visit_plans[{vi}]: "
                        f"H10 違反 — 昼休憩 12:00-13:00 に重なる visit は不可 "
                        f"(start={vp.start_time}, end={vp.end_time})"
                    ),
                )
            if vp.end_time <= vp.start_time:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"visit_plans_per_patient[{pi}].visit_plans[{vi}]: "
                        f"end_time は start_time より後にしてください "
                        f"(start={vp.start_time}, end={vp.end_time})"
                    ),
                )

    patient_visit_plans = [
        {
            "patient_id": pvp.patient_id,
            "visit_plans": [vp.model_dump() for vp in pvp.visit_plans],
        }
        for pvp in payload.visit_plans_per_patient
    ]
    try:
        result = await apply_week_only(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            patient_visit_plans=patient_visit_plans,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "apply_week_only: integrity error (likely concurrent apply): "
            "iso_year=%s iso_week=%s err=%s",
            payload.iso_year,
            payload.iso_week,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    return AutoScheduleV2ApplyWeekOnlyResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        visits_created=int(result.get("visits_created", 0)),
        visits_soft_deleted=int(result.get("visits_soft_deleted", 0)),
        courses_created=int(result.get("courses_created", 0)),
        visit_staff_assignments_created=int(result.get("visit_staff_assignments_created", 0)),
        warnings=list(result.get("warnings", [])),
    )


__all__ = ["router"]
