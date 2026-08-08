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
from datetime import UTC, date, datetime, timedelta
from datetime import time as time_cls
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.user import User
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit
from app.schemas.v2.auto_schedule_v2 import (
    ApplyIndividualWeekSync,
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
    AutoScheduleV2SyncFixedToWeekRequest,
    AutoScheduleV2SyncFixedToWeekResponse,
    AutoScheduleV2UnassignAllRequest,
    AutoScheduleV2UnassignAllResponse,
    PfvCoursePresenceItem,
    PfvCoursePresenceResponse,
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
    VisitMoveWeekOnlyRequest,
    VisitMoveWeekOnlyResponse,
    VisitWeekPinBulkRequest,
    VisitWeekPinBulkResponse,
    VisitWeekPinRequest,
    VisitWeekPinResponse,
    WeekdayStaffCapacityItem,
    WeekdayStaffCapacityResponse,
)
from app.schemas.v2.board import (
    BoardCapacity,
    BoardCell,
    BoardCourse,
    BoardOffice,
    BoardResponse,
    BoardVisit,
    BoardWeekday,
)
from app.schemas.v2.improvement_suggestion import (
    ApplySwapRequest,
    ApplySwapResponse,
    CourseSnapshotEvent,
    ImprovementCandidateSlot,
    ImprovementChanges,
    ImprovementCurrentSlot,
    ImprovementDelta,
    ImprovementDismissRequest,
    ImprovementDismissResponse,
    ImprovementFilteredSummary,
    ImprovementSuggestion,
    ImprovementSuggestionsResponse,
    SwapCounterpart,
    SwapWeekSync,
)
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base
from app.schemas.v2.pool_bulk import (
    PoolBulkApplyRequest,
    PoolBulkApplyResponse,
    PoolBulkKpi,
    PoolBulkPartial,
    PoolBulkPlacement,
    PoolBulkSimulateRequest,
    PoolBulkSimulateResponse,
    PoolBulkUnplaced,
)
from app.schemas.v2.pool_overview import (
    PoolOverviewBestSlot,
    PoolOverviewItem,
    PoolOverviewRequest,
    PoolOverviewResponse,
)
from app.schemas.v2.propose_slots import (
    WEEKDAY_CODE_TO_INT,
    WEEKDAY_INT_TO_CODE,
    ProposeCoverage,
    ProposeCoverageDay,
    ProposeEventConflict,
    ProposeExcludedReason,
    ProposeMiniScheduleEntry,
    ProposeSlotItem,
    ProposeSlotsRequest,
    ProposeSlotsResponse,
    _parse_hhmm,
)
from app.schemas.v2.schedule_health import (
    ScheduleHealthCourseDetailResponse,
    ScheduleHealthResponse,
    ScheduleHealthTrendResponse,
)
from app.schemas.v2.scope_optimization import (
    ScopeCourseSnapshot,
    ScopeOptimizationApplyRequest,
    ScopeOptimizationApplyResponse,
    ScopeOptimizationCourseBeforeAfter,
    ScopeOptimizationExcludedSummary,
    ScopeOptimizationMetrics,
    ScopeOptimizationSimulateRequest,
    ScopeOptimizationSimulateResponse,
    ScopeOptimizationStep,
    ScopeOptimizationWeekSync,
    ScopeSnapshotVisit,
)
from app.schemas.v2.scope_optimization import (
    ScopeOptimizationScope as ScopeOptimizationScopeSchema,
)
from app.schemas.v2.travel_estimate import (
    TravelEstimateRequest,
    TravelEstimateResponse,
)
from app.schemas.v2.unblock import (
    ProposeUnblockApplyRequest,
    ProposeUnblockApplyResponse,
    ProposeUnblockRequest,
    ProposeUnblockResponse,
    UnblockCourseEvent,
    UnblockCourseSnapshot,
    UnblockCourseVisit,
    UnblockInsertItem,
    UnblockMoveItem,
    UnblockPlanItem,
    UnblockSlotRef,
    UnblockUnmovableSummary,
)
from app.services.geocoding.client import (
    GeocodingServiceError,
    geocode_address,
)
from app.services.office_assigner import OfficeAssigner
from app.services.op_log_service import fmt_time, fmt_weekday, record_op
from app.services.scheduling.auto_allocator_v2 import (
    _COURSE_CODES_MAX,
    # Wave 3 (#WAVE3): API 境界の H10 (lunch overlap) ガードは
    # ``_is_in_lunch_break`` (= 動的 lunch のどの 45 分配置でも避けられない visit か?)
    # で判定する. service 層 (compute_lunch_window) がコース別に lunch を
    # 11:30-12:15 / 12:30-13:30 等にずらすことで合法になる visit
    # (例: 12:15-12:45 = AM 側 11:30-12:15 lunch なら衝突しない) は
    # API 境界で 422 reject せず service 層に通す.
    # Phase G-88 最終監査: 各 apply 境界は ``config.lunch_window_start`` /
    # ``config.lunch_window_end`` を ``_is_in_lunch_break(window_start=, window_end=)``
    # に注入するため、固定の ``LUNCH_DEFAULT_*`` 参照は撤去した.
    CrossAddressTimeConflictError,
    PinnedVisitMovedError,
    V2Visit,
    V2Warning,
    _add_minutes,
    _address_bucket,
    _is_in_lunch_break,
    _load_same_address_pair_modes,
    _load_unavailable_slots,
    apply_individual_proposal,
    apply_week_only,
    calc_h_violations,
    calc_total_distance,
    count_active_managers_per_weekday,
    count_active_staff_per_weekday,
    haversine_km,
    reset_visits_to_fixed,
    resolve_reset_office_ids,
    run_v2_pipeline,
    unassign_all_staff_for_week,
)
from app.services.scheduling.board_service import (
    MAX_PATIENTS_PER_COURSE,
    BoardCourseData,
    load_board_buckets,
    load_weekday_staff_counts,
)
from app.services.scheduling.board_service import (
    _office_short as _board_office_short,
)
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config
from app.services.scheduling.improvement_engine import (
    ImprovementCandidateData,
    find_improvement_candidates,
)
from app.services.scheduling.pfv_validator import (
    _find_conflict,
    validate_pfv_changes,
)
from app.services.scheduling.pool_bulk_inserter import (
    compute_bulk_state_token,
    simulate_pool_bulk_insert,
)
from app.services.scheduling.proposal_solver import (
    VISIT_BUFFER_MINUTES,
    ExistingVisit,
    haversine_minutes,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ExcludedReasonSummary,
    ProposedSlot,
    compute_all_proposed_slots,
    compute_coverage,
    compute_overcapacity_slots,
    load_week_course_buckets,
)
from app.services.scheduling.schedule_health import (
    compute_course_detail,
    compute_schedule_health,
    compute_schedule_health_trend,
)
from app.services.scheduling.scope_optimizer import (
    OptimizationScope,
    ScopeCourseSnapshotData,
    ScopeMetricsData,
    compute_current_state_token,
    simulate_scope_optimization,
)
from app.services.scheduling.unblock_search import (
    compute_plan_id,
    search_unblock_plans,
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
    """``V2Warning`` (dataclass) を Pydantic ``V2WarningOut`` に変換.

    P2: ``affected_patient_ids`` を伝播.
    Wave 4 (Phase C): ``category`` (集約カテゴリ) を伝播.
    """
    # Wave 4 (Phase C): V2Warning.__post_init__ で category は自動解決済み.
    # Enum を string value に落としてから V2WarningCategoryOut (Literal) に渡す.
    category_value = w.category.value if w.category is not None else "conflict"
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
        affected_patient_ids=list(w.affected_patient_ids or []),
        category=category_value,
    )


def _v2visit_to_ui(
    v: V2Visit,
    *,
    same_address_group_id: str | None = None,
    warnings: list[V2WarningOut] | None = None,
) -> V2VisitForUI:
    """1 visit を UI 表示用の ``V2VisitForUI`` に変換.

    W41 v2 final cross-review (M-Codex-2): 旧 ``_v2visit_to_dict`` は untyped dict
    を返していたが、``V2CourseContainer`` を型付き化したため Pydantic model を返す.

    W41 v2 (Mode 2 UI 拡張): ``V2Visit.address`` / ``V2Visit.area_label`` を
    そのまま流す (auto_allocator_v2 が build 時に Patient.address から抽出済).

    W41 v2 (Mode 2 Before/After 表示拡張): ``time_type`` / ``sex_restriction`` も流す.

    W41 v2 (H2 視覚化): ``same_address_group_id`` を引数で受け取り埋める.

    CareFlow #101 FE: ``warnings`` (V2WarningOut のリスト) を受け取り、UI 側で
    赤枠ハイライト + ツールチップ表示の元データを埋める. 呼び出し側
    (``_group_visits_into_courses``) が当該 visit に紐づく warning を抽出する.
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
        warnings=list(warnings or []),
    )


def _assign_same_address_groups(
    visits: list[V2Visit],
) -> dict[tuple[UUID, int, time_cls], str]:
    """同 (office, weekday, address_bucket) で 2 名以上の場合に group_id を割当.

    W41 v2 (H2 視覚化): UI で「📍 同住所 (N 名)」を表示するための group_id を計算する.

    W41 v2.8 (実動時間入力でペア囲み消失問題の修正):
        旧仕様では key に ``start_time`` を含めていたため、実動時間/移動時間で
        09:00 / 09:30 のように時刻が連番にズレた瞬間に同住所判定から外れ、
        UI のペア囲みが消えていた。本修正で key から ``start_time`` を除き、
        ``(office, weekday, address_bucket)`` で同住所を判定する。
        「連番として隣り合うペアのみ囲む」というユーザー意図は FE 側の隣接判定
        (``prev/next.same_address_group_id`` 一致) で担保されるため、
        ここでは時刻条件を緩めるだけで充分。間に別住所訪問が挟まれば自然に
        FE 側で囲みが切れる (= 連番ペアでない場合は表示されない)。

    Returns:
        ``{(patient_id, weekday, start_time): group_id_str}`` の辞書.
        該当しない visit はキーに含まれない.
    """
    from collections import defaultdict

    bucket_to_visits: dict[tuple[UUID, int, tuple[float, float]], list[V2Visit]] = defaultdict(list)
    for v in visits:
        if v.lat is None or v.lng is None:
            continue
        bucket = _address_bucket(v.lat, v.lng)
        key = (v.office_id, v.weekday, bucket)
        bucket_to_visits[key].append(v)

    group_id_by_key: dict[tuple[UUID, int, time_cls], str] = {}
    for key, vs in bucket_to_visits.items():
        if len(vs) >= 2:
            office_id, wd, (lat_b, lng_b) = key
            gid = f"sa_{office_id}_{wd}_{lat_b:.4f}_{lng_b:.4f}"
            for v in vs:
                visit_key = (v.patient_id, wd, v.start_time)
                group_id_by_key[visit_key] = gid
    return group_id_by_key


def _build_kpi_overall(
    before: list[V2Visit],
    after: list[V2Visit],
    *,
    warnings: list[V2Warning],
    config: SchedulingConfig | None = None,
) -> V2KpiOverall:
    bd = calc_total_distance(before)
    ad = calc_total_distance(after)
    reduction = ((bd - ad) / bd * 100.0) if bd > 0 else 0.0
    courses_before = len({(v.office_id, v.weekday, v.course_code) for v in before if v.course_code})
    courses_after = len({(v.office_id, v.weekday, v.course_code) for v in after if v.course_code})
    h_viol = calc_h_violations(after, config=config)
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
    warnings: list[V2WarningOut] | None = None,
) -> list[V2WeekdayBeforeAfter]:
    """機能 B: 曜日ごとに Before / After の courses 構造を返す.

    W41 v2 (Mode 2 Before/After 表示拡張): ``office_name_by_id`` を受け取り
    各コースに ``office_name`` をセット + (office_name, code) で ABC 順ソート.

    CareFlow #101 FE: ``warnings`` を受け取り、各 visit に紐づくものを抽出して
    ``V2VisitForUI.warnings`` に流す.
    """
    out: list[V2WeekdayBeforeAfter] = []
    for wd in range(7):
        before_wd = [v for v in before if v.weekday == wd]
        after_wd = [v for v in after if v.weekday == wd]
        # 当該 weekday の warning のみ抽出 (None=曜日不問 warning も含める).
        wd_warnings = [w for w in (warnings or []) if w.weekday is None or w.weekday == wd]
        before_courses = _group_visits_into_courses(
            before_wd, office_name_by_id=office_name_by_id, warnings=wd_warnings
        )
        after_courses = _group_visits_into_courses(
            after_wd, office_name_by_id=office_name_by_id, warnings=wd_warnings
        )
        out.append(
            V2WeekdayBeforeAfter(
                weekday=wd,
                before=V2CourseContainer(courses=before_courses),
                after=V2CourseContainer(courses=after_courses),
            )
        )
    return out


def _warnings_for_visit(
    v: V2Visit,
    warnings: list[V2WarningOut] | None,
) -> list[V2WarningOut]:
    """CareFlow #101 FE: 当該 visit に紐づく warning を抽出.

    抽出条件 (いずれかを満たす):
      - ``w.weekday`` が visit.weekday と一致 + ``v.patient_id`` が
        ``w.affected_patient_ids`` に含まれる + ``w.type == 'travel_time_shortage'``
      - ``w.weekday is None`` (曜日不問) で ``patient_id`` 一致

    現状は travel_time_shortage のみ (#101 の赤枠用) に限定する.
    将来 two_staff_shortage 等を追加する場合は type 条件を緩める.
    """
    if not warnings:
        return []
    out: list[V2WarningOut] = []
    for w in warnings:
        # weekday 一致 (None は不問として全 weekday に流す).
        if w.weekday is not None and w.weekday != v.weekday:
            continue
        # 現状 #101 では travel_time_shortage に限定.
        if w.type != "travel_time_shortage":
            continue
        if v.patient_id in (w.affected_patient_ids or []):
            out.append(w)
    return out


def _group_visits_into_courses(
    visits: list[V2Visit],
    *,
    office_name_by_id: dict[UUID, str] | None = None,
    warnings: list[V2WarningOut] | None = None,
) -> list[V2CourseSummary]:
    """同 (office_id, weekday, course_code) を 1 コースとして集約.

    W41 v2 (Mode 2 Before/After 表示拡張):
        - ``office_name`` を ``office_name_by_id`` から引いて埋める.
        - 戻り値を (office_name, code) で ABC 順ソート.
          本店 A → B → ... → M, 続いて 都賀 A → ... を保証する.
          office_name が未取得な場合は str(office_id) で安定ソートする.

    W41 v2 (H2 視覚化): ``same_address_group_id`` を visits 全体で計算し
    `_v2visit_to_ui` に流して UI 側で連結表示できるようにする.

    CareFlow #101 FE: ``warnings`` を受け取り、各 visit に紐づくものを抽出して
    ``V2VisitForUI.warnings`` に流す (赤枠ハイライト用).
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
                        warnings=_warnings_for_visit(v, warnings),
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
    # Phase G-88 Step3: 事業所別の最適化設定をロードして注入 (read-only).
    # propose と full-optimize で同一 config を使い一貫性を保つ.
    config = await load_scheduling_config(db)
    try:
        result = await run_v2_pipeline(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            mode="diff_add",
            config=config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    before_visits: list[V2Visit] = result["before_visits"]
    after_visits: list[V2Visit] = result["after_visits"]
    pool_visits: list[V2Visit] = result["pool_visits"]
    warnings: list[V2Warning] = result["warnings"]
    # Phase G-92: 患者単位の proposal_source / 固定不可理由
    # ({patient_id: {"proposal_source": ..., "fixed_unavailable_reasons": [...]}}).
    proposal_meta_by_patient: dict[UUID, dict[str, Any]] = result.get(
        "proposal_meta_by_patient", {}
    )

    # プール患者ごとに 1 つの proposal を作る
    by_pid: dict[UUID, list[V2Visit]] = {}
    for v in pool_visits:
        by_pid.setdefault(v.patient_id, []).append(v)

    proposals: list[V2DiffAddProposal] = []
    for pid, pv in by_pid.items():
        pv_sorted = sorted(pv, key=lambda x: (x.weekday, x.start_time))
        primary = pv_sorted[0]
        # Phase G-92: proposal_source / fixed_unavailable_reasons を patient meta から
        # 引く. meta が無い (= 旧経路 / full_optimize) 場合は既定 "preferred".
        _meta = proposal_meta_by_patient.get(pid) or {}
        _proposal_source = _meta.get("proposal_source", "preferred")
        _fixed_unavailable_reasons = list(_meta.get("fixed_unavailable_reasons", []))
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
                # Phase G-92: 固定優先→希望フォールバックの分類結果.
                proposal_source=_proposal_source,
                fixed_unavailable_reasons=_fixed_unavailable_reasons,
            )
        )

    kpi = _build_kpi_overall(before_visits, after_visits, warnings=warnings, config=config)

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
    # Phase G-88 Step3: 事業所別の最適化設定をロードして注入 (read-only).
    config = await load_scheduling_config(db)
    try:
        result = await run_v2_pipeline(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            mode="full_optimize",
            pending_edits=list(payload.pending_edits),
            config=config,
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

    # CareFlow #101 FE: warnings を V2WarningOut に変換して
    # _build_weekday_before_after に渡し、各 visit に紐づくものを赤枠表示用に
    # V2VisitForUI.warnings へ流す.
    warnings_out = [_warning_to_out(w) for w in warnings]
    week_proposals = _build_weekday_before_after(
        before_visits,
        after_visits,
        office_name_by_id=office_name_by_id,
        warnings=warnings_out,
    )
    individual = _build_individual_proposals(before_visits, after_visits)
    kpi = _build_kpi_overall(before_visits, after_visits, warnings=warnings, config=config)

    # W41 v2 (Mode 2 UI 拡張): pool に入れたが after_visits に出てこなかった患者.
    # P2: reason / reason_detail / dropped_at_stage を構造化フィールドとして渡す.
    unassigned_raw = result.get("unassigned_patients", []) or []
    unassigned = [
        UnassignedPatient(
            patient_id=u["patient_id"],
            patient_name=u["patient_name"],
            patient_code=u.get("patient_code"),
            reason=u["reason"],
            reason_detail=u.get("reason_detail"),
            dropped_at_stage=u.get("dropped_at_stage"),
        )
        for u in unassigned_raw
    ]

    return AutoScheduleV2FullOptimizeResponse(
        proposal_batch_id=result["proposal_batch_id"],
        week_proposals=week_proposals,
        individual_proposals=individual,
        kpi_overall=kpi,
        warnings=warnings_out,
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

    注意: 本 endpoint は「45 分 lunch が物理的に取れる」visit を通すが、
    当該コース内で実際に lunch slot を取れるかは次回 full-optimization の
    ``_filter_unavailable_and_lunch`` + ``compute_lunch_window`` で初めて確定する.
    = apply 直後に PFV は 12:15-12:45 で保存されるが、次回再算出時に
    同コース 11:30-12:15 が他 visit で占有されていれば unassigned になり得る.
    UX 影響: 「採用したのに翌週消えた」というユーザー混乱が起こり得る.
    緩和策: FE 側で「採用後 即 full-optimize」フローを推奨済 (1ca5bba commit).
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

    # Wave U-2 (§2.2 A 経路): pattern_and_week は今週再生成のため iso_year/iso_week 必須.
    # PFV 更新前に 422 で弾く (PUT fixed-visits と同一契約).
    if payload.change_scope == "pattern_and_week" and (
        payload.iso_year is None or payload.iso_week is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="change_scope=pattern_and_week のときは iso_year / iso_week が必須です",
        )

    # Phase G-88 Step3: 確定再計算をプレビュー (propose/full-optimize) と同一 config で
    # 行い、確定スケジュールを設定どおりにする (read-only ロード).
    # Phase G-88 最終監査: H10 事前ゲートも config 窓を適用するため、ゲートより前に
    # ロードする (旧版では下のサービス層呼出直前でのみロードしていたため、事前ゲートが
    # 固定窓 11:30-13:30 のままで非既定昼休み窓設定時に設定窓では合法な visit を
    # 422 で弾く回帰があった).
    config = await load_scheduling_config(db)
    _lws = config.lunch_window_start
    _lwe = config.lunch_window_end

    # W41 v2 final cross-review (H-Codex-2): apply 境界での最低限の再検証.
    # クライアントが偽の visit_plans を送る攻撃に対して明らかな違反を弾く.
    # service 層は信頼境界の内側なので weekday / duration_min / start_time の
    # 範囲は Pydantic schema が既に担保しているが、H10 (昼休憩 重複禁止)
    # は schema にエンコードされていないためここで明示的にチェックする.
    #
    # Wave 3 (#WAVE3) Phase B 修正: 旧仕様の固定 12:00-13:00 ガードでは、
    # service 層 (compute_lunch_window) が動的に lunch=11:30-12:15 等にずらせば
    # 合法になる visit (例: 12:15-12:45) も 422 で reject されてしまい、
    # apply 経路が詰まっていた. ``_is_in_lunch_break`` (= 動的 lunch のどの配置でも
    # 避けられない時間帯か?) を使って「物理的に不可能」な visit のみ reject する.
    for idx, vp in enumerate(payload.visit_plans):
        # H10: 動的 lunch (config 窓・45-60 分) のどの配置でも避けられない
        # visit を弾く. 例: 12:15-12:45 は AM 側 11:30-12:15 lunch なら合法 → 通す.
        # 例: 12:10-12:50 は AM/PM どちらの避け方でも 45 分 lunch を確保できない → 422.
        if _is_in_lunch_break(vp.start_time, vp.end_time, window_start=_lws, window_end=_lwe):
            # P0-2 §4 (force_lunch モデル): 既定 (force_lunch=False) は現行どおり 422 拒否.
            # force_lunch=True は 422 を warning に降格して続行する. 昼休み警告の文言は
            # 二重に持たず、service 層 apply_individual_proposal 内の適用時再検証
            # (validate_pfv_changes の V4 = 本ゲートと同一 ``_is_in_lunch_break`` 判定) が
            # レスポンス warnings に日本語メッセージを載せる.
            if not payload.force_lunch:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"visit_plans[{idx}]: H10 違反 — 動的昼休憩 "
                        f"({_lws.strftime('%H:%M')}-{_lwe.strftime('%H:%M')} 内 45 分) を "
                        f"どこにも確保できない visit は不可 "
                        f"(start={vp.start_time}, end={vp.end_time})"
                    ),
                )
        # end_time > start_time (Pydantic では検証されない 0 / 負時間ガード).
        # force_lunch とは無関係に常に 422 (論理的に不正な区間のため).
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
            db, patient_id=payload.patient_id, visit_plans=plans, config=config
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except CrossAddressTimeConflictError as exc:
        # Wave 1 (#115) で意味変更: 本エラーは「データ不備で auto_shift 不能」
        # な配置を示す (= 座標 None patient 混在 / office 未解決).
        # 通常の異住所同時刻ペアは Wave 1 の auto_shift が解消するため 422 にしない.
        await db.rollback()
        logger.warning(
            "apply_individual: unresolvable same-time conflict (missing coord): "
            "patient=%s conflicts=%d",
            payload.patient_id,
            len(exc.conflicts),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "same_time_conflict_with_other_patient",
                "message": (
                    f"{len(exc.conflicts)} 件の解消不能な同時刻衝突を検出, 採用拒否. "
                    "対象 patient の座標 (lat/lng) または primary_office が未設定の "
                    "ため自動シフトで解消できません。患者マスタを修正してください。"
                ),
                "conflicts": exc.conflicts[:10],
            },
        ) from exc
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

    # Wave U-2 (§2.2 A 経路): PFV 更新コミット後、当該患者の今週 visits を PFV から
    # 再生成する (reset_visits_to_fixed の 1 患者版). 部分失敗の扱いは PUT fixed-visits と
    # 同一: 週再生成のみ失敗 → week_sync=null の 200 + logger.warning / IntegrityError → 409.
    week_sync: ApplyIndividualWeekSync | None = None
    if payload.change_scope == "pattern_and_week":
        assert payload.iso_year is not None and payload.iso_week is not None  # 上で 422 済み
        office_ids = await resolve_reset_office_ids(db, payload.patient_id)
        try:
            sync_result = await reset_visits_to_fixed(
                db,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                office_ids=office_ids,
                mode="legacy",
                dry_run=False,
                config=config,
                patient_id=payload.patient_id,
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
            ) from exc
        except Exception:
            # PFV は既にコミット済みのため、ここで 500 を返すと「型には登録されたのに失敗」
            # という紛らわしい状態になる。week_sync=None の 200 で返し、FE が「今週への反映は
            # 未実施 → 週を生成を再実行」と案内する (visits 側は rollback 済みで無傷)。
            await db.rollback()
            logger.warning(
                "apply-individual pattern_and_week: week sync failed after PFV commit "
                "(patient_id=%s iso=%s-W%s)",
                payload.patient_id,
                payload.iso_year,
                payload.iso_week,
                exc_info=True,
            )
        else:
            week_sync = ApplyIndividualWeekSync(
                visits_regenerated=int(sync_result.get("visits_regenerated", 0)),
                visits_soft_deleted=int(sync_result.get("visits_soft_deleted", 0)),
            )

    return AutoScheduleV2ApplyIndividualResponse(
        patient_id=result["patient_id"],
        applied=bool(result["applied"]),
        fixed_visit_ids=result["fixed_visit_ids"],
        idempotent=bool(result.get("idempotent", False)),
        warnings=list(result.get("warnings", [])),
        week_sync=week_sync,
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
    # Phase G-21 T3 (Reviewer L2 fix): dry_run リクエストを 1 行 INFO で監査ログに残す.
    if payload.dry_run:
        logger.info(
            "reset_to_fixed dry_run=True iso_year=%d iso_week=%d office_ids=%s mode=%s",
            payload.iso_year,
            payload.iso_week,
            [str(oid) for oid in payload.office_ids],
            payload.mode,
        )
    # Phase G-88 Step3: 確定再計算 (mode='auto') をプレビューと同一 config で行う.
    config = await load_scheduling_config(db)
    try:
        result = await reset_visits_to_fixed(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            mode=payload.mode,
            dry_run=payload.dry_run,
            config=config,
        )
        # Phase G-21 T3-5: dry_run=True の場合は DB 変更がないので rollback で statement
        # を破棄する (= flush もしていないが、 SQLAlchemy autobegin で開いた tx を閉じる).
        if payload.dry_run:
            await db.rollback()
        else:
            await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CrossAddressTimeConflictError as exc:  # pragma: no cover
        # Wave 1 (#115) で意味変更: 本エラーは「データ不備で auto_shift 不能」
        # な配置を示す. 通常の異住所同時刻ペアは Wave 1 の auto_shift が解消する.
        #
        # 現状到達不能: ``reset_visits_to_fixed`` (auto_allocator_v2.py) は内部で
        # ``_detect_cross_address_time_conflicts`` を呼ぶものの、結果は
        # **warning log のみ** で raise しない (= 続行する). よって本 except は
        # 現行コードパス上では捕捉対象なし (= dead code, coverage tool に明示).
        # 残してある理由:
        #   - 将来 reset 経路で「データ不備 → reset 拒否」に方針変更する可能性に備えた
        #     防御 (Phase A reviewer LOW #1).
        #   - 他 4 経路 (full-optimize / apply-proposal 等) と例外型を統一しておくと
        #     上位レイヤの HTTPException マッピングが簡潔.
        # 万一 raise された場合は念のため 422 で返す.
        await db.rollback()
        logger.warning(
            "reset_to_fixed: unresolvable same-time conflict (missing coord): "
            "iso_year=%s iso_week=%s conflicts=%d",
            payload.iso_year,
            payload.iso_week,
            len(exc.conflicts),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "same_time_conflict_with_other_patient",
                "message": (
                    f"{len(exc.conflicts)} 件の解消不能な同時刻衝突を検出, リセット拒否. "
                    "対象 patient の座標 (lat/lng) または primary_office が未設定で "
                    "自動シフトが行えません。患者マスタを修正してください。"
                ),
                "conflicts": exc.conflicts[:10],
            },
        ) from exc
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
        dry_run=bool(result.get("dry_run", False)),
        visits_to_insert=int(result.get("visits_to_insert", 0)),
        visits_to_skip_protected=int(result.get("visits_to_skip_protected", 0)),
        visits_to_skip_conflict=int(result.get("visits_to_skip_conflict", 0)),
    )


# ---------------------------------------------------------------------------
# 4b) POST /schedule/v2/sync-fixed-to-week (Wave U-0 §2.2-1: 1 患者版 型→週同期)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/sync-fixed-to-week",
    response_model=AutoScheduleV2SyncFixedToWeekResponse,
    status_code=status.HTTP_200_OK,
    summary="Wave U-0: 1 患者の当該週 visits を PFV から再生成 (型→週同期)",
)
async def sync_fixed_to_week_endpoint(
    payload: AutoScheduleV2SyncFixedToWeekRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2SyncFixedToWeekResponse:
    """1 患者の当該週 visits を patient_fixed_visits ベースで再生成する.

    反映先選択「A. 固定訪問週間に登録」の後段プリミティブ (§2.2-1). PFV へ書込後、
    今週の表へ即反映するために呼ぶ. 内部は ``reset_visits_to_fixed`` の
    ``patient_id`` フィルタ経路を再利用し、削除・再生成を当該 1 患者に限定する.

    保護規則は全患者版と同一: ``source='manual'`` / ``status='completed'`` は
    soft-delete しない, 衝突時は INSERT スキップ.
    """
    # ISO 週の妥当性検証 (reset_visits_to_fixed 内でも検証されるが早期に 400 で弾く).
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # 患者存在検証 (soft-delete 済みは 404).
    patient = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"patient_id={payload.patient_id} が見つかりません",
        )

    # office_ids を患者から導出する. reset_visits_to_fixed は serving office
    # (sub_office / course_template office) を内部で解決するが、staff プールと
    # active 患者ロードは office_ids に依存するため、primary + PFV.sub_office を含める.
    office_ids: set[UUID] = set()
    if patient.primary_office_id is not None:
        office_ids.add(patient.primary_office_id)
    sub_office_rows = await db.scalars(
        select(PatientFixedVisit.sub_office_id)
        .where(
            PatientFixedVisit.patient_id == payload.patient_id,
            PatientFixedVisit.sub_office_id.is_not(None),
        )
        .distinct()
    )
    for _oid in sub_office_rows.all():
        if _oid is not None:
            office_ids.add(_oid)

    config = await load_scheduling_config(db)
    try:
        result = await reset_visits_to_fixed(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(office_ids),
            mode="legacy",
            dry_run=False,
            config=config,
            patient_id=payload.patient_id,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "sync_fixed_to_week: integrity error (likely concurrent op): patient=%s err=%s",
            payload.patient_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    return AutoScheduleV2SyncFixedToWeekResponse(
        patient_id=payload.patient_id,
        visits_regenerated=int(result.get("visits_regenerated", 0)),
        visits_soft_deleted=int(result.get("visits_soft_deleted", 0)),
        warnings=list(result.get("warnings", [])),
    )


# ---------------------------------------------------------------------------
# 4c) POST /schedule/v2/visit-move-week-only (Wave U-2 §2.2 B: 汎用「1 手の週だけ移動」)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/visit-move-week-only",
    response_model=VisitMoveWeekOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="Wave U-2: 1 患者の該当 visit を今週だけ新位置へ移動 (PFV 不変)",
)
async def visit_move_week_only_endpoint(
    payload: VisitMoveWeekOnlyRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> VisitMoveWeekOnlyResponse:
    """改善提案 move の「この週だけ」経路 (§2.2 B). ``_apply_visit_move_week_only`` の薄い公開ラッパ.

    当該患者の該当 visit (planned・2 名体制なら全行) を新位置へ移動し
    ``source='manual_week'`` を刻む. PFV は一切変更しない (週生成・固定枠戻で保護).
    対象 visit が無ければ ``visits_moved=0`` の 200 (この週の表に元々出ていない)。

    安全網 (改善提案 B 経路): 移動元 (patient, old_weekday, old_start_time) に一致する
    ``is_pinned=True`` の PFV があれば 422 (ピン留め枠は週だけでも動かさない)。
    """
    # ISO 週の妥当性検証 (date 算出前に 400 で弾く).
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # 患者存在検証 (soft-delete 済みは 404).
    patient = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"patient_id={payload.patient_id} が見つかりません",
        )

    # 安全網: 移動元位置に一致する pinned PFV があれば 422 (U-1 scope pinned 検証と同水準).
    pinned = await db.scalar(
        select(PatientFixedVisit.id).where(
            PatientFixedVisit.patient_id == payload.patient_id,
            PatientFixedVisit.weekday == payload.old_weekday,
            PatientFixedVisit.start_time == payload.old_start_time,
            PatientFixedVisit.is_pinned.is_(True),
        )
    )
    if pinned is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ピン留めされた枠は「この週だけ」でも動かせません",
        )

    counters: dict[str, Any] = {"visits": 0, "patients": set()}
    try:
        await _apply_visit_move_week_only(
            db,
            patient_id=payload.patient_id,
            old_weekday=payload.old_weekday,
            old_start=payload.old_start_time,
            new_weekday=payload.new_weekday,
            new_start=payload.new_start_time,
            new_course=payload.new_course_template_id,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            counters=counters,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
        ) from exc
    except Exception:
        await db.rollback()
        raise

    moved = int(counters["visits"])

    # Wave U-3: 移動があった場合のみ操作ジャーナルに記録（ベストエフォート）
    if moved > 0:
        _patient_name = getattr(patient, "name", None) or str(payload.patient_id)
        _old_wd = fmt_weekday(payload.old_weekday)
        _old_st = payload.old_start_time.strftime("%H:%M")
        _new_wd = fmt_weekday(payload.new_weekday)
        _new_st = payload.new_start_time.strftime("%H:%M")
        _label = f"{_patient_name}様を {_old_wd}{_old_st}→{_new_wd}{_new_st} に移動"
        _iso_year = payload.iso_year
        _iso_week = payload.iso_week
        _old_start_str = fmt_time(payload.old_start_time)
        _new_start_str = fmt_time(payload.new_start_time)
        await record_op(
            db,
            user_id=current_user.id,
            iso_year=_iso_year,
            iso_week=_iso_week,
            op_group_id=payload.op_group_id,
            op_kind="move_visit_week_only",
            label=_label,
            forward_payload={
                "op": "move_visit_week_only",
                "patient_id": str(payload.patient_id),
                "iso_year": _iso_year,
                "iso_week": _iso_week,
                "from_weekday": payload.old_weekday,
                "from_start": _old_start_str,
                "to_weekday": payload.new_weekday,
                "to_start": _new_start_str,
            },
            inverse_payload={
                "op": "move_visit_week_only",
                "patient_id": str(payload.patient_id),
                "iso_year": _iso_year,
                "iso_week": _iso_week,
                "from_weekday": payload.new_weekday,
                "from_start": _new_start_str,
                "to_weekday": payload.old_weekday,
                "to_start": _old_start_str,
            },
        )
        await db.commit()

    return VisitMoveWeekOnlyResponse(visits_moved=moved)


# ---------------------------------------------------------------------------
# 4d) PATCH /schedule/v2/visits/{visit_id}/week-pin (週のピン = 青ピン)
#     PO 決定 2026-08-08 / 仕様: docs/plans/pin-and-movability-spec.md
# ---------------------------------------------------------------------------

# (旧) 青ピンを掛け外しできる source の制限は PO 決定 2026-08-09 で撤廃した。
# 実体が visits.week_pinned フラグになり source に触れないため、カイポケ取込
# (import) や手動作成 (manual) にも安全に掛け外しできる (出所は失われない)。
# 解除時に「型の管理へ戻す」意味を持つのは manual_week だけなので、その変換用に
# 定数だけ残す。
_WEEK_PIN_RELEASED_SOURCE = "auto"


def _visit_is_week_pinned(visit: Visit) -> bool:
    """青ピンが立っているか。フラグ or 旧方式 (source='manual_week') の和集合。

    0066 が manual_week を backfill 済みだが、DnD 移動等で新たに manual_week に
    なる行はフラグ無しで生まれる。表示・解除の対象はどちらも含める。
    """
    return bool(visit.week_pinned) or visit.source == VISIT_SOURCE_MANUAL_WEEK


@router.patch(
    "/v2/visits/{visit_id}/week-pin",
    response_model=VisitWeekPinResponse,
    status_code=status.HTTP_200_OK,
    summary="週のピン (青ピン): 今週この位置で固定する / 解除する",
)
async def visit_week_pin_endpoint(
    visit_id: uuid.UUID,
    payload: VisitWeekPinRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> VisitWeekPinResponse:
    """今週の訪問を「この位置のまま動かさない」状態にする / 解除する。

    実体は ``visits.week_pinned`` フラグ (PO 決定 2026-08-09 / migration 0066)。
    source には触れないため、カイポケ取込 (import) の訪問にも掛け外しでき、
    解除しても「取込由来」という出所と保護は失われない。

    保護の意味論 (reset_visits_to_fixed 側):
      - week_pinned=true は週生成の削除対象外
      - 当該 (patient, visit_date) の再生成を skip (型の時刻で上書きされない)

    解除 (``pinned=false``):
      - フラグを下ろす。**その場では訪問を動かさない**。
      - source='manual_week' (この週だけの手動配置) は 'auto' に戻し、次の週生成で
        型の時刻が読み込まれる。import / manual 等は source そのまま = 保護継続。

    planned 以外 (完了・実績入力済み等) は 422。
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"visit_id={visit_id} が見つかりません",
        )

    if visit.status != VISIT_STATUS_PLANNED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"予定 (planned) 以外の訪問は今週固定を変更できません (status={visit.status})",
        )

    old_flag = bool(visit.week_pinned)
    old_source = visit.source
    changed = False
    if payload.pinned:
        if not visit.week_pinned:
            visit.week_pinned = True
            changed = True
    else:
        if visit.week_pinned:
            visit.week_pinned = False
            changed = True
        if visit.source == VISIT_SOURCE_MANUAL_WEEK:
            visit.source = _WEEK_PIN_RELEASED_SOURCE
            changed = True

    if changed:
        db.add(
            AuditLog(
                actor_user_id=current_user.id,
                action="visit_week_pin_toggle",
                target_table="visits",
                target_id=str(visit.id),
                before={"week_pinned": old_flag, "source": old_source},
                after={"week_pinned": bool(visit.week_pinned), "source": visit.source},
            )
        )
        await db.commit()
        await db.refresh(visit)

    return VisitWeekPinResponse(
        visit_id=visit.id,
        pinned=_visit_is_week_pinned(visit),
        source=visit.source,
    )


@router.post(
    "/v2/visits/week-pin/bulk",
    response_model=VisitWeekPinBulkResponse,
    status_code=status.HTTP_200_OK,
    summary="週のピン (青) 一括: 今週を全件固定する / 今週の固定を全解除する",
)
async def visit_week_pin_bulk_endpoint(
    payload: VisitWeekPinBulkRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> VisitWeekPinBulkResponse:
    """赤ピンの「全件ピン留め / 全件ピン留め解除」と対になる青ピンの一括操作。

    実体は ``visits.week_pinned`` フラグ (PO 決定 2026-08-09)。source に触れないため
    **カイポケ取込 (import) を含む当該週の planned 全訪問** が対象になる
    (旧実装は source 書き換え方式で import/manual を除外しており、取込週では
    119 件中 5 件しか固定されなかった)。

    - ``pinned=true``  : planned かつ未固定 (フラグ無し・manual_week でもない) を
      一括で week_pinned=true に。
    - ``pinned=false`` : 固定済み (フラグ or manual_week) を一括で解除。
      **その場では動かない**。manual_week は 'auto' へ戻し、次の週生成で型の時刻が
      読み込まれる。import / manual は source そのまま = 出所と保護は失われない。

    ``dry_run=true`` は件数だけ返す (確認ダイアログ用)。
    audit_log は 1 操作 1 行 (counts)。
    """
    try:
        week_monday = date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc
    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    base = [
        Visit.deleted_at.is_(None),
        Visit.visit_date >= week_monday,
        Visit.visit_date <= week_sunday,
        Visit.status == VISIT_STATUS_PLANNED,
    ]
    if payload.pinned:
        # 未固定のみ (既に青ピンの行を数えない = 件数表示が「これから変わる数」になる)。
        cond = [
            *base,
            Visit.week_pinned.is_(False),
            Visit.source != VISIT_SOURCE_MANUAL_WEEK,
        ]
    else:
        cond = [
            *base,
            or_(Visit.week_pinned.is_(True), Visit.source == VISIT_SOURCE_MANUAL_WEEK),
        ]

    rows = (await db.scalars(select(Visit).where(*cond))).all()

    if payload.dry_run or not rows:
        return VisitWeekPinBulkResponse(target_count=len(rows), updated_count=0)

    for v in rows:
        if payload.pinned:
            v.week_pinned = True
        else:
            v.week_pinned = False
            if v.source == VISIT_SOURCE_MANUAL_WEEK:
                v.source = _WEEK_PIN_RELEASED_SOURCE
    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            action="visit_week_pin_bulk",
            target_table="visits",
            target_id=f"{payload.iso_year}-W{payload.iso_week:02d}",
            before={"pinned": not payload.pinned, "count": len(rows)},
            after={"pinned": payload.pinned, "count": len(rows)},
        )
    )
    await db.commit()

    return VisitWeekPinBulkResponse(target_count=len(rows), updated_count=len(rows))


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

    # Phase G-88 Step3: 確定再計算をプレビュー (full-optimize) と同一 config で行う.
    # Phase G-88 最終監査: H10 事前ゲートも config 窓を適用するため、ゲートより前に
    # ロードする (旧版では下のサービス層呼出直前でのみロードしていたため、警告判定が
    # 固定窓 11:30-13:30 のままで非既定昼休み窓設定時に不整合だった). 下のサービス層
    # 呼出はこの config を再利用する.
    config = await load_scheduling_config(db)
    _lws = config.lunch_window_start
    _lwe = config.lunch_window_end

    # H10 を境界で再検証 (apply-individual と同じ防衛深度).
    # CareFlow #113 hotfix: H10 違反 (動的昼休憩 重複) も 422 拒否を撤去し
    # warning log のみで続行. Fix E の auto_shift で意図せず lunch にずれた visit
    # を apply できないと業務詰まり. 後段で全面最適化を再実行すれば Fix E + lunch
    # bump で解消する想定. end_time <= start_time は論理的に不正なので 422 維持.
    #
    # Wave 3 (#WAVE3) Phase B 修正: 旧仕様の固定 12:00-13:00 判定では、
    # service 層 (compute_lunch_window) が動的に lunch=11:30-12:15 等にずらせば
    # 合法になる visit (例: 12:15-12:45) も警告対象になっていた. 他 endpoint と
    # 統一して ``_is_in_lunch_break`` (= 動的 lunch config 窓のどの 45 分配置でも
    # 避けられない visit か?) で判定する.
    # P0-2 (I-05): endpoint 段で検出した H10 警告をレスポンス warnings に表面化する
    # (従来は logger.warning のみ). apply-individual との非対称を解消する.
    endpoint_warnings: list[str] = []
    lunch_violations: list[str] = []
    for pi, pvp in enumerate(payload.visit_plans_per_patient):
        for vi, vp in enumerate(pvp.visit_plans):
            if _is_in_lunch_break(vp.start_time, vp.end_time, window_start=_lws, window_end=_lwe):
                lunch_violations.append(
                    f"patient[{pi}].visit[{vi}] start={vp.start_time} end={vp.end_time}"
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
    if lunch_violations:
        logger.warning(
            "apply_week_only: H10 violations (lunch dynamic %s-%s overlap) "
            "detected, apply 続行: %s",
            _lws.strftime("%H:%M"),
            _lwe.strftime("%H:%M"),
            lunch_violations[:5],
        )
        # P0-2 (I-05): 同じ H10 警告をレスポンス warnings にも載せる (挙動は従来どおり続行).
        endpoint_warnings.append(
            f"H10 昼休み（{_lws.strftime('%H:%M')}-{_lwe.strftime('%H:%M')}）と重なる visit が "
            f"{len(lunch_violations)} 件あります（週限定反映のため続行しました）。"
        )

    # CareFlow バグ検出 (apply_week_only 境界検証): 同 (office_id, weekday,
    # course_code, start_time) で **異 patient_id** の visit_plan が複数あれば
    # 「同コース同時刻に異住所 2 名配置」を検出する.
    #
    # CareFlow #112 hotfix (2026-05-18): 422 拒否すると Fix E (auto_shift) で
    # 解消しきれないケースで apply が永久に詰まる. ユーザー impact が大きいため
    # warning ログのみ残し apply は続行する. 後段で全面最適化を再実行すれば
    # Fix E が再度シフトを試みる想定.
    seen_slots: dict[tuple[UUID, int, str, str], UUID] = {}
    conflicts: list[dict[str, str | int | None]] = []
    for pvp in payload.visit_plans_per_patient:
        for vp in pvp.visit_plans:
            slot_key = (
                vp.office_id,
                vp.weekday,
                vp.course_code or "M",
                vp.start_time.isoformat(timespec="minutes"),
            )
            first_pid = seen_slots.get(slot_key)
            if first_pid is None:
                seen_slots[slot_key] = pvp.patient_id
            elif first_pid != pvp.patient_id:
                conflicts.append(
                    {
                        "office_id": str(vp.office_id),
                        "weekday": vp.weekday,
                        "course_code": vp.course_code,
                        "start_time": vp.start_time.isoformat(timespec="minutes"),
                        "patient_a": str(first_pid),
                        "patient_b": str(pvp.patient_id),
                    }
                )

    if conflicts:
        # 拒否せず warning log のみ. apply は続行.
        # (reviewer 指摘で module-level logger に統一 + iso_year/iso_week 追加)
        logger.warning(
            "apply_week_only.same_time_conflict count=%d iso_year=%d iso_week=%d conflicts=%s",
            len(conflicts),
            payload.iso_year,
            payload.iso_week,
            conflicts[:5],
        )

    patient_visit_plans = [
        {
            "patient_id": pvp.patient_id,
            "visit_plans": [vp.model_dump() for vp in pvp.visit_plans],
        }
        for pvp in payload.visit_plans_per_patient
    ]
    # config は上の H10 事前ゲートでロード済 (Phase G-88 最終監査で前倒し). 再利用する.
    try:
        result = await apply_week_only(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
            patient_visit_plans=patient_visit_plans,
            pending_edits=list(payload.pending_edits),
            config=config,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PinnedVisitMovedError as exc:
        # Phase G-21 T3-6: D&D で pinned PFV を動かした → 422 拒否.
        await db.rollback()
        logger.warning(
            "apply_week_only: pinned PFV moved attempt: iso_year=%s iso_week=%s count=%d",
            payload.iso_year,
            payload.iso_week,
            len(exc.violations),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "pinned_visit_moved",
                "message": (
                    f"{len(exc.violations)} 件の pinned PFV を D&D で動かす操作は拒否されました. "
                    "pinned 解除してから再操作してください."
                ),
                "violations": exc.violations[:10],
            },
        ) from exc
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
        # P0-2 (I-05): endpoint 段の H10 警告 + service 層の warnings を合流.
        warnings=endpoint_warnings + list(result.get("warnings", [])),
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

        # H10: 動的昼休憩 (11:30-13:30 内 45-60 分) を確保できない時刻を弾く.
        # time_type='時間帯' などレンジ指定の場合、new_start..new_end は希望範囲であって
        # 実訪問時間ではないので H10 をスキップする (実訪問は duration_min 分のどこかで行う).
        #
        # Wave 3 (#WAVE3) Phase B 修正: 旧仕様の固定 12:00-13:00 ガードを
        # ``_is_in_lunch_break`` (動的 lunch 11:30-13:30 のどの配置でも避けられない
        # 時間帯か?) に置き換えた. 例: 12:15-12:45 は AM 側 11:30-12:15 lunch なら
        # 合法 → 通す. 12:10-12:50 はどう取っても 45 分 lunch を確保できない → 422.
        if not is_range_type:
            if end_t is not None:
                actual_end_t = end_t
            else:
                end_total = start_t.hour * 60 + start_t.minute + duration_min
                if end_total >= 24 * 60:
                    end_total = 23 * 60 + 59
                actual_end_t = time_cls(end_total // 60, end_total % 60)
            # Phase G-88 最終監査: out-of-scope (PFV マスタ CRUD, 最適化3経路ではない).
            # config 窓注入は full/diff/propose/apply-individual/apply-week-only/
            # reset-to-fixed のみが対象のため、ここは固定窓のまま据え置く.
            if _is_in_lunch_break(start_t, actual_end_t):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "H10 違反: 動的昼休憩 (11:30-13:30 内 45 分) を確保できない "
                        "時間は指定できません"
                    ),
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
        # H10: 動的昼休憩 (11:30-13:30 内 45-60 分) を確保できない時刻は弾く.
        # Wave 3 (#WAVE3) Phase B 修正: 旧仕様の固定 12:00-13:00 ガードを
        # ``_is_in_lunch_break`` (動的 lunch のどの配置でも避けられない時間帯か?)
        # に置き換えた. 例: 12:15-12:45 は AM 側 11:30-12:15 lunch なら合法 → 通す.
        # Phase G-88 最終監査: out-of-scope (PFV マスタ CRUD week-only,
        # 最適化3経路ではない). config 窓注入対象外のため固定窓のまま据え置く.
        effective_end = end_t or visit_row.end_time
        if _is_in_lunch_break(start_t, effective_end):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "H10 違反: 動的昼休憩 (11:30-13:30 内 45 分) を確保できない "
                    "時間は指定できません"
                ),
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


# ---------------------------------------------------------------------------
# 8) POST /schedule/v2/unassign-all-staff (Phase G-17: 一斉未割当)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/unassign-all-staff",
    response_model=AutoScheduleV2UnassignAllResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase G-17: 表示中の週の全 Course 担当 + visit_staff_assignments を一括解除",
)
async def unassign_all_staff_endpoint(
    payload: AutoScheduleV2UnassignAllRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AutoScheduleV2UnassignAllResponse:
    """Phase G-17: 表示中の週の全 Course 担当 + visit_staff_assignments を一括解除.

    - ``courses.assigned_staff_id`` を NULL に (course 自体は残す)
    - ``visit_staff_assignments`` を物理 delete (visit 自体は残す)
    - audit_logs に ``action="schedule_unassign_all_staff"`` で件数を記録

    RBAC: admin / manager のみ.
    """
    try:
        result = await unassign_all_staff_for_week(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=list(payload.office_ids),
        )
        # 監査ログ (件数を after に記録).
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="schedule_unassign_all_staff",
                target_table="courses",
                target_id=f"{payload.iso_year}-W{payload.iso_week}",
                before={},
                after={
                    "courses_unassigned": result["courses_unassigned"],
                    "visit_assignments_removed": result["visit_assignments_removed"],
                },
            )
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    return AutoScheduleV2UnassignAllResponse(
        courses_unassigned=int(result["courses_unassigned"]),
        visit_assignments_removed=int(result["visit_assignments_removed"]),
    )


# ---------------------------------------------------------------------------
# weekday-staff-capacity (週ビューのコース「休」/定員をスタッフ数連動に)
# ---------------------------------------------------------------------------


@router.get(
    "/v2/weekday-staff-capacity",
    response_model=WeekdayStaffCapacityResponse,
    status_code=status.HTTP_200_OK,
    summary="週ビュー: (拠点×曜日) ごとの稼働スタッフ数 (A-E コース開講判定用)",
)
async def weekday_staff_capacity_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(..., ge=2020, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    office_id: UUID | None = Query(
        default=None,
        description="単一拠点に絞る場合に指定. 未指定なら全 active 拠点を対象.",
    ),
) -> WeekdayStaffCapacityResponse:
    """週ビュー (CourseWeekOverview / CourseDayTablePanel) の A-E コース「休」/
    定員表示を auto-schedule (auto_allocator_v2 Stage 4) と統一するための read-only
    エンドポイント.

    返すのは (office_id, weekday) ごとの稼働可能 staff 数 (role='staff', trainee
    除外, shift/override/応援/営業日 考慮済 = ``count_active_staff_per_weekday``).
    frontend は ``courseCodeIndex(A=0..E=4) < min(staff_count, course_codes_max)``
    で A-E コースを開講 (定員6) / 休 (定員0) と判定する.

    Mコース (M/M2..) はこの API の対象外 (引き続き course_template の静的
    capacity_<曜日> を使う).
    """
    # 対象拠点: office_id 指定なら単一、未指定なら全 active 拠点.
    if office_id is not None:
        office_ids = [office_id]
    else:
        rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(rows.all())

    counts = await count_active_staff_per_weekday(
        db,
        office_ids=office_ids,
        iso_year=iso_year,
        iso_week=iso_week,
    )
    # Phase G-53: 週ビューヘッダーの「拠点別 S/M」表示用に manager 数も集計する.
    # staff と独立に (office_id, weekday) → count を引けるよう別 dict で持ち、
    # どちらか一方でも > 0 の (office_id, weekday) を item として返す
    # (frontend は欠損キーを 0 扱いするので両方 0 は省略する).
    manager_counts = await count_active_managers_per_weekday(
        db,
        office_ids=office_ids,
        iso_year=iso_year,
        iso_week=iso_week,
    )

    keys = set(counts.keys()) | set(manager_counts.keys())
    items = [
        WeekdayStaffCapacityItem(
            office_id=oid,
            weekday=wd,
            staff_count=counts.get((oid, wd), 0),
            manager_count=manager_counts.get((oid, wd), 0),
        )
        for (oid, wd) in keys
        if counts.get((oid, wd), 0) > 0 or manager_counts.get((oid, wd), 0) > 0
    ]
    # 安定した出力順 (office_id, weekday) でソート.
    items.sort(key=lambda it: (str(it.office_id), it.weekday))

    return WeekdayStaffCapacityResponse(
        items=items,
        course_codes_max=_COURSE_CODES_MAX,
    )


# ---------------------------------------------------------------------------
# pfv-course-presence (PO 2026-07-09: PFV に含まれるコースを「正」として列を出す)
# ---------------------------------------------------------------------------


@router.get(
    "/v2/pfv-course-presence",
    response_model=PfvCoursePresenceResponse,
    status_code=status.HTTP_200_OK,
    summary="固定訪問(PFV)にコースが含まれる (course_template_id×曜日) の件数",
)
async def pfv_course_presence_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PfvCoursePresenceResponse:
    """PO 決定 (2026-07-09): 固定訪問スケジュール (PFV) に含まれるコースを「正」とし、
    スタッフ数連動の開講判定と和集合で週/日ビューの列を出すための read-only 集計.

    スタッフ削除等で稼働 0 になっても PFV がコース指定済みなら列を隠さず (= 既存訪問を
    可視に保ち) 別途警告を出す、という新原則の表示側根拠を提供する.

    ``patient_fixed_visits`` を (course_template_id, weekday) で GROUP BY し件数を返す.
      - ``course_template_id IS NULL`` は除外 (office フォールバックで解決される枠は
        特定の course_template 列に紐付かないため).
      - 削除済み患者 (``patients.deleted_at IS NOT NULL``) の PFV は除外.
      - mode ('normal'/'special') は絞らず全 mode を集計する. 本 API は週非依存で
        「その (テンプレ×曜日) に固定枠が存在するか」だけを見るため、normal/special の
        いずれの固定枠でも「正」= 列を出す根拠になる (Layer1 は ``_select_pattern`` で
        週ごとに mode を選ぶが、ここは存在判定のみ). 隠して不可視になる事故を防ぐ側に倒す.

    RBAC: admin / manager のみ.
    """
    result = await db.execute(
        select(
            PatientFixedVisit.course_template_id,
            PatientFixedVisit.weekday,
            func.count(PatientFixedVisit.id),
        )
        .join(Patient, Patient.id == PatientFixedVisit.patient_id)
        .where(
            PatientFixedVisit.course_template_id.is_not(None),
            Patient.deleted_at.is_(None),
        )
        .group_by(PatientFixedVisit.course_template_id, PatientFixedVisit.weekday)
    )
    items = [
        PfvCoursePresenceItem(
            course_template_id=ctid,
            weekday=wd,
            pfv_count=int(cnt),
        )
        for (ctid, wd, cnt) in result.all()
        if ctid is not None
    ]
    # 安定した出力順 (course_template_id, weekday) でソート.
    items.sort(key=lambda it: (str(it.course_template_id), it.weekday))

    return PfvCoursePresenceResponse(items=items)


# ---------------------------------------------------------------------------
# schedule-advisor Phase 1「診る」: スケジュール健康診断 (read-only 集計)
# ---------------------------------------------------------------------------


@router.get(
    "/v2/schedule-health",
    response_model=ScheduleHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="スケジュール健康診断: 週次・拠点別・曜日別・コース別の移動/隙間の無駄集計",
)
async def schedule_health_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(..., ge=2020, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    office_id: UUID | None = Query(
        default=None,
        description="単一拠点に絞る場合に指定. 未指定なら全拠点を対象.",
    ),
) -> ScheduleHealthResponse:
    """schedule-advisor §3 Phase 1「診る」: 固定スケジュールの無駄を数字で返す.

    対象週の Visit (planned/in_progress/completed, 未削除) を Course/Patient と JOIN し
    ``(office_id, weekday, course_code)`` 単位で総移動時間・距離・訪問間バッファー・
    隙間 (gap) を集計する read-only エンドポイント.

    効果の物差しは提案エンジンと同一の travel モデル (単一ソース). speed/buffer は
    ``load_scheduling_config`` の事業所別設定で上書き可能. 空のコース / 曜日は出力しない.
    """
    # 提案 / 全面最適化と同一 config をロードして speed/buffer を一致させる (read-only).
    config = await load_scheduling_config(db)
    office_ids = [office_id] if office_id is not None else []
    return await compute_schedule_health(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        office_ids=office_ids,
        config=config,
    )


@router.get(
    "/v2/schedule-health/trend",
    response_model=ScheduleHealthTrendResponse,
    status_code=status.HTTP_200_OK,
    summary="見直しどきトレンド: 指定週から遡る週次の移動/隙間サマリ (劣化判定は FE)",
)
async def schedule_health_trend_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(..., ge=2020, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    weeks: int = Query(default=8, ge=1, le=12, description="遡る週数 (上限12)."),
    office_id: UUID | None = Query(
        default=None,
        description="単一拠点に絞る場合に指定. 未指定なら全拠点を対象.",
    ),
) -> ScheduleHealthTrendResponse:
    """schedule-advisor §3 Phase 3「見直しどき通知」: 週次トレンドの素データを返す.

    指定週から遡って ``weeks`` 週分、各週の **office 横断合計のみ**
    (visit_count / travel_minutes / travel_km / gap_minutes) を古→新順で返す
    read-only エンドポイント. 過去週の実 Visit に対し健康診断計算を週ごとに回す
    (履歴テーブルは持たない). visit ゼロの週も totals 全 0 で含める.

    劣化判定 (見直しどきの成否) は現場フィードバックで調整しやすいよう **FE 側**
    で行う. BE は素データのみを返す.
    """
    config = await load_scheduling_config(db)
    office_ids = [office_id] if office_id is not None else []
    return await compute_schedule_health_trend(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        weeks=weeks,
        office_ids=office_ids,
        config=config,
    )


@router.get(
    "/v2/schedule-health/course-detail",
    response_model=ScheduleHealthCourseDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="H1: 対象コースの原因内訳 (重い遷移 + 患者別配置コスト) を返す (read-only)",
)
async def schedule_health_course_detail_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(..., ge=2020, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    office_id: UUID = Query(..., description="対象拠点"),
    course_code: str = Query(..., min_length=1, max_length=8, description="対象コース (例: B)"),
) -> ScheduleHealthCourseDetailResponse:
    """健康診断→処方箋 (H1): 「なぜこのコースが重いのか」の内訳を返す.

    - transitions: 連続訪問間の移動 (健康診断と同一物差し。同住所 0 / 座標欠損 0)。
    - patient_costs: 患者別の配置コスト (W3 厳密限界コスト。座標欠損患者は対象外)。
    該当コースの訪問が無い週は weekdays=[] の 200。read-only。
    """
    office = await db.scalar(select(Office).where(Office.id == office_id))
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Office not found")
    config = await load_scheduling_config(db)
    try:
        return await compute_course_detail(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            office_id=office_id,
            course_code=course_code,
            config=config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# propose-slots (Phase2-2: 候補患者の実現可能な空き枠を算出・ランキング)
# ---------------------------------------------------------------------------


async def _resolve_candidate_coords(
    db: DbDep,
    payload: ProposeSlotsRequest,
) -> tuple[float | None, float | None, UUID | None]:
    """候補座標と (address 由来の) 拠点を確定する.

    優先順位:
        1. address があれば geocode → lat/lng + 拠点判定 (OfficeAssigner).
        2. address 無し + lat/lng があればそれを採用.
        3. どちらも無ければ (None, None, None) を返す
           (近接スコア無効で続行; 400 にはしない).

    geocode 失敗時は lat/lng フォールバックがあればそれを使い、無ければ
    座標 None で続行する (実現可能性判定自体は座標が無くても容量/時刻系で動く).
    """
    resolved_office_id: UUID | None = None
    if payload.address and payload.address.strip():
        try:
            geo = await geocode_address(payload.address)
        except GeocodingServiceError as exc:
            logger.warning("propose-slots geocode failed: %s", exc)
            geo = None
        if geo is not None:
            # 住所から拠点も判定する (失敗は無視).
            try:
                resolution = await OfficeAssigner.resolve_with_details(db, payload.address)
                if resolution.office is not None:
                    resolved_office_id = resolution.office.id
            except Exception as exc:  # pragma: no cover - 補助情報なので握り潰す
                logger.warning("propose-slots office resolve failed: %s", exc)
            return geo.lat, geo.lng, resolved_office_id
    # address 無し or geocode 失敗 → lat/lng フォールバック.
    return payload.lat, payload.lng, resolved_office_id


def _mini_entries(mini: list[dict[str, object]] | None) -> list[ProposeMiniScheduleEntry] | None:
    """ミニスケジュールの dict 列を schema entry 列へ変換 (None はそのまま None)."""
    if mini is None:
        return None
    return [
        ProposeMiniScheduleEntry(
            time=str(e["time"]),
            name=str(e["name"]),
            ins=e["ins"],  # type: ignore[arg-type]
            is_here=bool(e["is_here"]),
            is_pair=bool(e["is_pair"]),
            sex_restriction=e.get("sex_restriction"),  # type: ignore[arg-type]
            is_multi_staff=bool(e.get("is_multi_staff", False)),
            sex=e.get("sex"),  # type: ignore[arg-type]
            is_event=bool(e.get("is_event", False)),
            end_time=e.get("end_time"),  # type: ignore[arg-type]
        )
        for e in mini
    ]


def _proposed_to_item(p: ProposedSlot) -> ProposeSlotItem:
    """内部表現 ``ProposedSlot`` を API schema ``ProposeSlotItem`` へ変換."""
    return ProposeSlotItem(
        office_id=p.office_id,
        office_name=p.office_name,
        weekday=p.weekday,
        weekday_code=WEEKDAY_INT_TO_CODE[p.weekday],  # type: ignore[arg-type]
        course_code=p.course_code,
        course_label=p.course_label,
        staff_name=p.staff_name,
        start_time=f"{p.start.hour:02d}:{p.start.minute:02d}",
        end_time=f"{p.end.hour:02d}:{p.end.minute:02d}",
        score=p.score,
        reasons=p.reasons,
        warnings=p.warnings,
        is_pair=p.is_pair,
        pair_partner=p.pair_partner,
        mini_schedule=_mini_entries(p.mini_schedule) or [],
        is_efficiency_alternative=p.is_efficiency_alternative,
        marginal_cost_minutes=p.marginal_cost_minutes,
        overcapacity=p.overcapacity,
        partner_course_code=p.partner_course_code,
        partner_course_label=p.partner_course_label,
        partner_course_template_id=p.partner_course_template_id,
        partner_staff_name=p.partner_staff_name,
        partner_mini_schedule=_mini_entries(p.partner_mini_schedule),
        event_conflicts=[ProposeEventConflict(**c) for c in p.event_conflicts],
    )


@router.post(
    "/v2/propose-slots",
    response_model=ProposeSlotsResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase2-2: 候補患者を入れられる実現可能な空き枠を算出・ランキング",
)
async def propose_slots_endpoint(
    payload: ProposeSlotsRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ProposeSlotsResponse:
    """候補患者の希望から、対象週 × 拠点の実スケジュールの実現可能な空き枠を返す.

    本 endpoint は **read-only**: DB を変更しない.

    実現不能な時刻は一切返さない (距離 / 移動 / バッファー / 昼休み / 18:00 /
    容量 / time_type / 同住所を Stage1 純ソルバが自動割当と同手法で判定).
    """
    # 1. 候補座標 (+ 住所判定拠点) を確定.
    cand_lat, cand_lng, resolved_office_id = await _resolve_candidate_coords(db, payload)

    # 対象拠点: 明示指定 > 住所判定拠点 > 全拠点.
    office_ids = list(payload.office_ids)
    if not office_ids and resolved_office_id is not None:
        office_ids = [resolved_office_id]

    # 座標が確定できなければ近接スコア / 距離判定が無効になる. ソルバは座標を
    # 必須にするため (haversine 計算), 座標無しでは枠を返せない. 0 件として返す.
    if cand_lat is None or cand_lng is None:
        return ProposeSlotsResponse(
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            candidate_lat=None,
            candidate_lng=None,
            resolved_office_id=resolved_office_id,
            slots=[],
            message=(
                "候補の座標を確定できませんでした (住所のジオコード失敗 / lat-lng 未指定). "
                "住所または緯度経度を指定してください。"
            ),
        )

    # 2. 対象週 × 拠点の実 Visit を 1 回ロードしてコース単位に集計.
    try:
        buckets, office_name_by_id, office_code_by_id = await load_week_course_buckets(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=office_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 3-4. 候補入力を組み立てて全コース × 希望曜日でソルバを回しランキング.
    preferred_weekday_ints = frozenset(
        WEEKDAY_CODE_TO_INT[code]
        for code in payload.preferred_weekdays
        if code in WEEKDAY_CODE_TO_INT
    )
    candidate = CandidateInput(
        lat=cand_lat,
        lng=cand_lng,
        service_minutes=payload.service_minutes,
        time_type=payload.time_type,
        preferred_start=_parse_hhmm(payload.preferred_start),
        preferred_end=_parse_hhmm(payload.preferred_end),
        preferred_weekdays=preferred_weekday_ints,
        requires_multiple_staff=payload.requires_multiple_staff,
        existing_patient_id=payload.existing_patient_id,
        sex_restriction=payload.sex_restriction,
    )

    # Phase G-88 Step3: full-optimize と同一の事業所別設定をロードして注入 (read-only).
    config = await load_scheduling_config(db)

    # I-07 (H5 warning): 受入カレンダー × を diff-add と同一ローダで読む (read-only).
    #   enforce ではなく warning: 該当時刻の枠に acceptance_calendar 警告 + スコア降格
    #   を付けるだけで除外はしない (N-6「なぜ出ないか分かるように」).
    unavailable_slots = await _load_unavailable_slots(db, office_ids=office_ids)
    # I-11 (pair blocked): pair_mode='blocked' 判定用に same-address link を読む.
    #   候補が既存患者 (existing_patient_id) のときのみ意味を持つ (blocked は候補と
    #   既存訪問患者の関係). 新規候補 (None) では blocked 判定不要なので DB 参照を省く.
    pair_modes: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
    if candidate.existing_patient_id is not None:
        pair_patient_ids: set[uuid.UUID] = {candidate.existing_patient_id}
        for _bucket in buckets.values():
            for _v in _bucket.visits:
                pair_patient_ids.add(_v.patient_id)
        pair_modes = await _load_same_address_pair_modes(db, patient_ids=list(pair_patient_ids))

    # ランキング済み全スロットを 1 回算出し、slots[] (上位 limit) と coverage で共有.
    # P3-④: include_efficiency_alternatives=True 時は末尾に効率代替 (最大3件) が付く.
    # P-1a: 上位候補に marginal_cost_minutes (厳密限界コスト) が付与され delta 昇順に並ぶ.
    # P-1b: 候補 0 件時は excluded_raw に除外理由が集約される (非空を保証).
    excluded_raw: list[ExcludedReasonSummary] = []
    all_proposed = compute_all_proposed_slots(
        buckets,
        office_name_by_id,
        candidate,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        config=config,
        include_efficiency_alternatives=payload.include_efficiency_alternatives,
        exclusions_out=excluded_raw,
        unavailable_slots=unavailable_slots,
        pair_modes=pair_modes,
    )
    # P3-④: 通常候補で limit を消費し、効率代替は limit 外で最大3件付加する
    #   (通常候補が効率代替に押し出されないよう flag で分離). 既定 False では
    #   効率代替が存在しないため slots[] = all_proposed[:limit] と完全に同一.
    normal_proposed = [p for p in all_proposed if not p.is_efficiency_alternative]
    alt_proposed = [p for p in all_proposed if p.is_efficiency_alternative]
    proposed = normal_proposed[: payload.limit] + alt_proposed

    # 5. API schema に詰める (slots[] は通常候補 上位 limit 件 + 効率代替 最大3件).
    slots_out = [_proposed_to_item(p) for p in proposed]

    # 6. 週N日カバレッジ: 希望曜日ごとに実現可否 + 最良枠をグルーピング.
    #    required_days は frequency_per_week 優先, 無ければ希望曜日数.
    #    P3-④: カバレッジは希望適合の通常候補のみで判定 (効率代替=希望外は除外) し、
    #    include_efficiency_alternatives の有無で coverage 出力が変わらないようにする.
    if payload.frequency_per_week is not None:
        required_days = payload.frequency_per_week
    else:
        required_days = len(preferred_weekday_ints)
    cov = compute_coverage(
        normal_proposed,
        requested_weekdays=preferred_weekday_ints,
        required_days=required_days,
    )
    coverage = ProposeCoverage(
        required_days=cov.required_days,
        requested_weekdays=cov.requested_weekdays,
        per_day=[
            ProposeCoverageDay(
                weekday=d.weekday,
                weekday_code=WEEKDAY_INT_TO_CODE[d.weekday],  # type: ignore[arg-type]
                has_slot=d.has_slot,
                best_slot=_proposed_to_item(d.best_slot) if d.best_slot is not None else None,
            )
            for d in cov.per_day
        ],
        covered_days=cov.covered_days,
        fully_covered=cov.fully_covered,
    )

    message = None if slots_out else "入れられる枠なし (実現可能な空き枠が見つかりませんでした)"

    # P-1b: 除外理由集約を API schema へ (0 件時のみ非空. 候補があれば空).
    excluded_summary = [
        ProposeExcludedReason(
            reason=e.reason,  # type: ignore[arg-type]
            count=e.count,
            weekday=e.weekday,
            sample_course_code=e.sample_course_code,
        )
        for e in excluded_raw
    ]

    # 定員超過の管理者相談プロセス (方式b): 定員 +1 なら入る「定員超過候補」の照会.
    #   - True: 定員 +1 で列挙し overcapacity_slots (別配列 / 上限 limit) を返す.
    #   - False (既定): 通常候補が 0 件かつ capacity_full が理由に含まれるときのみ、
    #     定員 +1 で件数だけ数えて overcapacity_available_count に載せる (それ以外は None).
    # いずれも 1 回目の通常列挙 (compute_all_proposed_slots) の結果には手を触れない.
    overcapacity_available_count: int | None = None
    overcapacity_slots_out: list[ProposeSlotItem] = []
    if payload.include_overcapacity:
        over = compute_overcapacity_slots(
            buckets,
            office_name_by_id,
            candidate,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
            unavailable_slots=unavailable_slots,
            pair_modes=pair_modes,
        )
        overcapacity_slots_out = [_proposed_to_item(p) for p in over[: payload.limit]]
    elif not normal_proposed and any(e.reason == "capacity_full" for e in excluded_summary):
        # 件数のみ (delta 計算はスキップ). +1 でも 0 件なら 0.
        over_count = compute_overcapacity_slots(
            buckets,
            office_name_by_id,
            candidate,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
            unavailable_slots=unavailable_slots,
            pair_modes=pair_modes,
            assign_marginal=False,
        )
        overcapacity_available_count = len(over_count)

    return ProposeSlotsResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        candidate_lat=cand_lat,
        candidate_lng=cand_lng,
        resolved_office_id=resolved_office_id,
        slots=slots_out,
        coverage=coverage,
        excluded_summary=excluded_summary,
        message=message,
        overcapacity_available_count=overcapacity_available_count,
        overcapacity_slots=overcapacity_slots_out,
    )


# ---------------------------------------------------------------------------
# pool-overview (Stage P-2: プール患者ごとの最良候補を軽量計算し「効果順」一覧を返す)
# ---------------------------------------------------------------------------

# 候補の希望時刻種別 (propose_slots.ProposeTimeType と同一語彙).
_POOL_TIME_TYPES: frozenset[str] = frozenset({"固定", "時間帯", "午前", "午後", "終日"})

# top_excluded_reason の tie-break 優先度 (同数のとき代表理由を決定的に選ぶ).
# capacity_full を最優先 (根本原因). course_closed は末尾 (曜日そのものが未開講).
_POOL_EXCLUSION_PRIORITY: tuple[str, ...] = (
    "capacity_full",
    "travel_shortage",
    "lunch_window",
    "no_gap",
    "course_closed",
)


def _patient_to_pool_candidate(patient: Patient) -> CandidateInput | None:
    """プール患者 1 名を propose-slots と同じ ``CandidateInput`` に変換する.

    導出は FE の個別プール提案 (``PoolCandidateList`` → coerceWeeklyPattern) と同一に揃え、
    pool-overview の最良候補が個別 propose-slots 呼び出しの先頭候補と一致するようにする:
        - service_minutes / time_type / preferred_weekdays は ``weekly_pattern`` サマリ形式
          の top-level キーから取る.
        - preferred_start は time_type∈(固定, 時間帯) のときのみ、preferred_end は
          time_type=時間帯 のときのみ有効 (FE の showTimeRange / showEnd と同一ゲート).
        - 座標は患者の lat/lng を使う. どちらか欠落なら None (ソルバが座標必須のため算出不能).
    """
    if patient.lat is None or patient.lng is None:
        return None
    wp = patient.weekly_pattern if isinstance(patient.weekly_pattern, dict) else {}

    sm_raw = wp.get("service_minutes")
    # float も許容 (JSONB 経路によっては 30.0 が混入しうる。FE coerceWeeklyPattern の
    # Number() 解釈と一致させ、pool-overview と個別 propose-slots の食い違いを防ぐ).
    service_minutes = (
        int(sm_raw)
        if isinstance(sm_raw, (int, float)) and not isinstance(sm_raw, bool) and sm_raw > 0
        else 35
    )

    tt_raw = wp.get("time_type")
    time_type = tt_raw if isinstance(tt_raw, str) and tt_raw in _POOL_TIME_TYPES else "終日"

    show_time_range = time_type in ("固定", "時間帯")
    show_end = time_type == "時間帯"
    try:
        preferred_start = _parse_hhmm(wp.get("preferred_start")) if show_time_range else None
        preferred_end = _parse_hhmm(wp.get("preferred_end")) if show_end else None
    except (ValueError, TypeError, AttributeError):
        # 非文字列 (int 等) の混入でも 500 にしない (FE coerceWeeklyPattern の
        # typeof === 'string' ガードと同じく null 扱いに揃える).
        preferred_start = None
        preferred_end = None

    weekdays_raw = wp.get("preferred_weekdays")
    preferred_weekdays = frozenset(
        WEEKDAY_CODE_TO_INT[c]
        for c in (weekdays_raw if isinstance(weekdays_raw, list) else [])
        if isinstance(c, str) and c in WEEKDAY_CODE_TO_INT
    )

    return CandidateInput(
        lat=float(patient.lat),
        lng=float(patient.lng),
        service_minutes=service_minutes,
        time_type=time_type,
        preferred_start=preferred_start,
        preferred_end=preferred_end,
        preferred_weekdays=preferred_weekdays,
        requires_multiple_staff=bool(patient.requires_multiple_staff),
        existing_patient_id=patient.id,
        sex_restriction=patient.sex_restriction,
    )


def _pick_top_excluded_reason(excluded: list[ExcludedReasonSummary]) -> str | None:
    """除外理由集約から最多 reason を選ぶ (同数は _POOL_EXCLUSION_PRIORITY で決定的)."""
    if not excluded:
        return None

    def _priority_index(reason: str) -> int:
        try:
            return _POOL_EXCLUSION_PRIORITY.index(reason)
        except ValueError:
            return len(_POOL_EXCLUSION_PRIORITY)

    # 最多 count → 同数は優先度が高い (index が小さい) reason を選ぶ.
    best = max(excluded, key=lambda e: (e.count, -_priority_index(e.reason)))
    return best.reason


@router.post(
    "/v2/pool-overview",
    response_model=PoolOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Stage P-2: プール患者ごとの最良候補 (delta 最小) を軽量計算し効果順一覧を返す",
)
async def pool_overview_endpoint(
    payload: PoolOverviewRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PoolOverviewResponse:
    """保留プールの患者それぞれの「最良候補 1 件」を軽量計算して返す read-only API.

    本 endpoint は **read-only**: DB を変更しない.

    候補生成は propose-slots と完全に同一 (``compute_all_proposed_slots`` を再利用) で、
    best_delta_minutes は propose-slots 単体呼び出しの先頭候補と一致する. 週バケット
    (実 Visit / スタッフ実態) と事業所別設定はループ外で 1 回だけロードし、N 患者ぶんの
    重複クエリを避ける (患者数に依らず DB クエリ回数はほぼ一定).
    """
    if not payload.patient_ids:
        return PoolOverviewResponse(items=[])

    office_ids: list[UUID] = [payload.office_id] if payload.office_id is not None else []

    # 週バケット (実 Visit + スタッフ実態) を 1 回だけロード (propose-slots と同じローダ).
    try:
        buckets, office_name_by_id, office_code_by_id = await load_week_course_buckets(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=office_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 事業所別設定も 1 回だけロード (full-optimize / propose-slots と同一 config).
    config = await load_scheduling_config(db)

    # プール患者をまとめて 1 クエリでロード (存在するものだけ処理; 重複 id は 1 回に集約).
    unique_ids = list(dict.fromkeys(payload.patient_ids))
    patient_rows = (
        await db.scalars(
            select(Patient).where(
                Patient.id.in_(unique_ids),
                Patient.deleted_at.is_(None),
            )
        )
    ).all()
    patient_by_id: dict[UUID, Patient] = {p.id: p for p in patient_rows}

    items: list[PoolOverviewItem] = []
    # 重複 id は 1 回だけ計算する (unique_ids は入力順保存の dedup).
    for pid in unique_ids:
        patient = patient_by_id.get(pid)
        if patient is None:
            continue  # 存在しない / 削除済み患者は俯瞰対象外 (黙って除外).

        candidate = _patient_to_pool_candidate(patient)
        if candidate is None:
            # 座標未確定 → 算出不能 (propose-slots の座標なし早期 return と同じく除外理由なし).
            items.append(
                PoolOverviewItem(
                    patient_id=pid,
                    best_slot=None,
                    best_delta_minutes=None,
                    candidate_count=0,
                    top_excluded_reason=None,
                )
            )
            continue

        # propose-slots と同一の候補生成 (効率代替は付けない = 既定 False で propose と同挙動).
        # 内部で上位 DELTA_EVAL_LIMIT 件のみ delta を厳密計算し delta 昇順に並ぶため、
        # results[0] が最良 (delta 最小) 候補になる.
        excluded_raw: list[ExcludedReasonSummary] = []
        results = compute_all_proposed_slots(
            buckets,
            office_name_by_id,
            candidate,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
            exclusions_out=excluded_raw,
        )

        if not results:
            items.append(
                PoolOverviewItem(
                    patient_id=pid,
                    best_slot=None,
                    best_delta_minutes=None,
                    candidate_count=0,
                    top_excluded_reason=_pick_top_excluded_reason(excluded_raw),
                )
            )
            continue

        best = results[0]
        items.append(
            PoolOverviewItem(
                patient_id=pid,
                best_slot=PoolOverviewBestSlot(
                    weekday=best.weekday,
                    course_code=best.course_code,
                    office_id=best.office_id,
                    start_time=f"{best.start.hour:02d}:{best.start.minute:02d}:{best.start.second:02d}",
                    end_time=f"{best.end.hour:02d}:{best.end.minute:02d}:{best.end.second:02d}",
                ),
                best_delta_minutes=best.marginal_cost_minutes,
                candidate_count=len(results),
                top_excluded_reason=None,
            )
        )

    return PoolOverviewResponse(items=items)


# ---------------------------------------------------------------------------
# pool-bulk-simulate (W-1: プール一括投入の逐次シミュレーション / read-only)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/pool-bulk-simulate",
    response_model=PoolBulkSimulateResponse,
    status_code=status.HTTP_200_OK,
    summary="W-1: プール患者列を逐次シミュレーションで積み投入プレビューを返す (read-only)",
)
async def pool_bulk_simulate_endpoint(
    payload: PoolBulkSimulateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PoolBulkSimulateResponse:
    """プール一括投入の read-only プレビュー (設計書 §3-4).

    本 endpoint は **read-only**: DB を変更しない (apply=W-2 と完全分離).

    候補生成・delta は個別提案 (propose-slots) と同一 (``compute_all_proposed_slots`` 再利用)
    で、1 人目の投入先は pool-overview の best と厳密一致する. 患者を D-1 ハイブリッド順序で
    1 人ずつメモリ内バケットに積み、先行患者が埋めた枠は後続患者のソルバ走査で自然に弾かれる
    (= 調停の実体). 上限 (POOL_BULK_MAX_PATIENTS=50) 超過は schema validator が 422.
    """
    config = await load_scheduling_config(db)

    try:
        result = await simulate_pool_bulk_insert(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
            patient_ids=payload.patient_ids,
            config=config,
            candidate_of=_patient_to_pool_candidate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # before/after 週ビューは既存 full-optimize と同一ヘルパで組む (ProposalWeekCalendar 互換).
    week_before_after = _build_weekday_before_after(
        result.before_visits,
        result.after_visits,
        office_name_by_id=result.office_name_by_id,
    )

    return PoolBulkSimulateResponse(
        placements=[
            PoolBulkPlacement(
                seq=p.seq,
                patient_id=p.patient_id,
                patient_name=p.patient_name,
                weekday=p.weekday,
                course_code=p.course_code,
                office_id=p.office_id,
                start_time=f"{p.start.hour:02d}:{p.start.minute:02d}:{p.start.second:02d}",
                service_minutes=p.service_minutes,
                delta_minutes=p.delta_minutes,
                warnings=p.warnings,
            )
            for p in result.placements
        ],
        partial=[
            PoolBulkPartial(
                patient_id=pt.patient_id,
                patient_name=pt.patient_name,
                placed_days=pt.placed_days,
                missing_days=pt.missing_days,
                unplaced_reasons={str(wd): reason for wd, reason in pt.unplaced_reasons.items()},
                overcapacity_available_count=pt.overcapacity_available_count,
            )
            for pt in result.partial
        ],
        unplaced=[
            PoolBulkUnplaced(
                patient_id=u.patient_id,
                patient_name=u.patient_name,
                reason=u.reason,
                overcapacity_available_count=u.overcapacity_available_count,
            )
            for u in result.unplaced
        ],
        week_before_after=week_before_after,
        kpi=PoolBulkKpi(
            placed_patients=result.placed_patients,
            placed_slots=result.placed_slots,
            travel_minutes_before=result.travel_minutes_before,
            travel_minutes_after=result.travel_minutes_after,
            travel_km_before=result.travel_km_before,
            travel_km_after=result.travel_km_after,
        ),
        state_token=result.state_token,
    )


# ---------------------------------------------------------------------------
# pool-bulk-apply (W-2: プール一括投入の 1TX 適用)
# ---------------------------------------------------------------------------


def _parse_placement_start(s: str) -> time_cls:
    """placement の start_time ("HH:MM:SS" / "HH:MM") を time に変換する.

    simulate の placements は ``"%H:%M:%S"`` 形式 (pool-bulk-simulate 出力) だが、
    "HH:MM" も許容する (秒は 0 埋め). 不正形式は ValueError → 422 変換.
    """
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError(f"start_time は HH:MM(:SS) 形式が必要です: {s!r}")
    h = int(parts[0])
    m = int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    return time_cls(h, m, sec)


@router.post(
    "/v2/pool-bulk-apply",
    response_model=PoolBulkApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="W-2: プール一括投入の placements を固定訪問週間に 1TX で登録する",
)
async def pool_bulk_apply_endpoint(
    payload: PoolBulkApplyRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PoolBulkApplyResponse:
    """simulate の placements を **1 トランザクション**で固定訪問週間 (PFV) に登録する.

    設計書 §4 / D-2 (反映先 = pattern_and_week 固定):

    - **楽観ロック**: ``compute_bulk_state_token`` を再計算し、不一致なら 409
      (simulate 後にスケジュールが変わったら必ずやり直し).
    - **1TX / all-or-nothing**: 患者ごとに 既存 normal PFV を保持したまま placements の枠を
      **追加** (全置換ではない) → ``apply_individual_proposal`` (V2 pinned 違反=422 で全体
      rollback / V3-V5 は warnings) → ``reset_visits_to_fixed`` で今週 visits を再生成 →
      明示 flush. 1 人でも 422 なら全体を rollback する.
    - **監査**: 適用サマリを ``AuditLog`` に記録する. 一括投入は Ctrl+Z (undo) 対象外
      (設計書 §7・§5.3 バナー) のため ``schedule_op_log`` (undo/redo スタック) には記録しない
      — 同モデルは「undo 不可エントリ」を構造的に表現できず (undoable 列が無い / migration
      追加禁止)、記録すると undo API がこれを誤って undo 対象として拾ってしまうため、
      scope-optimization apply と同じ ``AuditLog`` 監査方式に統一する.
    - session は autoflush=False のため、患者ごとに明示 flush して後続患者の SELECT へ
      DB レベルで反映する (逐次適用の教訓).
    """
    config = await load_scheduling_config(db)

    # 1. 楽観ロック: simulate 時と同一規約で state_token を再計算し、不一致なら 409.
    current_token = await compute_bulk_state_token(
        db,
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        office_id=payload.office_id,
    )
    if current_token != payload.state_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="シミュレーション後にスケジュールが変更されました。再計算してください",
        )

    if not payload.placements:
        # 適用対象なし (空 placements). read-only ではないが no-op で 200 を返す.
        return PoolBulkApplyResponse(applied_patients=0, applied_slots=0, warnings=[])

    # 2. placements を患者ごとにまとめる (入力順を保持した決定的グルーピング).
    by_patient: dict[UUID, list[PoolBulkPlacement]] = {}
    name_by_patient: dict[UUID, str] = {}
    for pl in payload.placements:
        by_patient.setdefault(pl.patient_id, []).append(pl)
        name_by_patient.setdefault(pl.patient_id, pl.patient_name)

    warnings: list[str] = []
    applied_patients = 0
    applied_slots = 0

    try:
        for patient_id, pls in by_patient.items():
            # 既存 normal PFV (slot_index=0) を保持したまま追加するため、既存曜日を
            # visit_plans に含めて全置換を防ぐ (apply_individual_proposal は visit_plans を
            # PFV の全集合として扱い、含まれない曜日を削除するため).
            existing_rows = (
                await db.scalars(
                    select(PatientFixedVisit).where(
                        PatientFixedVisit.patient_id == patient_id,
                        PatientFixedVisit.mode == "normal",
                        PatientFixedVisit.slot_index == 0,
                    )
                )
            ).all()
            proposed_by_wd: dict[int, tuple[time_cls, int]] = {
                r.weekday: (r.start_time, r.duration_min) for r in existing_rows
            }
            # placements の枠を追加 (同曜日に既存 PFV があれば apply-individual の規約どおり
            # 上書き = upsert).
            for pl in pls:
                st = _parse_placement_start(pl.start_time)
                proposed_by_wd[pl.weekday] = (st, pl.service_minutes)

            visit_plans = [
                {"weekday": wd, "start_time": st, "duration_min": dur}
                for wd, (st, dur) in sorted(proposed_by_wd.items())
            ]
            result = await apply_individual_proposal(
                db, patient_id=patient_id, visit_plans=visit_plans, config=config
            )
            p_name = name_by_patient.get(patient_id, str(patient_id))
            warnings.extend(f"{p_name}: {w}" for w in result.get("warnings", []))

            # 今週 visits を PFV から再生成する (reset_visits_to_fixed の 1 患者版).
            office_ids = await resolve_reset_office_ids(db, patient_id)
            await reset_visits_to_fixed(
                db,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                office_ids=office_ids,
                mode="legacy",
                dry_run=False,
                config=config,
                patient_id=patient_id,
            )
            # autoflush=False のため明示 flush: 後続患者の SELECT / 容量調停へ反映する.
            await db.flush()
            applied_patients += 1
            applied_slots += len(pls)

        # 監査ログ (適用サマリ). undo 対象外のため schedule_op_log ではなく AuditLog.
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="pool_bulk_apply",
                target_table="patient_fixed_visits",
                target_id=f"{payload.iso_year}-W{payload.iso_week}",
                before={},
                after={
                    "office_id": str(payload.office_id),
                    "applied_patients": applied_patients,
                    "applied_slots": applied_slots,
                    "change_scope": "pattern_and_week",
                },
            )
        )
        await db.commit()
    except HTTPException:
        # apply_individual_proposal の pinned 違反 (422) 等は全体 rollback (all-or-nothing).
        await db.rollback()
        raise
    except CrossAddressTimeConflictError as exc:
        await db.rollback()
        logger.warning(
            "pool_bulk_apply: unresolvable same-time conflict (missing coord): conflicts=%d",
            len(exc.conflicts),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "same_time_conflict_with_other_patient",
                "message": (
                    f"{len(exc.conflicts)} 件の解消不能な同時刻衝突を検出, 一括投入を中止しました. "
                    "対象患者の座標 (lat/lng) または primary_office を確認してください。"
                ),
                "conflicts": exc.conflicts[:10],
            },
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("pool_bulk_apply: integrity error (likely concurrent apply): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じスケジュールを処理中です。もう一度実行してください。",
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception:
        await db.rollback()
        raise

    logger.info("pool_bulk_apply: success patients=%d slots=%d", applied_patients, applied_slots)
    return PoolBulkApplyResponse(
        applied_patients=applied_patients,
        applied_slots=applied_slots,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# travel-estimate (Phase G-84 A-1: 空き枠直接配置の移動時間提案 = 案2の根拠)
# ---------------------------------------------------------------------------


@router.post(
    "/v2/travel-estimate",
    response_model=TravelEstimateResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase G-84: 直前訪問から候補住所までの移動時間を推定",
)
async def travel_estimate_endpoint(
    payload: TravelEstimateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> TravelEstimateResponse:
    """空き枠直接配置 (案2) 用に from→to の移動時間を推定する read-only API.

    本 endpoint は **read-only**: DB を変更しない.

    座標解決:
        - from 座標: ``from_patient_id`` → ``patients.lat/lng``、無ければ
          ``from_lat/lng`` フォールバック.
        - to 座標: ``to_lat/lng`` を優先、無ければ ``geocode_address(to_address)``
          を best-effort (失敗 / 例外は座標未確定として握り潰す).
        - 両方確定: ``travel = haversine_minutes(haversine_km(...))`` /
          ``total = travel + VISIT_BUFFER_MINUTES``.
        - どちらか未確定: minutes は null (フロントは案1=枠頭にフォールバック).

    距離 / 移動時間 / バッファーは ``proposal_solver`` の既存ユーティリティを
    そのまま再利用する (自動割当 / 提案と同一の数値).
    """
    # 1. from 座標を確定する.
    from_lat: float | None = None
    from_lng: float | None = None
    if payload.from_patient_id is not None:
        patient = await db.scalar(
            select(Patient).where(
                Patient.id == payload.from_patient_id,
                Patient.deleted_at.is_(None),
            )
        )
        if patient is not None and patient.lat is not None and patient.lng is not None:
            from_lat = float(patient.lat)
            from_lng = float(patient.lng)
    if (from_lat is None or from_lng is None) and (
        payload.from_lat is not None and payload.from_lng is not None
    ):
        from_lat = payload.from_lat
        from_lng = payload.from_lng

    # 2. to 座標を確定する (lat/lng 優先 → geocode best-effort).
    to_lat: float | None = None
    to_lng: float | None = None
    if payload.to_lat is not None and payload.to_lng is not None:
        to_lat = payload.to_lat
        to_lng = payload.to_lng
    elif payload.to_address and payload.to_address.strip():
        try:
            geo = await geocode_address(payload.to_address)
        except Exception as exc:  # noqa: BLE001 - best-effort: minutes=null で続行
            logger.warning("travel-estimate geocode failed (best-effort): %s", exc)
            geo = None
        if geo is not None:
            to_lat = geo.lat
            to_lng = geo.lng

    from_resolved = from_lat is not None and from_lng is not None
    to_resolved = to_lat is not None and to_lng is not None

    # 3. 両方確定なら移動時間 + バッファーを算出. 片方でも未確定なら null.
    travel_minutes: int | None = None
    total_minutes: int | None = None
    if from_resolved and to_resolved:
        travel_minutes = haversine_minutes(
            haversine_km(from_lat, from_lng, to_lat, to_lng)  # type: ignore[arg-type]
        )
        total_minutes = travel_minutes + VISIT_BUFFER_MINUTES

    return TravelEstimateResponse(
        from_resolved=from_resolved,
        to_resolved=to_resolved,
        travel_minutes=travel_minutes,
        buffer_minutes=VISIT_BUFFER_MINUTES,
        total_minutes=total_minutes,
    )


# ---------------------------------------------------------------------------
# board (Phase2-3a: モバイル現場ボード /m 用の週ボード read API)
# ---------------------------------------------------------------------------


def _board_course_to_schema(
    course: BoardCourseData,
) -> BoardCourse:
    """``BoardCourseData`` → API schema ``BoardCourse`` (実時刻 + 容量集計).

    cancelled は visits[] に含まれるが、定員 (filled/total_minutes/remaining) からは除外する。
    """
    visits_out = [
        BoardVisit(
            visit_id=bv.visit_id,
            patient_id=bv.patient_id,
            patient_name=bv.patient_name,
            patient_kana=bv.patient_kana,
            insurance=bv.insurance,  # type: ignore[arg-type]
            service_minutes=bv.service_minutes,
            start_time=f"{bv.start_time.hour:02d}:{bv.start_time.minute:02d}",
            end_time=f"{bv.end_time.hour:02d}:{bv.end_time.minute:02d}",
            address=bv.address,
            lat=bv.lat,
            lng=bv.lng,
            same_address_group_id=bv.same_address_group_id,
            mode=bv.mode,  # type: ignore[arg-type]
            slot_index=bv.slot_index,
            status=bv.status,
        )
        for bv in course.visits
    ]
    # cancelled は定員・時間合計に含めない (枠は空き扱い).
    planned_visits = [bv for bv in course.visits if bv.status != "cancelled"]
    filled = len(planned_visits)
    total_minutes = sum(bv.service_minutes for bv in planned_visits)
    return BoardCourse(
        course_id=course.course_id,
        course_code=course.course_code,
        course_label=f"{_board_office_short(course.office_code)}{course.course_code}",
        staff_name=course.staff_name,
        visits=visits_out,
        capacity=BoardCapacity(
            filled=filled,
            max=MAX_PATIENTS_PER_COURSE,
            total_minutes=total_minutes,
            remaining=max(0, MAX_PATIENTS_PER_COURSE - filled),
        ),
    )


@router.get(
    "/v2/board",
    response_model=BoardResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase2-3a: モバイル現場ボード /m 用の週ボード (実 Visit) を返す",
)
async def board_endpoint(
    db: DbDep,
    # 現場ボードは全ロール閲覧可 (staff は FE 側で閲覧専用 UI)。編集系 API は別途 admin/manager。
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    iso_year: int = Query(..., ge=2020, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    office_id: UUID | None = Query(
        default=None,
        description="単一拠点に絞る場合に指定. 未指定なら全 active 拠点を対象.",
    ),
) -> BoardResponse:
    """対象週 × 拠点の実 Visit を office × weekday × course 構造で返す read-only API.

    実 Visit 由来の **実時刻** (start/end) をそのまま返す (モックの空き枠は出さない).
    休講曜日 (その拠点・曜日にスタッフ 0 名) は courses を空にし closed=True とする.
    """
    # 対象拠点: office_id 指定なら単一、未指定なら全 active 拠点.
    if office_id is not None:
        office_ids = [office_id]
    else:
        rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(rows.all())

    # 実 Visit を 1 回ロードしてコース単位に集計 (同住所 group_id 付与済).
    # include_cancelled=True: キャンセル済み visit もボードに表示する（表示のみ・定員除外）.
    try:
        buckets, office_name_by_id, _office_code_by_id = await load_board_buckets(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            office_ids=office_ids,
            include_cancelled=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # (office, weekday) のスタッフ数 (休講判定 + ヘッダー集計).
    staff_counts, manager_counts = await load_weekday_staff_counts(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        office_ids=office_ids,
    )

    # 対象拠点: 明示指定 office_ids を基本にしつつ、visit / staff に出た office も含める.
    target_office_ids: set[UUID] = set(office_ids)
    target_office_ids |= {b.office_id for b in buckets.values()}
    target_office_ids |= {oid for (oid, _wd) in staff_counts}
    target_office_ids |= {oid for (oid, _wd) in manager_counts}
    if office_id is not None:
        # 単一指定時はその拠点に限定する.
        target_office_ids = {office_id}

    # offices[] は安定順 (拠点名 → id) で返す.
    offices_sorted = sorted(
        target_office_ids,
        key=lambda oid: (office_name_by_id.get(oid, ""), str(oid)),
    )
    offices_out = [
        BoardOffice(office_id=oid, office_name=office_name_by_id.get(oid, ""))
        for oid in offices_sorted
    ]

    # weekdays[] (曜日ヘッダー: 日付 + 全拠点合計患者数). cancelled は除外.
    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    patients_per_weekday: dict[int, int] = {}
    for b in buckets.values():
        planned_count = sum(1 for v in b.visits if v.status != "cancelled")
        patients_per_weekday[b.weekday] = patients_per_weekday.get(b.weekday, 0) + planned_count
    weekdays_out = [
        BoardWeekday(
            weekday=wd,
            weekday_code=WEEKDAY_INT_TO_CODE[wd],  # type: ignore[arg-type]
            date=(week_monday + timedelta(days=wd)).isoformat(),
            patient_count=patients_per_weekday.get(wd, 0),
        )
        for wd in range(7)
    ]

    # board[]: office × weekday ごとに courses をまとめる.
    courses_by_cell: dict[tuple[UUID, int], list[BoardCourseData]] = {}
    for b in buckets.values():
        courses_by_cell.setdefault((b.office_id, b.weekday), []).append(b)

    board_out: list[BoardCell] = []
    for oid in offices_sorted:
        for wd in range(7):
            staff_count = staff_counts.get((oid, wd), 0)
            manager_count = manager_counts.get((oid, wd), 0)
            cell_courses = courses_by_cell.get((oid, wd), [])
            # 休講: スタッフ 0 名 (= コース開講なし). 実 visit が無くても courses 空.
            closed = staff_count == 0 and manager_count == 0
            courses_schema = [
                _board_course_to_schema(c)
                for c in sorted(cell_courses, key=lambda c: (c.course_code,))
            ]
            # cancelled を patient_count に含めない (枠は空き扱い).
            patient_count = sum(
                sum(1 for v in c.visits if v.status != "cancelled") for c in cell_courses
            )
            board_out.append(
                BoardCell(
                    office_id=oid,
                    weekday=wd,
                    weekday_code=WEEKDAY_INT_TO_CODE[wd],  # type: ignore[arg-type]
                    closed=closed,
                    staff_count=staff_count,
                    manager_count=manager_count,
                    patient_count=patient_count,
                    courses=courses_schema,
                )
            )

    return BoardResponse(
        iso_year=iso_year,
        iso_week=iso_week,
        course_max=MAX_PATIENTS_PER_COURSE,
        offices=offices_out,
        weekdays=weekdays_out,
        board=board_out,
    )


# ---------------------------------------------------------------------------
# improvement-suggestions (P2-B: 配置済み患者の改善提案 + 却下記憶)
# ---------------------------------------------------------------------------


def _hhmm(t: time_cls) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _swap_counterpart_of(c: ImprovementCandidateData) -> SwapCounterpart | None:
    """kind='swap' の内部表現から SwapCounterpart schema を組む (それ以外は None)."""
    if c.kind != "swap" or c.swap_counterpart_patient_id is None:
        return None
    assert c.swap_counterpart_current_start is not None
    assert c.swap_counterpart_new_start is not None
    assert c.swap_counterpart_current_weekday is not None
    assert c.swap_counterpart_new_weekday is not None
    return SwapCounterpart(
        patient_id=c.swap_counterpart_patient_id,
        patient_name=c.swap_counterpart_name or "",
        current_weekday=c.swap_counterpart_current_weekday,
        current_start_time=_hhmm(c.swap_counterpart_current_start),
        new_weekday=c.swap_counterpart_new_weekday,
        new_start_time=_hhmm(c.swap_counterpart_new_start),
        requires_patient_confirmation=c.swap_counterpart_requires_confirmation,
        within_preference=c.swap_counterpart_within_preference,
    )


def _improvement_to_schema(c: ImprovementCandidateData) -> ImprovementSuggestion:
    """内部表現 ``ImprovementCandidateData`` を API schema へ変換."""
    return ImprovementSuggestion(
        kind=c.kind,  # type: ignore[arg-type]
        target_weekday=c.target_weekday,
        current=ImprovementCurrentSlot(
            office_id=c.current_office_id,
            weekday=c.current_weekday,
            weekday_code=WEEKDAY_INT_TO_CODE[c.current_weekday],  # type: ignore[arg-type]
            start_time=f"{c.current_start.hour:02d}:{c.current_start.minute:02d}",
            end_time=f"{c.current_end.hour:02d}:{c.current_end.minute:02d}",
            course_label=c.current_course_label,
            staff_name=c.current_staff_name,
        ),
        candidate=ImprovementCandidateSlot(
            office_id=c.cand_office_id,
            office_name=c.cand_office_name,
            weekday=c.cand_weekday,
            weekday_code=WEEKDAY_INT_TO_CODE[c.cand_weekday],  # type: ignore[arg-type]
            start_time=f"{c.cand_start.hour:02d}:{c.cand_start.minute:02d}",
            end_time=f"{c.cand_end.hour:02d}:{c.cand_end.minute:02d}",
            course_code=c.cand_course_code,
            course_label=c.cand_course_label,
            staff_name=c.cand_staff_name,
        ),
        delta=ImprovementDelta(
            travel_minutes_saved=c.delta_minutes,
            travel_km_saved=c.delta_km,
        ),
        changes=ImprovementChanges(changes=c.changes, unchanged=c.unchanged),
        staff_warnings=c.staff_warnings,
        event_conflicts=c.event_conflicts,
        feasibility_basis="pfv",
        requires_patient_confirmation=c.requires_patient_confirmation,
        within_preference=c.within_preference,
        swap_counterpart=_swap_counterpart_of(c),
        # UI 統一: タイムライン表示用スナップショット (改善提案経路で populate。
        # scope simulate 経路の candidate は None で、step 側のスナップショットを使う).
        source_course=_scope_snapshot_to_schema(c.source_course),
        destination_course=_scope_snapshot_to_schema(c.destination_course),
        reason=c.reason,
    )


@router.get(
    "/v2/improvement-suggestions",
    response_model=ImprovementSuggestionsResponse,
    status_code=status.HTTP_200_OK,
    summary="P2-B: 配置済み患者の改善提案 (限界コスト方式) を返す",
)
async def improvement_suggestions_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    patient_id: Annotated[UUID, Query(description="対象患者 ID")],
    iso_year: Annotated[int, Query(ge=2020, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
) -> ImprovementSuggestionsResponse:
    """対象患者の各固定枠について、移動を縮められる入れ替え候補を返す read-only API.

    本 endpoint は **read-only**: DB を変更しない.

    0 件でも 200 + ``filtered_summary`` (pinned/locked/dismissed/below_threshold/
    day_restricted の内訳 = N-6「黙って消さない」) を返す.
    """
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    config = await load_scheduling_config(db)
    try:
        suggestions, summary = await find_improvement_candidates(
            db,
            patient=patient,
            iso_year=iso_year,
            iso_week=iso_week,
            config=config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ImprovementSuggestionsResponse(
        patient_id=patient_id,
        iso_year=iso_year,
        iso_week=iso_week,
        suggestions=[_improvement_to_schema(c) for c in suggestions],
        filtered_summary=ImprovementFilteredSummary(
            pinned=summary.pinned,
            locked=summary.locked,
            no_current_visit=summary.no_current_visit,
            dismissed=summary.dismissed,
            below_threshold=summary.below_threshold,
            day_restricted=summary.day_restricted,
        ),
    )


# 却下理由 → 可動域昇格先 (設計書 §2.3). is_pinned=true 行は昇格せず locked のまま.
_PROMOTE_TARGET: dict[str, str] = {
    "day_immovable": "time_flexible",
    "time_immovable": "locked",
}


@router.post(
    "/v2/improvement-suggestions/dismiss",
    response_model=ImprovementDismissResponse,
    status_code=status.HTTP_200_OK,
    summary="P2-B: 改善提案を却下記憶に upsert (任意で可動域昇格)",
)
async def improvement_dismiss_endpoint(
    payload: ImprovementDismissRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ImprovementDismissResponse:
    """改善提案の却下を記録する.

    同一指紋 ``(patient_id, kind, target_weekday)`` は既存行を upsert (reason 更新).
    ``promote_movability=true`` かつ reason が day_immovable / time_immovable のときのみ、
    該当 PFV の movability を昇格する (is_pinned=true の行は locked のまま = 昇格しない).
    """
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == payload.patient_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # 指紋 (patient_id, kind, target_weekday) で upsert.
    # UniqueConstraint (uq_sd_fingerprint) により TOCTOU 競合は DB 側で検出される.
    _fp_filter = [
        SuggestionDismissal.patient_id == payload.patient_id,
        SuggestionDismissal.kind == payload.kind,
        SuggestionDismissal.target_weekday == payload.target_weekday,
    ]
    now = datetime.now(UTC)
    existing = await db.scalar(select(SuggestionDismissal).where(*_fp_filter))
    if existing is not None:
        # UPDATE: reason / dismissed_by / dismissed_at を最新化.
        existing.reason = payload.reason
        existing.reason_note = payload.reason_note
        existing.dismissed_by = actor.id
        existing.dismissed_at = now
        dismissal = existing
        await db.flush()
    else:
        # INSERT: TOCTOU 競合で UniqueConstraint 違反が起きたら rollback → re-SELECT → UPDATE (1 retry).
        new_row = SuggestionDismissal(
            patient_id=payload.patient_id,
            kind=payload.kind,
            target_weekday=payload.target_weekday,
            reason=payload.reason,
            reason_note=payload.reason_note,
            dismissed_by=actor.id,
        )
        db.add(new_row)
        try:
            await db.flush()
            dismissal = new_row
        except IntegrityError:
            # 競合リクエストが同一指紋を先にINSERT → rollback して既存行をUPDATE.
            await db.rollback()
            existing = await db.scalar(select(SuggestionDismissal).where(*_fp_filter))
            if existing is None:
                raise  # DB 不整合: 再 raise して 500 に.
            existing.reason = payload.reason
            existing.reason_note = payload.reason_note
            existing.dismissed_by = actor.id
            existing.dismissed_at = now
            dismissal = existing
            await db.flush()

    # 可動域昇格 (人間確認後にのみ). is_pinned=true 行は locked のまま.
    movability_updated = False
    new_movability: str | None = None
    if payload.promote_movability and payload.reason in _PROMOTE_TARGET:
        target = _PROMOTE_TARGET[payload.reason]
        rows = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == payload.patient_id,
                    PatientFixedVisit.mode == "normal",
                    PatientFixedVisit.weekday == payload.target_weekday,
                )
            )
        ).all()
        for row in rows:
            if row.is_pinned:
                continue  # is_pinned ⇒ locked のまま (昇格しない).
            if row.movability != target:
                row.movability = target
                movability_updated = True
        if movability_updated:
            new_movability = target

    await db.commit()

    return ImprovementDismissResponse(
        dismissal_id=dismissal.id,
        movability_updated=movability_updated,
        new_movability=new_movability,
    )


# ---------------------------------------------------------------------------
# apply-swap (P3-②: 2 患者の入れ替えを 1 TX で適用)
# ---------------------------------------------------------------------------


def _pfv_to_base(row: PatientFixedVisit) -> PatientFixedVisitV2Base:
    """既存 PFV 行を再検証用 schema (PatientFixedVisitV2Base) に写す (無変更)."""
    return PatientFixedVisitV2Base(
        weekday=row.weekday,
        start_time=row.start_time,
        duration_min=row.duration_min,
        course_template_id=row.course_template_id,
        sub_office_id=row.sub_office_id,
        slot_index=row.slot_index,
        is_pinned=row.is_pinned,
        movability=row.movability,
    )


def _proposed_with_move(
    rows: list[PatientFixedVisit],
    *,
    moving_id: UUID,
    new_weekday: int,
    new_start: time_cls,
    new_course: UUID | None,
) -> list[PatientFixedVisitV2Base]:
    """患者の全 PFV を base 化しつつ、移動行のみ新位置に差し替える (再検証入力)."""
    items: list[PatientFixedVisitV2Base] = []
    for row in rows:
        if row.id == moving_id:
            items.append(
                PatientFixedVisitV2Base(
                    weekday=new_weekday,
                    start_time=new_start,
                    duration_min=row.duration_min,
                    course_template_id=new_course,
                    sub_office_id=row.sub_office_id,
                    slot_index=0,
                    is_pinned=row.is_pinned,
                    movability=row.movability,
                )
            )
        else:
            items.append(_pfv_to_base(row))
    return items


async def _apply_pfv_move(
    db: DbDep,
    *,
    moving_row: PatientFixedVisit,
    all_rows: list[PatientFixedVisit],
    new_weekday: int,
    new_start: time_cls,
    new_course: UUID | None,
) -> None:
    """1 患者の slot0 移動を適用する (同曜日=更新 / 別曜日=旧行削除+移動先作成/更新).

    movability / is_pinned / duration / sub_office は移動行のものを保持する.

    course_template_id の意味論:
      ``new_course is None`` = 「省略 = 変更なし」. FE は counterpart 側 (b_new)
      の移動先コースを解決できないため省略してくる. 省略を無条件 None 代入すると
      layer1_expander の非 NULL 優先採用が壊れる. 3 分岐すべてで
      ``new_course if new_course is not None else moving_row.course_template_id``
      として既存値を保持する (後方互換オプション b).
    """
    # 省略(None) = 既存コースを保持. 明示値があれば上書き.
    resolved_course = new_course if new_course is not None else moving_row.course_template_id

    if new_weekday == moving_row.weekday:
        moving_row.start_time = new_start
        moving_row.course_template_id = resolved_course
        return

    # 別曜日移動: 移動先に既存 slot0 行があれば上書き, なければ移動行の曜日を付け替える.
    target = next(
        (
            r
            for r in all_rows
            if r.weekday == new_weekday and r.slot_index == 0 and r.id != moving_row.id
        ),
        None,
    )
    if target is not None:
        target.start_time = new_start
        target.course_template_id = resolved_course
        target.duration_min = moving_row.duration_min
        target.movability = moving_row.movability
        target.is_pinned = moving_row.is_pinned
        target.sub_office_id = moving_row.sub_office_id
        await db.delete(moving_row)
    else:
        moving_row.weekday = new_weekday
        moving_row.start_time = new_start
        moving_row.course_template_id = resolved_course


@router.post(
    "/v2/improvement-suggestions/apply-swap",
    response_model=ApplySwapResponse,
    status_code=status.HTTP_200_OK,
    summary="P3-②: 2 患者の固定枠を入れ替える (1 TX / all-or-nothing)",
)
async def improvement_apply_swap_endpoint(
    payload: ApplySwapRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ApplySwapResponse:
    """A の枠と B の枠を入れ替える. A は b の旧位置 (a_new) へ、B は a の旧位置 (b_new) へ.

    N-4 再検証: pinned 枠の移動は 422. validate_pfv_changes の warning
    (患者間衝突 / 昼休み / 容量) と両者の新位置同士の相互衝突はブロックせず warnings に載せる.
    all-or-nothing (1 トランザクション).
    """
    # 同一患者を両方に指定するのは論理エラー (早期ガード).
    if payload.patient_a_id == payload.patient_b_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_a_id と patient_b_id に同一患者は指定できません",
        )

    try:
        a_new_start = _parse_hhmm(payload.a_new.start_time)
        b_new_start = _parse_hhmm(payload.b_new.start_time)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if a_new_start is None or b_new_start is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a_new.start_time / b_new.start_time は HH:MM 必須です",
        )

    pa = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_a_id, Patient.deleted_at.is_(None))
    )
    pb = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_b_id, Patient.deleted_at.is_(None))
    )
    if pa is None or pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # 各患者の旧枠曜日は相手の新枠曜日から導出する (swap 不変量).
    a_old_weekday = payload.b_new.weekday
    b_old_weekday = payload.a_new.weekday

    a_all = list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == payload.patient_a_id,
                    PatientFixedVisit.mode == "normal",
                )
            )
        ).all()
    )
    b_all = list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == payload.patient_b_id,
                    PatientFixedVisit.mode == "normal",
                )
            )
        ).all()
    )
    a_row = next((r for r in a_all if r.weekday == a_old_weekday and r.slot_index == 0), None)
    b_row = next((r for r in b_all if r.weekday == b_old_weekday and r.slot_index == 0), None)
    if a_row is None or b_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="対象の固定枠 (slot0) が見つかりません",
        )

    # N-4 再検証 (read-only). pinned 保護違反は適用前に 422 で返す.
    config = await load_scheduling_config(db)
    a_proposed = _proposed_with_move(
        a_all,
        moving_id=a_row.id,
        new_weekday=payload.a_new.weekday,
        new_start=a_new_start,
        new_course=payload.a_new.course_template_id,
    )
    b_proposed = _proposed_with_move(
        b_all,
        moving_id=b_row.id,
        new_weekday=payload.b_new.weekday,
        new_start=b_new_start,
        new_course=payload.b_new.course_template_id,
    )
    va = await validate_pfv_changes(db, payload.patient_a_id, a_proposed, "normal", config=config)
    vb = await validate_pfv_changes(db, payload.patient_b_id, b_proposed, "normal", config=config)
    if va.has_errors or vb.has_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "ピン留めされた枠は入れ替えできません",
                "violations": [
                    {
                        "code": w.code,
                        "message": w.message,
                        "weekday": w.weekday,
                        "severity": w.severity,
                    }
                    for w in (*va.errors, *vb.errors)
                ],
            },
        )
    warnings: list[str] = [w.message for w in va.warnings_only]
    warnings.extend(w.message for w in vb.warnings_only)

    # 両者の新位置同士の相互衝突 (同曜日・同コース) をブロックせず検査する.
    # validate_pfv_changes は相手の「現在」DB 行しか見ないため、新位置同士は個別に見る.
    if (
        payload.a_new.weekday == payload.b_new.weekday
        and payload.a_new.course_template_id == payload.b_new.course_template_id
        and pa.lat is not None
        and pa.lng is not None
        and pb.lat is not None
        and pb.lng is not None
    ):
        a_ev = ExistingVisit(
            start_time=a_new_start,
            end_time=_add_minutes(a_new_start, a_row.duration_min),
            lat=float(pa.lat),
            lng=float(pa.lng),
            service_minutes=a_row.duration_min,
            patient_id=str(payload.patient_a_id),
        )
        b_ev = ExistingVisit(
            start_time=b_new_start,
            end_time=_add_minutes(b_new_start, b_row.duration_min),
            lat=float(pb.lat),
            lng=float(pb.lng),
            service_minutes=b_row.duration_min,
            patient_id=str(payload.patient_b_id),
        )
        if (
            _find_conflict(a_ev, [b_ev], config=config) is not None
            or _find_conflict(b_ev, [a_ev], config=config) is not None
        ):
            warnings.append("入れ替え後の 2 枠が移動時間を含めて重なる可能性があります。")

    # Wave U-2 (§2.2 反映先の統一): week_only は PFV を触らず今週 visits にのみ反映する.
    # pinned 検証 (va/vb) は上で全 scope 共通に実行済み.
    week_only = payload.change_scope == "week_only"
    week_counters: dict[str, Any] = {"visits": 0, "patients": set()}

    # 適用 (1 TX / all-or-nothing). 片方でも失敗すれば rollback.
    if week_only:
        # B: PFV は不変. 両側の今週 visit を相互の新位置へ移動 (source='manual_week').
        await _apply_visit_move_week_only(
            db,
            patient_id=payload.patient_a_id,
            old_weekday=a_old_weekday,
            old_start=a_row.start_time,
            new_weekday=payload.a_new.weekday,
            new_start=a_new_start,
            new_course=payload.a_new.course_template_id,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            counters=week_counters,
        )
        await _apply_visit_move_week_only(
            db,
            patient_id=payload.patient_b_id,
            old_weekday=b_old_weekday,
            old_start=b_row.start_time,
            new_weekday=payload.b_new.weekday,
            new_start=b_new_start,
            new_course=payload.b_new.course_template_id,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            counters=week_counters,
        )
    else:
        await _apply_pfv_move(
            db,
            moving_row=a_row,
            all_rows=a_all,
            new_weekday=payload.a_new.weekday,
            new_start=a_new_start,
            new_course=payload.a_new.course_template_id,
        )
        await _apply_pfv_move(
            db,
            moving_row=b_row,
            all_rows=b_all,
            new_weekday=payload.b_new.weekday,
            new_start=b_new_start,
            new_course=payload.b_new.course_template_id,
        )

    # Wave U-2 (§2.2 A = pattern_and_week): PFV 入れ替え後、両患者の今週 visits を
    # PFV から再生成する (同一 TX / all-or-nothing). 先の PFV 変更を DB に反映するため flush.
    week_sync: SwapWeekSync | None = None
    if payload.change_scope == "pattern_and_week":
        await db.flush()
        total_regen = 0
        total_del = 0
        for pid in (payload.patient_a_id, payload.patient_b_id):
            office_ids = await resolve_reset_office_ids(db, pid)
            reset_result = await reset_visits_to_fixed(
                db,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                office_ids=office_ids,
                mode="legacy",
                dry_run=False,
                config=config,
                patient_id=pid,
            )
            total_regen += int(reset_result.get("visits_regenerated", 0))
            total_del += int(reset_result.get("visits_soft_deleted", 0))
        week_sync = SwapWeekSync(
            patients=2,
            visits_regenerated=total_regen,
            visits_soft_deleted=total_del,
        )
    elif week_only:
        week_sync = SwapWeekSync(
            patients=len(week_counters["patients"]),
            visits_regenerated=int(week_counters["visits"]),
            visits_soft_deleted=0,
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="入れ替え適用が既存の固定枠と競合しました",
        ) from exc

    return ApplySwapResponse(
        applied=True,
        warnings=warnings,
        change_scope=payload.change_scope,
        week_sync=week_sync,
    )


# ---------------------------------------------------------------------------
# scope-optimization (範囲最適化 W1: 選択範囲の一括改善シミュレーション)
# ---------------------------------------------------------------------------


def _scope_metrics_to_schema(m: ScopeMetricsData) -> ScopeOptimizationMetrics:
    return ScopeOptimizationMetrics(
        visit_count=m.visit_count,
        travel_minutes=m.travel_minutes,
        travel_km=m.travel_km,
        buffer_minutes=m.buffer_minutes,
        gap_minutes=m.gap_minutes,
    )


def _to_optimization_scope(s: ScopeOptimizationScopeSchema) -> OptimizationScope:
    """API schema → エンジン内部表現 (frozenset 化)."""
    return OptimizationScope(
        office_id=s.office_id,
        weekdays=frozenset(s.weekdays) if s.weekdays is not None else None,
        course_codes=(frozenset(s.course_codes) if s.course_codes is not None else None),
    )


def _validate_search_contains_focus(
    focus: ScopeOptimizationScopeSchema, search: ScopeOptimizationScopeSchema
) -> None:
    """§10: 探索範囲がフォーカスを包含することを検証する (違反は 422)."""
    if search.office_id != focus.office_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="探索範囲はフォーカスと同一拠点である必要があります",
        )

    def _contains(sup: list | None, sub: list | None) -> bool:
        if sup is None:  # None = 全部 → 何でも包含.
            return True
        if sub is None:  # フォーカスが全部なのに探索が限定 → 包含しない.
            return False
        return set(sub) <= set(sup)

    if not _contains(search.weekdays, focus.weekdays) or not _contains(
        search.course_codes, focus.course_codes
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="探索範囲はフォーカス (対策を練りたい範囲) を包含する必要があります",
        )


def _scope_snapshot_to_schema(
    s: ScopeCourseSnapshotData | None,
) -> ScopeCourseSnapshot | None:
    """コーススナップショット (内部表現) を API schema へ変換 (None はそのまま)."""
    if s is None:
        return None
    return ScopeCourseSnapshot(
        office_id=s.office_id,
        weekday=s.weekday,
        course_code=s.course_code,
        course_label=s.course_label,
        staff_name=s.staff_name,
        visits=[
            ScopeSnapshotVisit(
                patient_id=v.patient_id,
                patient_name=v.patient_name,
                start_time=_hhmm(v.start_time),
                end_time=_hhmm(v.end_time),
            )
            for v in s.visits
        ],
        events=[
            CourseSnapshotEvent(
                title=e.title,
                start_time=_hhmm(e.start_time),
                end_time=_hhmm(e.end_time),
            )
            for e in s.events
        ],
    )


@router.post(
    "/v2/scope-optimization/simulate",
    response_model=ScopeOptimizationSimulateResponse,
    status_code=status.HTTP_200_OK,
    summary="範囲最適化: 選択範囲 (コース/曜日/全体) の一括改善手順列を算出する (read-only)",
)
async def scope_optimization_simulate_endpoint(
    payload: ScopeOptimizationSimulateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ScopeOptimizationSimulateResponse:
    """選択範囲の中で move / swap を貪欲反復で積み上げ、手順列と前後メトリクスを返す.

    本 endpoint は **read-only**: DB を変更しない (適用は W2 の apply)。
    0 手でも 200 + ``excluded_summary`` (N-6「黙って消さない」) を返す。
    """
    office = await db.scalar(select(Office).where(Office.id == payload.scope.office_id))
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Office not found")

    # §10: フォーカス (scope) と探索範囲 (search_scope) の分離。包含を検証する.
    if payload.search_scope is not None:
        _validate_search_contains_focus(payload.scope, payload.search_scope)
    scope = _to_optimization_scope(payload.scope)
    search = (
        _to_optimization_scope(payload.search_scope) if payload.search_scope is not None else None
    )

    config = await load_scheduling_config(db)
    try:
        result = await simulate_scope_optimization(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            scope=scope,
            config=config,
            search=search,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ScopeOptimizationSimulateResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        office_id=payload.scope.office_id,
        steps=[
            ScopeOptimizationStep(
                seq=s.seq,
                patient_id=s.patient_id,
                patient_name=s.patient_name,
                suggestion=_improvement_to_schema(s.candidate),
                cumulative_delta_minutes=s.cumulative_delta_minutes,
                cumulative_delta_km=s.cumulative_delta_km,
                source_course=_scope_snapshot_to_schema(s.source_course),
                destination_course=_scope_snapshot_to_schema(s.destination_course),
            )
            for s in result.steps
        ],
        before=_scope_metrics_to_schema(result.before),
        after=_scope_metrics_to_schema(result.after),
        excluded_summary=ScopeOptimizationExcludedSummary(
            pinned=result.excluded.pinned,
            locked=result.excluded.locked,
            no_current_visit=result.excluded.no_current_visit,
            dismissed=result.excluded.dismissed,
            confirmation_required_excluded=result.excluded.confirmation_required_excluded,
            two_staff=result.excluded.two_staff,
            truncated=result.excluded.truncated,
        ),
        state_token=result.state_token,
        # §10: フォーカスのみの前後合計 (探索範囲=フォーカスのときは before/after と同値).
        focus_before=(
            _scope_metrics_to_schema(result.focus_before)
            if result.focus_before is not None
            else None
        ),
        focus_after=(
            _scope_metrics_to_schema(result.focus_after) if result.focus_after is not None else None
        ),
        # H2: コース別の実行後見通し.
        courses=[
            ScopeOptimizationCourseBeforeAfter(
                office_id=cba.office_id,
                weekday=cba.weekday,
                course_code=cba.course_code,
                course_label=cba.course_label,
                staff_name=cba.staff_name,
                before=_scope_metrics_to_schema(cba.before),
                after=_scope_metrics_to_schema(cba.after),
            )
            for cba in result.courses
        ],
    )


async def _resolve_course_template_id(db: DbDep, office_id: UUID, course_code: str) -> UUID | None:
    """office + course code (label) から course_templates.id を解決する (無ければ None).

    None は `_apply_pfv_move` の「省略 = 既存コース保持」に落ちるため、テンプレート
    未整備の環境でも apply 自体は成立する (コースだけ据え置きになる)。
    """
    return await db.scalar(
        select(CourseTemplate.id).where(
            CourseTemplate.office_id == office_id,
            CourseTemplate.label == course_code,
            CourseTemplate.deleted_at.is_(None),
        )
    )


async def _load_normal_pfvs(db: DbDep, patient_id: UUID) -> list[PatientFixedVisit]:
    """患者の normal PFV 全行を取り直す.

    先行 step の変更は `_validate_and_move_one` 末尾の flush で DB に反映済みのため、
    後続 step の SELECT (identity map 経由の属性変更も含め) は逐次状態を参照する。
    """
    return list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == "normal",
                )
            )
        ).all()
    )


async def _abort_apply(db: DbDep, status_code: int, detail: object) -> NoReturn:
    """途中 step で中断するとき、先行 step の ORM 変更を捨ててから HTTP エラーを返す."""
    await db.rollback()
    raise HTTPException(status_code=status_code, detail=detail)


async def _apply_visit_move_week_only(
    db: DbDep,
    *,
    patient_id: UUID,
    old_weekday: int,
    old_start: time_cls,
    new_weekday: int,
    new_start: time_cls,
    new_course: UUID | None,
    iso_year: int,
    iso_week: int,
    counters: dict[str, Any],
) -> None:
    """Wave U-1 (§2.2 B = week_only): 1 手ぶんの移動を今週の visits にのみ反映する.

    PFV は一切変更しない (呼び出し側で ``_apply_pfv_move`` を呼ばない). 当該患者の
    今週 (old_weekday) の該当 visit を探し、new_weekday/new_start へ更新して
    ``source='manual_week'`` を刻む (週生成・固定枠戻で保護される値・U-0 保証)。
    コース (course_id) は移動先曜日の Course を解決/生成して更新する
    (``new_course`` = course_template_id。解決不能なら据え置き = _apply_pfv_move と同義)。

    今週の該当 visit が無い step は no-op (この週の表に元々出ていないため反映不要)。
    ``counters`` に更新した visit 数と患者集合を積む (week_sync サマリ用)。
    """
    # 循環 import を避けるため関数内 import (schedule.py は schedule_v2 を import しない).
    from app.api.v1.schedule import _get_or_create_course_for_template_week

    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    old_date = week_monday + timedelta(days=old_weekday)
    new_date = week_monday + timedelta(days=new_weekday)

    visits = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient_id,
                    Visit.visit_date == old_date,
                    Visit.start_time == old_start,
                    Visit.deleted_at.is_(None),
                    # レビュー指摘 (U-1 LOW): 完了済み visit は動かさない (simulate は
                    # planned のみ提案するが防御的にフィルタ).
                    Visit.status == "planned",
                )
            )
        ).all()
    )
    if not visits:
        return

    # 移動先曜日の Course を解決 (course_template_id が取れたときのみ course_id を更新).
    new_course_id: UUID | None = None
    if new_course is not None:
        course = await _get_or_create_course_for_template_week(
            db,
            course_template_id=new_course,
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=new_weekday,
        )
        new_course_id = course.id

    for v in visits:
        dur_min = (v.end_time.hour * 60 + v.end_time.minute) - (
            v.start_time.hour * 60 + v.start_time.minute
        )
        if dur_min <= 0:
            dur_min = 30
        end_minutes = new_start.hour * 60 + new_start.minute + dur_min
        v.visit_date = new_date
        v.start_time = new_start
        v.end_time = time_cls(min(end_minutes // 60, 23), end_minutes % 60)
        if new_course_id is not None:
            v.course_id = new_course_id
        v.source = VISIT_SOURCE_MANUAL_WEEK
        counters["visits"] += 1
        counters["patients"].add(patient_id)


async def _validate_and_move_one(
    db: DbDep,
    *,
    patient_id: UUID,
    seq: int,
    old_weekday: int,
    old_start: time_cls,
    new_weekday: int,
    new_start: time_cls,
    new_course: UUID | None,
    config: SchedulingConfig,
    warnings: list[str],
    week_only: bool = False,
    iso_year: int | None = None,
    iso_week: int | None = None,
    week_counters: dict[str, Any] | None = None,
) -> None:
    """1 患者の slot0 移動を「検証 → 適用」する (scope apply の 1 手ぶん).

    step ごとに PFV を取り直して逐次検証する (simulate の手順列は逐次状態を前提に
    生成されている)。session は autoflush=False のため、適用後に明示 flush して
    後続 step の SELECT へ DB レベルでも反映する (本関数末尾)。

    Wave U-1 (§2.2 B): ``week_only=True`` のときは pinned/安全網の再検証 (A と同一) は
    そのまま実行しつつ、適用先を PFV ではなく今週の visits に切替える
    (``_apply_visit_move_week_only``)。PFV は不変。
    """
    rows = await _load_normal_pfvs(db, patient_id)
    moving = next(
        (r for r in rows if r.weekday == old_weekday and r.slot_index == 0),
        None,
    )
    if moving is None or moving.start_time != old_start:
        # state_token 一致下では起きないはずの不整合 (防御). 再計算を促す.
        await _abort_apply(
            db,
            status.HTTP_409_CONFLICT,
            f"手順{seq}の対象枠が見つかりません。スケジュールが変更された可能性が"
            "あります。再計算してください",
        )
    if moving.is_pinned:
        # エンジンは pinned の手を生成しない (手作りペイロード対策の防御).
        await _abort_apply(
            db,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"手順{seq}: ピン留めされた枠は動かせません",
        )

    proposed = _proposed_with_move(
        rows,
        moving_id=moving.id,
        new_weekday=new_weekday,
        new_start=new_start,
        new_course=new_course,
    )
    v = await validate_pfv_changes(db, patient_id, proposed, "normal", config=config)
    if v.has_errors:
        await _abort_apply(
            db,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "message": f"手順{seq}の適用が固定枠の保護に違反します",
                "violations": [
                    {
                        "code": w.code,
                        "message": w.message,
                        "weekday": w.weekday,
                        "severity": w.severity,
                    }
                    for w in v.errors
                ],
            },
        )
    warnings.extend(f"手順{seq}: {w.message}" for w in v.warnings_only)

    if week_only:
        # B: PFV は動かさず、今週の visits にのみ反映する.
        assert iso_year is not None and iso_week is not None and week_counters is not None
        await _apply_visit_move_week_only(
            db,
            patient_id=patient_id,
            old_weekday=old_weekday,
            old_start=old_start,
            new_weekday=new_weekday,
            new_start=new_start,
            new_course=new_course,
            iso_year=iso_year,
            iso_week=iso_week,
            counters=week_counters,
        )
    else:
        await _apply_pfv_move(
            db,
            moving_row=moving,
            all_rows=rows,
            new_weekday=new_weekday,
            new_start=new_start,
            new_course=new_course,
        )
    # autoflush=False のため明示 flush: 後続 step の SELECT / validate が
    # この手の結果 (行の削除含む) を DB レベルで参照できるようにする.
    await db.flush()


@router.post(
    "/v2/scope-optimization/apply",
    response_model=ScopeOptimizationApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="範囲最適化: simulate の手順列を先頭から N 手まとめて適用する (1 TX)",
)
async def scope_optimization_apply_endpoint(
    payload: ScopeOptimizationApplyRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ScopeOptimizationApplyResponse:
    """simulate 結果の先頭から N 手を PFV へ適用する (all-or-nothing / 1 TX).

    - **プレフィックスのみ**: steps は seq=1..N の連続区間。欠番・順序乱れは 422
      (手順は前の手が空けた枠に依存するため)。
    - **楽観ロック**: state_token をサーバで再計算し、不一致なら 409
      (simulate 以降に scope 患者の固定枠が変わった)。
    - 各 step は適用直前に pfv_validator (N-4) で再検証する。pinned 違反 (V2) は
      422 で全体 rollback、V3-V5 は warnings で返しブロックしない (P0-2 と同じ扱い)。
    """
    # プレフィックス検証 (seq=1..N 連続).
    seqs = [s.seq for s in payload.steps]
    if seqs != list(range(1, len(seqs) + 1)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="steps は simulate 結果の先頭からの連続区間 (seq=1..N) を送ってください",
        )

    office = await db.scalar(select(Office).where(Office.id == payload.scope.office_id))
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Office not found")

    # §10: state_token は探索範囲の患者集合から計算する (simulate と同一規約).
    if payload.search_scope is not None:
        _validate_search_contains_focus(payload.scope, payload.search_scope)
    scope = _to_optimization_scope(payload.scope)
    token_scope = (
        _to_optimization_scope(payload.search_scope) if payload.search_scope is not None else scope
    )

    # 楽観ロック: simulate 時と同一規約で state_token を再計算.
    current_token = await compute_current_state_token(
        db, iso_year=payload.iso_year, iso_week=payload.iso_week, scope=token_scope
    )
    if current_token != payload.state_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="スケジュールが変更されました。再計算してください",
        )

    config = await load_scheduling_config(db)
    warnings: list[str] = []

    # Wave U-1 (§2.2 反映先の統一): week_only は PFV を触らず今週 visits にのみ反映する.
    week_only = payload.change_scope == "week_only"
    week_counters: dict[str, Any] = {"visits": 0, "patients": set()}
    # pattern_and_week 用: 影響を受けた患者集合 (move 対象 + swap 相手) を集める.
    affected_patient_ids: set[UUID] = set()

    for step in payload.steps:
        sug = step.suggestion
        affected_patient_ids.add(step.patient_id)
        # _parse_hhmm は不正形式で ValueError を送出する (apply-swap と同じ 422 変換).
        try:
            old_start = _parse_hhmm(sug.current.start_time)
            new_start = _parse_hhmm(sug.candidate.start_time)
        except ValueError:
            old_start = None
            new_start = None
        if old_start is None or new_start is None:
            await _abort_apply(
                db,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"手順{step.seq}: start_time は HH:MM 必須です",
            )

        if sug.kind in ("time_change", "day_change"):
            new_course = await _resolve_course_template_id(
                db, sug.candidate.office_id, sug.candidate.course_code
            )
            await _validate_and_move_one(
                db,
                patient_id=step.patient_id,
                seq=step.seq,
                old_weekday=sug.current.weekday,
                old_start=old_start,
                new_weekday=sug.candidate.weekday,
                new_start=new_start,
                new_course=new_course,
                config=config,
                warnings=warnings,
                week_only=week_only,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                week_counters=week_counters,
            )
        elif sug.kind == "swap":
            cp = sug.swap_counterpart
            if cp is None:
                await _abort_apply(
                    db,
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"手順{step.seq}: swap には swap_counterpart が必要です",
                )
            try:
                cp_new_start = _parse_hhmm(cp.new_start_time)
                cp_old_start = _parse_hhmm(cp.current_start_time)
            except ValueError:
                cp_new_start = None
                cp_old_start = None
            if cp_new_start is None or cp_old_start is None:
                await _abort_apply(
                    db,
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"手順{step.seq}: swap_counterpart の start_time は HH:MM 必須です",
                )
            affected_patient_ids.add(cp.patient_id)
            # Y は X の旧バケット (コース) へ移る: X の旧行のコースを先に控える.
            x_rows = await _load_normal_pfvs(db, step.patient_id)
            x_row = next(
                (r for r in x_rows if r.weekday == sug.current.weekday and r.slot_index == 0),
                None,
            )
            x_old_course = x_row.course_template_id if x_row is not None else None
            x_new_course = await _resolve_course_template_id(
                db, sug.candidate.office_id, sug.candidate.course_code
            )
            # X → Y の旧位置 (candidate).
            await _validate_and_move_one(
                db,
                patient_id=step.patient_id,
                seq=step.seq,
                old_weekday=sug.current.weekday,
                old_start=old_start,
                new_weekday=sug.candidate.weekday,
                new_start=new_start,
                new_course=x_new_course,
                config=config,
                warnings=warnings,
                week_only=week_only,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                week_counters=week_counters,
            )
            # Y → X の旧位置 (コースは X の旧コースを引き継ぐ. None なら据え置き).
            await _validate_and_move_one(
                db,
                patient_id=cp.patient_id,
                seq=step.seq,
                old_weekday=cp.current_weekday,
                old_start=cp_old_start,
                new_weekday=cp.new_weekday,
                new_start=cp_new_start,
                new_course=x_old_course,
                config=config,
                warnings=warnings,
                week_only=week_only,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                week_counters=week_counters,
            )
        else:
            await _abort_apply(
                db,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"手順{step.seq}: 未知の kind '{sug.kind}' です",
            )

    # Wave U-1 (§2.2 A = pattern_and_week): PFV 移動が全 step 反映 (flush 済) された後、
    # 影響を受けた患者それぞれの今週 visits を PFV から再生成する (同一 TX で commit).
    week_sync: ScopeOptimizationWeekSync | None = None
    if payload.change_scope == "pattern_and_week":
        total_regen = 0
        total_del = 0
        for pid in affected_patient_ids:
            office_ids = await resolve_reset_office_ids(db, pid)
            reset_result = await reset_visits_to_fixed(
                db,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                office_ids=office_ids,
                mode="legacy",
                dry_run=False,
                config=config,
                patient_id=pid,
            )
            total_regen += int(reset_result.get("visits_regenerated", 0))
            total_del += int(reset_result.get("visits_soft_deleted", 0))
        week_sync = ScopeOptimizationWeekSync(
            patients=len(affected_patient_ids),
            visits_regenerated=total_regen,
            visits_soft_deleted=total_del,
        )
    elif payload.change_scope == "week_only":
        week_sync = ScopeOptimizationWeekSync(
            patients=len(week_counters["patients"]),
            visits_regenerated=int(week_counters["visits"]),
            visits_soft_deleted=0,
        )

    # 監査ログ (適用サマリ).
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="scope_optimization_apply",
            target_table="patient_fixed_visits",
            target_id=f"{payload.iso_year}-W{payload.iso_week}",
            before={},
            after={
                "office_id": str(payload.scope.office_id),
                "weekdays": payload.scope.weekdays,
                "course_codes": payload.scope.course_codes,
                "applied_count": len(payload.steps),
                "cumulative_delta_minutes": payload.steps[-1].cumulative_delta_minutes,
                "change_scope": payload.change_scope,
            },
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="適用が既存の固定枠と競合しました。再計算してください",
        ) from exc

    return ScopeOptimizationApplyResponse(
        applied_count=len(payload.steps),
        warnings=warnings,
        change_scope=payload.change_scope,
        week_sync=week_sync,
    )


# ---------------------------------------------------------------------------
# propose-unblock (W-12d 詰まり解消相談: 「この方をずらせば入ります」)
# ---------------------------------------------------------------------------


def _unblock_candidate_input(
    payload: ProposeUnblockRequest,
    lat: float,
    lng: float,
) -> CandidateInput:
    """propose-unblock リクエスト → 対象患者の CandidateInput (propose-slots と同形)."""
    weekdays = frozenset(
        WEEKDAY_CODE_TO_INT[code]
        for code in payload.preferred_weekdays
        if code in WEEKDAY_CODE_TO_INT
    )
    return CandidateInput(
        lat=lat,
        lng=lng,
        service_minutes=payload.service_minutes,
        time_type=payload.time_type,
        preferred_start=_parse_hhmm(payload.preferred_start),
        preferred_end=_parse_hhmm(payload.preferred_end),
        preferred_weekdays=weekdays,
        requires_multiple_staff=payload.requires_multiple_staff,
        existing_patient_id=payload.existing_patient_id,
        sex_restriction=payload.sex_restriction,
    )


async def _resolve_unblock_office_id(
    db: DbDep,
    *,
    explicit_office_id: UUID | None,
    patient_id: UUID | None,
    missing_detail: str,
) -> UUID:
    """W-13a: 詰まり解消の対象拠点を確定する.

    明示指定があれば尊重 (患者の拠点と食い違っても指定を優先 = 既存挙動不変)。省略時は
    対象患者 (``patient_id``) の ``primary_office_id`` から解決する。患者が見つからない /
    主担当拠点が未設定なら 422 (W-6 採用ガードと同文言系)。
    """
    if explicit_office_id is not None:
        return explicit_office_id
    patient: Patient | None = None
    if patient_id is not None:
        patient = await db.scalar(
            select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
        )
    if patient is None or patient.primary_office_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=missing_detail)
    return patient.primary_office_id


def _unblock_course_to_schema(c: Any) -> UnblockCourseSnapshot:
    """内部 ``UnblockCourseSnapshotData`` を API schema へ変換 (W-13b)."""

    def _visit(v: Any) -> UnblockCourseVisit:
        return UnblockCourseVisit(
            patient_id=v.patient_id,
            patient_name=v.patient_name,
            start_time=_hhmm(v.start_time),
            end_time=_hhmm(v.end_time),
        )

    return UnblockCourseSnapshot(
        office_name=c.office_name,
        weekday=c.weekday,
        course_code=c.course_code,
        course_label=c.course_label,
        before=[_visit(v) for v in c.before],
        after=[_visit(v) for v in c.after],
        events=[
            UnblockCourseEvent(
                title=e.title,
                start_time=_hhmm(e.start_time),
                end_time=_hhmm(e.end_time),
            )
            for e in getattr(c, "events", [])
        ],
    )


def _unblock_plan_to_schema(p: Any) -> UnblockPlanItem:
    """内部 ``UnblockPlan`` を API schema ``UnblockPlanItem`` へ変換."""
    return UnblockPlanItem(
        plan_id=p.plan_id,
        moves=[
            UnblockMoveItem(
                patient_id=m.patient_id,
                patient_name=m.patient_name,
                from_=UnblockSlotRef(
                    weekday=m.from_weekday,
                    course_code=m.from_course_code,
                    start_time=_hhmm(m.from_start),
                ),
                to=UnblockSlotRef(
                    weekday=m.to_weekday,
                    course_code=m.to_course_code,
                    start_time=_hhmm(m.to_start),
                ),
                delta_minutes=m.delta_minutes,
                within_preference=m.within_preference,
            )
            for m in p.moves
        ],
        insert=UnblockInsertItem(
            weekday=p.insert.weekday,
            course_code=p.insert.course_code,
            start_time=_hhmm(p.insert.start),
            end_time=_hhmm(p.insert.end),
            partner_course_code=p.insert.partner_course_code,
        ),
        total_delta_minutes=p.total_delta_minutes,
        moved_count=p.moved_count,
        frees_capacity=p.frees_capacity,
        courses=[_unblock_course_to_schema(c) for c in p.courses],
    )


@router.post(
    "/v2/propose-unblock",
    response_model=ProposeUnblockResponse,
    status_code=status.HTTP_200_OK,
    summary="W-12d: 候補0件のとき『既存を1〜2手ずらせば入る』開通手順を提案 (read-only)",
)
async def propose_unblock_endpoint(
    payload: ProposeUnblockRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ProposeUnblockResponse:
    """対象患者 (候補 0 件) を入れるための開通手順を、乱れの小さい順に提案する.

    本 endpoint は **read-only**: DB を変更しない (適用は /propose-unblock/apply)。
    0 件でも 200 + ``unmovable_summary`` (N-6「黙って諦めない」) + ``state_token`` を返す。
    """
    # W-13a: office_id 省略時は対象患者 (existing_patient_id) の主担当拠点から解決する.
    office_id = await _resolve_unblock_office_id(
        db,
        explicit_office_id=payload.office_id,
        patient_id=payload.existing_patient_id,
        missing_detail=(
            "主担当拠点が未設定のため探索できません。患者マスタで主担当拠点を設定してください。"
        ),
    )
    office = await db.scalar(select(Office).where(Office.id == office_id))
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Office not found")

    # 座標確定 (address → geocode / lat-lng フォールバック). 座標無しでは判定不能.
    cand_lat, cand_lng, _resolved = await _resolve_candidate_coords(db, payload)  # type: ignore[arg-type]
    if cand_lat is None or cand_lng is None:
        token = await compute_current_state_token(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            scope=OptimizationScope(office_id=office_id),
        )
        return ProposeUnblockResponse(
            plans=[],
            unmovable_summary=UnblockUnmovableSummary(),
            state_token=token,
            resolved_office_id=office_id,
        )

    candidate = _unblock_candidate_input(payload, cand_lat, cand_lng)
    config = await load_scheduling_config(db)
    try:
        result = await search_unblock_plans(
            db,
            candidate=candidate,
            office_id=office_id,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            config=config,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    s = result.unmovable_summary
    return ProposeUnblockResponse(
        plans=[_unblock_plan_to_schema(p) for p in result.plans],
        unmovable_summary=UnblockUnmovableSummary(
            pinned=s.pinned,
            locked=s.locked,
            two_staff=s.two_staff,
            pair=s.pair,
            dismissed=s.dismissed,
            confirmation_required=s.confirmation_required,
        ),
        state_token=result.state_token,
        resolved_office_id=office_id,
    )


async def _insert_target_pfv(
    db: DbDep,
    *,
    target: Patient,
    insert: UnblockInsertItem,
    service_minutes: int,
    requires_multiple_staff: bool,
    office_id: UUID,
    config: SchedulingConfig,
    warnings: list[str],
) -> bool:
    """対象患者の PFV を insert 枠に配置する (2 名体制は slot0+slot1 原子).

    W-2 の union 教訓: **既存曜日 (他の PFV 行) を消さない**。insert 曜日の slot0
    (2 名体制なら slot1 も) だけを UPSERT し、他曜日・他 slot は保全する。
    pinned 保護は validate_pfv_changes (V2) で再検証し、違反は 422 全ロールバック。
    """
    try:
        start = _parse_hhmm(insert.start_time)
    except ValueError:
        start = None
    if start is None:
        await _abort_apply(
            db, status.HTTP_422_UNPROCESSABLE_ENTITY, "insert.start_time は HH:MM 必須です"
        )
    wd = insert.weekday
    slot0_course = await _resolve_course_template_id(db, office_id, insert.course_code)
    slot1_course: UUID | None = None
    if requires_multiple_staff and insert.partner_course_code is not None:
        slot1_course = await _resolve_course_template_id(db, office_id, insert.partner_course_code)

    existing_rows = list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == target.id,
                    PatientFixedVisit.mode == "normal",
                )
            )
        ).all()
    )
    existing_by_ws: dict[tuple[int, int], PatientFixedVisit] = {
        (r.weekday, r.slot_index): r for r in existing_rows
    }

    # --- 再検証入力 (最終形): 既存行を base に、insert 対象 slot を差し替え/追加する ---
    desired: dict[tuple[int, int], PatientFixedVisitV2Base] = {
        (r.weekday, r.slot_index): _pfv_to_base(r) for r in existing_rows
    }
    ex0 = existing_by_ws.get((wd, 0))
    desired[(wd, 0)] = PatientFixedVisitV2Base(
        weekday=wd,
        start_time=start,
        duration_min=service_minutes,
        course_template_id=slot0_course if slot0_course is not None else None,
        slot_index=0,
        is_pinned=ex0.is_pinned if ex0 is not None else False,
        movability=ex0.movability if ex0 is not None else "unknown",
    )
    if requires_multiple_staff and slot1_course is not None:
        ex1 = existing_by_ws.get((wd, 1))
        desired[(wd, 1)] = PatientFixedVisitV2Base(
            weekday=wd,
            start_time=start,
            duration_min=service_minutes,
            course_template_id=slot1_course,
            slot_index=1,
            is_pinned=ex1.is_pinned if ex1 is not None else False,
            movability=ex1.movability if ex1 is not None else "unknown",
        )

    v = await validate_pfv_changes(db, target.id, list(desired.values()), "normal", config=config)
    if v.has_errors:
        await _abort_apply(
            db,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "message": "対象患者の配置が固定枠の保護に違反します",
                "violations": [
                    {
                        "code": w.code,
                        "message": w.message,
                        "weekday": w.weekday,
                        "severity": w.severity,
                    }
                    for w in v.errors
                ],
            },
        )
    warnings.extend(f"配置: {w.message}" for w in v.warnings_only)

    # --- 適用 (insert 対象 slot のみ UPSERT。他曜日・他 slot は保全) ---
    def _upsert(slot_index: int, course: UUID | None) -> None:
        ex = existing_by_ws.get((wd, slot_index))
        if ex is not None:
            ex.start_time = start
            ex.duration_min = service_minutes
            if course is not None:
                ex.course_template_id = course
        else:
            db.add(
                PatientFixedVisit(
                    patient_id=target.id,
                    mode="normal",
                    weekday=wd,
                    slot_index=slot_index,
                    start_time=start,
                    duration_min=service_minutes,
                    course_template_id=course,
                )
            )

    _upsert(0, slot0_course)
    if requires_multiple_staff and slot1_course is not None:
        _upsert(1, slot1_course)
    await db.flush()
    return True


@router.post(
    "/v2/propose-unblock/apply",
    response_model=ProposeUnblockApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="W-12d: 開通手順 (退避 + 配置) を 1 TX で適用する (all-or-nothing)",
)
async def propose_unblock_apply_endpoint(
    payload: ProposeUnblockApplyRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ProposeUnblockApplyResponse:
    """プランの退避 (moves) を逐次適用 → 対象患者を配置 → 影響患者の今週 visits を再生成.

    - **楽観ロック**: state_token をサーバで再計算し、不一致なら 409 (詰まり状況が変わった)。
    - **plan_id 検証**: plan_id 先頭 UUID == target_patient_id、かつサーバ側で moves+insert+
      target_patient_id から再導出した plan_id と照合する (改竄・患者すり替え防止)。
    - **1 TX**: moves を scope apply の ``_validate_and_move_one`` (pfv_validator・明示 flush)
      で逐次適用 → 対象 PFV 配置 (2 名体制は slot0+slot1 原子) → 影響患者の
      reset_visits_to_fixed (pattern_and_week) → commit。V2 pinned 違反は 422 全ロールバック。
    - **manual_week 保護**: ``reset_visits_to_fixed`` は U-3 M-2 恒久対策により、同
      (patient_id, visit_date) に生存 manual_week visit がある日の再生成をスキップする。
      手動上書き visit は本エンドポイントで消えない。
    - 適用は型レベル (pattern_and_week) 固定 (設計 P-6)。監査は AuditLog のみ (op_log 非汚染)。
    """
    # W-13a: office_id 省略時は target_patient_id の主担当拠点から解決する.
    office_id = await _resolve_unblock_office_id(
        db,
        explicit_office_id=payload.office_id,
        patient_id=payload.target_patient_id,
        missing_detail=(
            "主担当拠点が未設定のため適用できません。患者マスタで主担当拠点を設定してください。"
        ),
    )
    office = await db.scalar(select(Office).where(Office.id == office_id))
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Office not found")

    plan = payload.plan
    # ① plan_id 先頭 UUID が target_patient_id と一致するか確認 (対象患者すり替え検出).
    prefix_raw = plan.plan_id.split(":", 1)[0]
    try:
        plan_prefix_id = UUID(prefix_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="plan_id から対象患者を特定できません",
        ) from None
    if plan_prefix_id != payload.target_patient_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="plan_id の対象患者が target_patient_id と一致しません",
        )
    # ② plan 内容 (moves + insert + target_patient_id) から plan_id を再導出して指紋照合.
    move_tuples = [
        (
            m.patient_id,
            m.from_.weekday,
            m.from_.start_time,
            m.to.weekday,
            m.to.course_code,
            m.to.start_time,
        )
        for m in plan.moves
    ]
    expected_plan_id = compute_plan_id(
        move_tuples,
        plan.insert.weekday,
        plan.insert.course_code,
        plan.insert.start_time,
        plan.insert.partner_course_code,
        payload.target_patient_id,
    )
    if plan.plan_id != expected_plan_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="plan_id の指紋が plan 内容と一致しません",
        )
    target = await db.scalar(
        select(Patient).where(Patient.id == payload.target_patient_id, Patient.deleted_at.is_(None))
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # 配置時間 (service_minutes) は insert 枠の end−start、2 名体制の別は partner_course_code
    # の有無から復元する (candidate 情報を request に持たないため plan から自足させる)。
    try:
        ins_start = _parse_hhmm(plan.insert.start_time)
        ins_end = _parse_hhmm(plan.insert.end_time)
    except ValueError:
        ins_start = None
        ins_end = None
    if ins_start is None or ins_end is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insert.start_time / end_time は HH:MM 必須です",
        )
    insert_duration = (ins_end.hour * 60 + ins_end.minute) - (
        ins_start.hour * 60 + ins_start.minute
    )
    if insert_duration <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insert.end_time は start_time より後である必要があります",
        )
    insert_requires_multiple_staff = plan.insert.partner_course_code is not None

    # 楽観ロック: simulate 時と同一規約 (office スコープ PFV 指紋) で再計算.
    current_token = await compute_current_state_token(
        db,
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        scope=OptimizationScope(office_id=office_id),
    )
    if current_token != payload.state_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="詰まり状況が変更されました。再探索してください",
        )

    config = await load_scheduling_config(db)
    warnings: list[str] = []
    affected_patient_ids: set[UUID] = set()

    # --- 退避 (moves) を逐次適用 (scope apply と同一機構) ---
    for idx, m in enumerate(plan.moves, start=1):
        try:
            old_start = _parse_hhmm(m.from_.start_time)
            new_start = _parse_hhmm(m.to.start_time)
        except ValueError:
            old_start = None
            new_start = None
        if old_start is None or new_start is None:
            await _abort_apply(
                db, status.HTTP_422_UNPROCESSABLE_ENTITY, f"手順{idx}: start_time は HH:MM 必須です"
            )
        affected_patient_ids.add(m.patient_id)
        new_course = await _resolve_course_template_id(db, office_id, m.to.course_code)
        await _validate_and_move_one(
            db,
            patient_id=m.patient_id,
            seq=idx,
            old_weekday=m.from_.weekday,
            old_start=old_start,
            new_weekday=m.to.weekday,
            new_start=new_start,
            new_course=new_course,
            config=config,
            warnings=warnings,
        )

    # --- 対象患者を配置 (moves の flush 済み状態に対して) ---
    inserted = await _insert_target_pfv(
        db,
        target=target,
        insert=plan.insert,
        service_minutes=insert_duration,
        requires_multiple_staff=insert_requires_multiple_staff,
        office_id=office_id,
        config=config,
        warnings=warnings,
    )
    affected_patient_ids.add(target.id)

    # --- 影響患者の今週 visits を PFV から再生成 (pattern_and_week・同一 TX) ---
    for pid in affected_patient_ids:
        office_ids = await resolve_reset_office_ids(db, pid)
        await reset_visits_to_fixed(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_ids=office_ids,
            mode="legacy",
            dry_run=False,
            config=config,
            patient_id=pid,
        )

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="propose_unblock_apply",
            target_table="patient_fixed_visits",
            target_id=f"{payload.iso_year}-W{payload.iso_week}",
            before={},
            after={
                "office_id": str(office_id),
                "target_patient_id": str(target.id),
                "plan_id": plan.plan_id,
                "applied_moves": len(plan.moves),
                "insert": {
                    "weekday": plan.insert.weekday,
                    "course_code": plan.insert.course_code,
                    "start_time": plan.insert.start_time,
                    "partner_course_code": plan.insert.partner_course_code,
                },
            },
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="適用が既存の固定枠と競合しました。再探索してください",
        ) from exc

    return ProposeUnblockApplyResponse(
        applied_moves=len(plan.moves),
        inserted=inserted,
        warnings=warnings,
    )


__all__ = ["router"]
