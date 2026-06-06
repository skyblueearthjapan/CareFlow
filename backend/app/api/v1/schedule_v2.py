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
from datetime import date, timedelta
from datetime import time as time_cls
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
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
    AutoScheduleV2UnassignAllRequest,
    AutoScheduleV2UnassignAllResponse,
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
from app.schemas.v2.propose_slots import (
    WEEKDAY_CODE_TO_INT,
    WEEKDAY_INT_TO_CODE,
    ProposeCoverage,
    ProposeCoverageDay,
    ProposeMiniScheduleEntry,
    ProposeSlotItem,
    ProposeSlotsRequest,
    ProposeSlotsResponse,
    _parse_hhmm,
)
from app.schemas.v2.travel_estimate import (
    TravelEstimateRequest,
    TravelEstimateResponse,
)
from app.services.geocoding.client import (
    GeocodingServiceError,
    geocode_address,
)
from app.services.office_assigner import OfficeAssigner
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
    _address_bucket,
    _is_in_lunch_break,
    apply_individual_proposal,
    apply_week_only,
    calc_h_violations,
    calc_total_distance,
    count_active_managers_per_weekday,
    count_active_staff_per_weekday,
    haversine_km,
    reset_visits_to_fixed,
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
from app.services.scheduling.proposal_solver import (
    VISIT_BUFFER_MINUTES,
    haversine_minutes,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ProposedSlot,
    compute_all_proposed_slots,
    compute_coverage,
    load_week_course_buckets,
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
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"visit_plans[{idx}]: H10 違反 — 動的昼休憩 "
                    f"({_lws.strftime('%H:%M')}-{_lwe.strftime('%H:%M')} 内 45 分) を "
                    f"どこにも確保できない visit は不可 "
                    f"(start={vp.start_time}, end={vp.end_time})"
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
        mini_schedule=[
            ProposeMiniScheduleEntry(
                time=str(e["time"]),
                name=str(e["name"]),
                ins=e["ins"],  # type: ignore[arg-type]
                is_here=bool(e["is_here"]),
                is_pair=bool(e["is_pair"]),
            )
            for e in p.mini_schedule
        ],
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
    )

    # Phase G-88 Step3: full-optimize と同一の事業所別設定をロードして注入 (read-only).
    config = await load_scheduling_config(db)

    # ランキング済み全スロットを 1 回算出し、slots[] (上位 limit) と coverage で共有.
    all_proposed = compute_all_proposed_slots(
        buckets,
        office_name_by_id,
        candidate,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        config=config,
    )
    proposed = all_proposed[: payload.limit]

    # 5. API schema に詰める (slots[] は従来通り上位 limit 件).
    slots_out = [_proposed_to_item(p) for p in proposed]

    # 6. 週N日カバレッジ: 希望曜日ごとに実現可否 + 最良枠をグルーピング.
    #    required_days は frequency_per_week 優先, 無ければ希望曜日数.
    if payload.frequency_per_week is not None:
        required_days = payload.frequency_per_week
    else:
        required_days = len(preferred_weekday_ints)
    cov = compute_coverage(
        all_proposed,
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

    return ProposeSlotsResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        candidate_lat=cand_lat,
        candidate_lng=cand_lng,
        resolved_office_id=resolved_office_id,
        slots=slots_out,
        coverage=coverage,
        message=message,
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
    """``BoardCourseData`` → API schema ``BoardCourse`` (実時刻 + 容量集計)."""
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
        )
        for bv in course.visits
    ]
    filled = len(visits_out)
    total_minutes = sum(bv.service_minutes for bv in course.visits)
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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
    try:
        buckets, office_name_by_id, _office_code_by_id = await load_board_buckets(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            office_ids=office_ids,
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

    # weekdays[] (曜日ヘッダー: 日付 + 全拠点合計患者数).
    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    patients_per_weekday: dict[int, int] = {}
    for b in buckets.values():
        patients_per_weekday[b.weekday] = patients_per_weekday.get(b.weekday, 0) + len(b.visits)
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
            patient_count = sum(len(c.visits) for c in cell_courses)
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


__all__ = ["router"]
