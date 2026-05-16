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
from datetime import time as time_cls
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
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
    UpdateFixedTimeMasterRequest,
    UpdateFixedTimeMasterResponse,
    UpdateFixedTimeWeekOnlyRequest,
    UpdateFixedTimeWeekOnlyResponse,
    V2BeforeAfterSummary,
    V2CourseContainer,
    V2CourseSummary,
    V2DiffAddProposal,
    V2IndividualProposal,
    V2KpiOverall,
    V2ProposalDelta,
    V2VisitForUI,
    V2VisitPlan,
    V2WarningOut,
    V2WeekdayBeforeAfter,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_END,
    LUNCH_START,
    V2Visit,
    V2Warning,
    _address_bucket,
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


def _warning_to_out(w: V2Warning) -> V2WarningOut:
    """``V2Warning`` (dataclass) を Pydantic ``V2WarningOut`` に変換."""
    return V2WarningOut(
        type=w.type,
        message=w.message,
        weekday=w.weekday,
        actionable=w.actionable,
        patient_id=w.patient_id,
        patient_name=w.patient_name,
        visit_id=w.visit_id,
        current_time=w.current_time,
        suggested_time=w.suggested_time,
        time_type=w.time_type,
        preferred_start=w.preferred_start,
        preferred_end=w.preferred_end,
    )


def _v2visit_to_ui(
    v: V2Visit,
    *,
    same_address_group_id: str | None = None,
) -> V2VisitForUI:
    """1 visit を UI 表示用の ``V2VisitForUI`` に変換.

    W41 v2 final cross-review (M-Codex-2): 旧 ``_v2visit_to_dict`` は untyped dict
    を返していたが、``V2CourseContainer`` を型付き化したため Pydantic model を返す.

    W41 v2 (Mode 2 UI 拡張): ``V2Visit.address`` / ``V2Visit.area_label`` を
    そのまま流す (auto_allocator_v2 が build 時に Patient.address から抽出済).

    W41 v2 (Mode 2 Before/After 表示拡張): ``time_type`` / ``sex_restriction`` も流す.

    W41 v2 (H2 視覚化): ``same_address_group_id`` を引数で受け取り埋める.
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
        time_type=v.time_type,
        sex_restriction=v.sex_restriction,
        same_address_group_id=same_address_group_id,
        preferred_start=v.preferred_start,
        preferred_end=v.preferred_end,
        distance_to_next_km=v.distance_to_next_km,
    )


def _assign_same_address_groups(
    visits: list[V2Visit],
) -> dict[tuple[UUID, int, time_cls], str]:
    """同 (office, weekday, start_time, address_bucket) で 2 名以上の場合に group_id を割当.

    W41 v2 (H2 視覚化): UI で「📍 同住所 (N 名)」を表示するための group_id を計算する.
    Returns:
        ``{(patient_id, weekday, start_time): group_id_str}`` の辞書.
        該当しない visit はキーに含まれない.
    """
    from collections import defaultdict

    bucket_to_visits: dict[tuple[UUID, int, time_cls, tuple[float, float]], list[V2Visit]] = (
        defaultdict(list)
    )
    for v in visits:
        if v.lat is None or v.lng is None:
            continue
        bucket = _address_bucket(v.lat, v.lng)
        key = (v.office_id, v.weekday, v.start_time, bucket)
        bucket_to_visits[key].append(v)

    group_id_by_key: dict[tuple[UUID, int, time_cls], str] = {}
    for key, vs in bucket_to_visits.items():
        if len(vs) >= 2:
            office_id, wd, st, (lat_b, lng_b) = key
            gid = f"sa_{office_id}_{wd}_{st.strftime('%H%M')}_{lat_b:.4f}_{lng_b:.4f}"
            for v in vs:
                visit_key = (v.patient_id, wd, st)
                group_id_by_key[visit_key] = gid
    return group_id_by_key


def _build_kpi_overall(
    before: list[V2Visit],
    after: list[V2Visit],
    *,
    warnings: list[V2Warning],
) -> V2KpiOverall:
    bd = calc_total_distance(before)
    ad = calc_total_distance(after)
    reduction = ((bd - ad) / bd * 100.0) if bd > 0 else 0.0
    courses_before = len({(v.office_id, v.weekday, v.course_code) for v in before if v.course_code})
    courses_after = len({(v.office_id, v.weekday, v.course_code) for v in after if v.course_code})
    h_viol = calc_h_violations(after)
    # W41 v2 (警告日本語化): warning に「マネージャー補充候補」が出る = 容量/コース超過.
    # 旧表現 "超過" / "exceeds" を後方互換で残す.
    overflows = sum(
        1
        for w in warnings
        if "マネージャー補充候補" in w.message or "超過" in w.message or "exceeds" in w.message
    )
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
    before: list[V2Visit],
    after: list[V2Visit],
    *,
    office_name_by_id: dict[UUID, str] | None = None,
) -> list[V2WeekdayBeforeAfter]:
    """機能 B: 曜日ごとに Before / After の courses 構造を返す.

    W41 v2 (Mode 2 Before/After 表示拡張): ``office_name_by_id`` を受け取り
    各コースに ``office_name`` をセット + (office_name, code) で ABC 順ソート.
    """
    out: list[V2WeekdayBeforeAfter] = []
    for wd in range(7):
        before_wd = [v for v in before if v.weekday == wd]
        after_wd = [v for v in after if v.weekday == wd]
        before_courses = _group_visits_into_courses(before_wd, office_name_by_id=office_name_by_id)
        after_courses = _group_visits_into_courses(after_wd, office_name_by_id=office_name_by_id)
        out.append(
            V2WeekdayBeforeAfter(
                weekday=wd,
                before=V2CourseContainer(courses=before_courses),
                after=V2CourseContainer(courses=after_courses),
            )
        )
    return out


def _group_visits_into_courses(
    visits: list[V2Visit],
    *,
    office_name_by_id: dict[UUID, str] | None = None,
) -> list[V2CourseSummary]:
    """同 (office_id, weekday, course_code) を 1 コースとして集約.

    W41 v2 (Mode 2 Before/After 表示拡張):
        - ``office_name`` を ``office_name_by_id`` から引いて埋める.
        - 戻り値を (office_name, code) で ABC 順ソート.
          本店 A → B → ... → M, 続いて 都賀 A → ... を保証する.
          office_name が未取得な場合は str(office_id) で安定ソートする.

    W41 v2 (H2 視覚化): ``same_address_group_id`` を visits 全体で計算し
    `_v2visit_to_ui` に流して UI 側で連結表示できるようにする.
    """
    # 同住所グループ id を全 visits でまず計算 (course 越境を許容)
    group_id_by_key = _assign_same_address_groups(visits)

    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)
    out: list[V2CourseSummary] = []
    for (office_id, _wd, code), gv in groups.items():
        sv = sorted(gv, key=lambda x: x.start_time)
        dist = 0.0
        for i in range(1, len(sv)):
            dist += haversine_km(sv[i - 1].lat, sv[i - 1].lng, sv[i].lat, sv[i].lng)
        # W41 v2 拡張 (訪問間距離): 隣接ペアの距離を各 visit に書き込む.
        # 最後の visit (sv[-1]) は None のまま.
        for i in range(len(sv) - 1):
            sv[i].distance_to_next_km = round(
                haversine_km(sv[i].lat, sv[i].lng, sv[i + 1].lat, sv[i + 1].lng), 4
            )
        office_name = (office_name_by_id or {}).get(office_id)
        out.append(
            V2CourseSummary(
                code=code or "M",
                office_id=office_id,
                office_name=office_name,
                visits=[
                    _v2visit_to_ui(
                        v,
                        same_address_group_id=group_id_by_key.get(
                            (v.patient_id, v.weekday, v.start_time)
                        ),
                    )
                    for v in sv
                ],
                distance_km=round(dist, 4),
                visits_count=len(sv),
                assigned_staff_id=sv[0].assigned_staff_id,
            )
        )
    out.sort(key=lambda c: (c.office_name or str(c.office_id), c.code or "Z"))
    return out


async def _load_office_name_map(db: DbDep, office_ids: set[UUID]) -> dict[UUID, str]:
    """W41 v2 (Mode 2 Before/After 表示拡張): UI ヘッダー用 office.name の lookup を作る."""
    if not office_ids:
        return {}
    rows = await db.scalars(select(Office).where(Office.id.in_(office_ids)))
    return {o.id: o.name for o in rows.all()}


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
    warnings: list[V2Warning] = result["warnings"]

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
        warnings=[_warning_to_out(w) for w in warnings],
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
            pending_edits=list(payload.pending_edits),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    before_visits: list[V2Visit] = result["before_visits"]
    after_visits: list[V2Visit] = result["after_visits"]
    warnings: list[V2Warning] = result["warnings"]

    # W41 v2 (Mode 2 Before/After 表示拡張): UI ヘッダーで「拠点名 + コース名」
    # 表記するため、Before/After に含まれる office_id 集合を 1 度だけ name に解決.
    office_ids_in_use: set[UUID] = {v.office_id for v in before_visits} | {
        v.office_id for v in after_visits
    }
    office_name_by_id = await _load_office_name_map(db, office_ids_in_use)

    week_proposals = _build_weekday_before_after(
        before_visits, after_visits, office_name_by_id=office_name_by_id
    )
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
        warnings=[_warning_to_out(w) for w in warnings],
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
            pending_edits=list(payload.pending_edits),
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


# ---------------------------------------------------------------------------
# 6) POST /schedule/v2/update-fixed-time-master (W41 v2 拡張: 警告アクション)
# ---------------------------------------------------------------------------


def _parse_hhmm_loose(value: str) -> time_cls | None:
    """ "HH:MM" or "HH:MM:SS" を ``time`` に変換. 失敗時は None."""
    parts = value.split(":")
    if len(parts) < 2 or len(parts) > 3:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return None
    return time_cls(h, m, s)


_TIME_TYPE_TO_AM_PM: dict[str, str] = {
    "午前": "am",
    "午後": "pm",
    "終日": "any",
}


def _update_weekly_pattern_entry(
    pattern: dict[str, object] | None,
    *,
    weekday: int,
    new_start: str,
    new_end: str | None,
    new_time_type: str | None,
) -> dict[str, object]:
    """``patient.weekly_pattern`` の entries[weekday] を更新する.

    リスト形式 (entries) を優先. entries が無い場合は新規に構築する.
    weekday は int (0=月..6=日) で書き込む.
    """
    pat: dict[str, object] = dict(pattern) if isinstance(pattern, dict) else {}
    entries_raw = pat.get("entries")
    entries: list[dict[str, object]]
    if isinstance(entries_raw, list):
        entries = [dict(e) if isinstance(e, dict) else {} for e in entries_raw]
    else:
        entries = []

    weekday_codes = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    target_code = weekday_codes.get(weekday)

    target_index: int | None = None
    for i, e in enumerate(entries):
        ew = e.get("weekday")
        if ew == weekday or ew == target_code:
            target_index = i
            break

    new_entry: dict[str, object] = {"weekday": weekday, "preferred_start": new_start}
    if new_end:
        new_entry["preferred_end"] = new_end
    if new_time_type:
        new_entry["time_type"] = new_time_type

    if target_index is not None:
        existing = entries[target_index]
        existing["preferred_start"] = new_start
        if new_end:
            existing["preferred_end"] = new_end
        elif "preferred_end" in existing and not new_end:
            # new_end=None は末端の上書きしないという意図. 既存値は触らない.
            pass
        if new_time_type:
            existing["time_type"] = new_time_type
        entries[target_index] = existing
    else:
        entries.append(new_entry)

    pat["entries"] = entries
    return pat


@router.post(
    "/v2/update-fixed-time-master",
    response_model=UpdateFixedTimeMasterResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2 拡張: 患者の固定時間マスター (PFV + weekly_pattern) を更新",
)
async def update_fixed_time_master_endpoint(
    payload: UpdateFixedTimeMasterRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> UpdateFixedTimeMasterResponse:
    """同住所集約警告の「マスター更新」アクション用エンドポイント.

    対象:
      - ``patient_fixed_visits (mode='normal', slot_index=0)`` の start_time / duration_min
      - ``patient.weekly_pattern.entries[weekday]`` の preferred_start / preferred_end / time_type

    PFV が無い場合は新規 INSERT, ある場合は UPDATE.
    """
    start_t = _parse_hhmm_loose(payload.new_start)
    if start_t is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"new_start は HH:MM 形式が必要 (received: {payload.new_start!r})",
        )
    end_t: time_cls | None = None
    if payload.new_end:
        end_t = _parse_hhmm_loose(payload.new_end)
        if end_t is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"new_end は HH:MM 形式が必要 (received: {payload.new_end!r})",
            )
        if end_t <= start_t:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="new_end は new_start より後にしてください",
            )
    try:
        # 患者存在チェック + 行ロック
        patient_row = await db.scalar(
            select(Patient)
            .where(Patient.id == payload.patient_id, Patient.deleted_at.is_(None))
            .with_for_update()
        )
        if patient_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"patient_id={payload.patient_id} が見つかりません",
            )

        # PFV の取得 (FOR UPDATE) — 無い場合は新規 INSERT
        pfv_row = await db.scalar(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == payload.patient_id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.weekday == payload.weekday,
                PatientFixedVisit.slot_index == 0,
            )
            .with_for_update()
        )
        # duration_min の算出.
        #   - time_type='固定' (またはレンジなし): new_end - new_start を実訪問時間とみなす
        #   - time_type='時間帯' / '午前' / '午後' / '終日': new_start..new_end は希望レンジで
        #     あって実訪問時間ではないため、既存 duration を保持する (無ければ 30 分).
        is_range_type = payload.new_time_type in ("時間帯", "午前", "午後", "終日")
        if is_range_type:
            duration_min = pfv_row.duration_min if pfv_row is not None else 30
        elif end_t is not None:
            duration_min = (end_t.hour * 60 + end_t.minute) - (start_t.hour * 60 + start_t.minute)
        elif pfv_row is not None:
            duration_min = pfv_row.duration_min
        else:
            duration_min = 30
        if duration_min <= 0 or duration_min > 480:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"duration_min が範囲外です (computed={duration_min})",
            )

        # H10: 12:00-13:00 の昼休憩重複を弾く.
        # time_type='時間帯' などレンジ指定の場合、new_start..new_end は希望範囲であって
        # 実訪問時間ではないので H10 をスキップする (実訪問は duration_min 分のどこかで行う).
        if not is_range_type:
            if end_t is not None:
                actual_end_t = end_t
            else:
                end_total = start_t.hour * 60 + start_t.minute + duration_min
                if end_total >= 24 * 60:
                    end_total = 23 * 60 + 59
                actual_end_t = time_cls(end_total // 60, end_total % 60)
            if start_t < LUNCH_END and actual_end_t > LUNCH_START:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="H10 違反: 昼休憩 12:00-13:00 に重なる時間は指定できません",
                )

        if pfv_row is None:
            pfv_row = PatientFixedVisit(
                patient_id=payload.patient_id,
                mode="normal",
                weekday=payload.weekday,
                start_time=start_t,
                duration_min=duration_min,
                slot_index=0,
            )
            db.add(pfv_row)
        else:
            pfv_row.start_time = start_t
            pfv_row.duration_min = duration_min

        # patient.weekly_pattern も更新
        patient_row.weekly_pattern = _update_weekly_pattern_entry(
            patient_row.weekly_pattern if isinstance(patient_row.weekly_pattern, dict) else None,
            weekday=payload.weekday,
            new_start=payload.new_start,
            new_end=payload.new_end,
            new_time_type=payload.new_time_type,
        )
        await db.flush()
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("update_fixed_time_master: integrity error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ固定枠を更新中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    return UpdateFixedTimeMasterResponse(
        updated=True,
        patient_id=payload.patient_id,
        weekday=payload.weekday,
    )


# ---------------------------------------------------------------------------
# 7) POST /schedule/v2/update-fixed-time-week-only (W41 v2 拡張)
# ---------------------------------------------------------------------------

# update-fixed-time-week-only は自動算出由来の visit のみ許可する.
# 手動作成 / インポート系の visit を保護するため source プレフィックスで判定.
_WEEK_ONLY_ALLOWED_SOURCE_PREFIX: str = "auto_alloc_v2"


@router.post(
    "/v2/update-fixed-time-week-only",
    response_model=UpdateFixedTimeWeekOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="W41 v2 拡張: 提案中 visit の時刻を 1 件だけ上書き (マスターは触らない)",
)
async def update_fixed_time_week_only_endpoint(
    payload: UpdateFixedTimeWeekOnlyRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> UpdateFixedTimeWeekOnlyResponse:
    """提案中 visit の start_time / end_time を 1 件だけ上書きする.

    対象 visit は ``source`` が ``auto_alloc_v2*`` で始まり、``status='planned'``
    かつ ``deleted_at IS NULL`` であること. 本番運用 visits を変更しないための保護.
    """
    start_t = _parse_hhmm_loose(payload.new_start)
    if start_t is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"new_start は HH:MM 形式が必要 (received: {payload.new_start!r})",
        )
    end_t: time_cls | None = None
    if payload.new_end:
        end_t = _parse_hhmm_loose(payload.new_end)
        if end_t is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"new_end は HH:MM 形式が必要 (received: {payload.new_end!r})",
            )
        if end_t <= start_t:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="new_end は new_start より後にしてください",
            )

    try:
        visit_row = await db.scalar(
            select(Visit)
            .where(Visit.id == payload.visit_id, Visit.deleted_at.is_(None))
            .with_for_update()
        )
        if visit_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"visit_id={payload.visit_id} が見つかりません",
            )
        # source: auto_alloc_v2 系のみ許可 (本番運用 visit を保護)
        if not visit_row.source.startswith(_WEEK_ONLY_ALLOWED_SOURCE_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "この visit は自動算出由来ではないため週限定変更できません "
                    f"(source={visit_row.source!r})."
                ),
            )
        # status: planned のみ許可 (進行中/完了済み visit を保護)
        if visit_row.status != VISIT_STATUS_PLANNED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "この visit は計画状態ではないため変更できません "
                    f"(status={visit_row.status!r})."
                ),
            )
        # H10: 12:00-13:00 と重複してはならない
        effective_end = end_t or visit_row.end_time
        if start_t < LUNCH_END and effective_end > LUNCH_START:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="H10 違反: 昼休憩 12:00-13:00 に重なる時間は指定できません",
            )
        # 旧 start から duration を計算 (end_t 未指定時は duration を保つ).
        prev_start = visit_row.start_time
        prev_end = visit_row.end_time
        prev_duration_min = (prev_end.hour * 60 + prev_end.minute) - (
            prev_start.hour * 60 + prev_start.minute
        )
        visit_row.start_time = start_t
        if end_t is not None:
            visit_row.end_time = end_t
        elif prev_duration_min > 0:
            # 元の duration を保ったまま start_time だけずらす.
            new_end_total = start_t.hour * 60 + start_t.minute + prev_duration_min
            if new_end_total >= 24 * 60:
                new_end_total = 23 * 60 + 59
            visit_row.end_time = time_cls(new_end_total // 60, new_end_total % 60)
        await db.flush()
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("update_fixed_time_week_only: integrity error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ visit を更新中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    return UpdateFixedTimeWeekOnlyResponse(updated=True, visit_id=payload.visit_id)


__all__ = ["router"]
