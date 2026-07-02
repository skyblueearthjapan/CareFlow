"""P3-① 当日欠勤の代替スタッフ提案 — エンジン + 適用.

設計書: docs/plans/p3-1-staff-substitute-design.md

「今朝◯◯さんが欠勤 → 今日の担当 visit、誰が代われる？」に答える. Layer3 の
適格性資産を **日粒度** で再利用し、新規ロジックは「候補スタッフの当日既存 visit
との時間衝突判定」のみ (``pfv_validator._find_conflict`` を再利用).

提案 (``find_substitute_candidates`` / read-only) → 人間が選ぶ →
適用 (``apply_substitutions`` / all-or-nothing) の 3 段.

ハード制約 (設計書 §2) は **全て既存関数の import 再利用** (コピー禁止):
    | 当日出勤   | ``Layer3Assigner.load_active_staff`` の work_days               |
    | 性別       | ``layer3.sex_satisfies_restrictions``                          |
    | 拠点       | ``StaffInfo.effective_office_for_weekday``                      |
    | Event±15分 | ``layer3._has_event_overlap_with_buffer`` (visit 単位で判定)   |
    | trainee 単独禁止 | ``Staff.is_trainee`` ∧ required_staff_count == 1         |
    | 時間衝突   | ``pfv_validator._find_conflict`` (移動 + バッファ / 90 分占有)  |

layer3 資産の再利用方法: ``load_active_staff`` / ``_load_staff_events`` /
``_load_patient_recent_staff`` は ``Layer3Assigner`` のインスタンスメソッドだが
``self`` 状態に依存しないため、 空インスタンスを生成して薄く呼ぶだけで再利用する
(layer3 本体は無変更).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import time as time_cls
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.patient import Patient
from app.models.staff import Staff, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.v2.staff_substitute import (
    SubstituteApplied,
    SubstituteApplyRequest,
    SubstituteApplyResponse,
    SubstituteCandidate,
    SubstituteCandidatesResponse,
    SubstituteNoCandidateReason,
    SubstituteScoreBreakdown,
    SubstituteVisitCandidates,
)
from app.services.scheduling.auto_allocator_v2 import haversine_km, haversine_minutes
from app.services.scheduling.config import SchedulingConfig

# 設計書 §5 asset-reuse 方針による意図的な cross-module 依存.
# load_active_staff / _load_staff_events / _load_patient_recent_staff /
# sex_satisfies_restrictions / _has_event_overlap_with_buffer は self 非依存の
# 純粋関数として再利用する (layer3_assignment.py 本体は無変更).
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
    _has_event_overlap_with_buffer,
    sex_satisfies_restrictions,
)
from app.services.scheduling.pfv_validator import _find_conflict

# ExistingVisit のサロゲートキー (patient_id + start_time) は同一患者に
# 1 日複数 visit がある場合に衝突しうる. 現実データでは稀なため許容.
from app.services.scheduling.proposal_solver import ExistingVisit

# ---------------------------------------------------------------------------
# 除外理由コード (設計書 §1 N-6)
# ---------------------------------------------------------------------------
REASON_NO_WORK_DAY = "no_work_day"
REASON_SEX_MISMATCH = "sex_mismatch"
REASON_OFFICE_MISMATCH = "office_mismatch"
REASON_EVENT_OVERLAP = "event_overlap"
REASON_TIME_CONFLICT = "time_conflict"
REASON_TRAINEE_SOLO = "trainee_solo"

# ---------------------------------------------------------------------------
# ソフトスコア重み (設計書 §3 / 命名定数)
# ---------------------------------------------------------------------------
WEIGHT_CONTINUITY_1 = 100.0  # 患者の直近 1 番目の担当だった
WEIGHT_CONTINUITY_2 = 50.0  # 2 番目
WEIGHT_CONTINUITY_3 = 20.0  # 3 番目
WEIGHT_TRAVEL = -1.0  # 追加移動 1 分あたり
WEIGHT_LOAD = -5.0  # 当日 visit 1 件あたり

_CONTINUITY_BY_INDEX: dict[int, float] = {
    0: WEIGHT_CONTINUITY_1,
    1: WEIGHT_CONTINUITY_2,
    2: WEIGHT_CONTINUITY_3,
}


class StaffSubstituteError(Exception):
    """代替提案 / 適用の業務エラー (HTTP status を保持).

    - 404: 欠勤スタッフ / visit が存在しない.
    - 409: 対象 visit が既に status 遷移済 (planned でない).
    - 422: 適用直前の N-4 再検証でハード制約違反 (all-or-nothing で書込なし).
    """

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


# ---------------------------------------------------------------------------
# 内部データ構造
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _VisitContext:
    """1 visit の適格判定に必要な情報 (patient / course / 時間)."""

    visit_id: UUID
    patient_id: UUID
    patient_name: str
    patient_lat: float | None
    patient_lng: float | None
    sex_restriction: str | None
    office_id: UUID | None
    required_staff_count: int
    start_time: time_cls
    end_time: time_cls
    visit_group_id: UUID | None
    is_secondary: bool
    # 当該グループの非欠勤メンバー (2 名体制で候補から除外する staff).
    partner_staff_id: UUID | None


@dataclass
class _EvalContext:
    """候補評価に共有する日粒度コンテキスト (layer3 資産の再利用結果)."""

    weekday: int
    week_monday: date_cls
    staff_pool: dict[UUID, StaffInfo]
    events_by_staff: dict[UUID, list]
    recent_by_patient: dict[UUID, list[UUID]]
    # staff_id -> 当日の既存 planned visit (ExistingVisit 列).
    staff_visits: dict[UUID, list[ExistingVisit]]


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _hhmm(t: time_cls) -> str:
    return t.strftime("%H:%M")


def _duration_min(start: time_cls, end: time_cls) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def _restrictions_of(sex_restriction: str | None) -> frozenset[str]:
    return frozenset({sex_restriction}) if sex_restriction else frozenset()


async def _load_eval_context(db: AsyncSession, target_date: date_cls) -> _EvalContext:
    """layer3 資産 (load_active_staff / _load_staff_events / _load_patient_recent_staff)
    と当日 visit を日粒度でロードして評価コンテキストを組む.

    ``Layer3Assigner`` のインスタンスメソッドは ``self`` 状態を持たないため、
    空インスタンス経由で薄く再利用する (layer3 本体は無変更).
    """
    iso_year, iso_week, _ = target_date.isocalendar()
    week_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
    weekday = target_date.weekday()

    # NOTE: これらのメソッドは self 状態に依存しないため空インスタンス経由で再利用する.
    # Layer3Assigner.__init__ に必須引数が追加された場合はモジュール関数として抽出すること.
    assigner = Layer3Assigner()
    staff_infos = await assigner.load_active_staff(
        db, iso_year=iso_year, iso_week=iso_week, week_monday=week_monday
    )
    staff_pool = {s.staff_id: s for s in staff_infos}
    staff_ids = list(staff_pool.keys())
    events_by_staff = await assigner._load_staff_events(
        db, staff_ids=staff_ids, week_monday=week_monday
    )
    recent_by_patient = await assigner._load_patient_recent_staff(db, week_monday=week_monday)

    # 当日の planned visit を staff 別 ExistingVisit 列に組む (時間衝突 / 移動 / 負荷用).
    staff_visits: dict[UUID, list[ExistingVisit]] = {}
    rows = (
        await db.execute(
            select(
                Visit.start_time,
                Visit.end_time,
                Patient.lat,
                Patient.lng,
                Patient.id,
                VisitStaffAssignment.staff_id,
            )
            .join(VisitStaffAssignment, VisitStaffAssignment.visit_id == Visit.id)
            .join(Patient, Patient.id == Visit.patient_id)
            .where(
                Visit.visit_date == target_date,
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    for start_t, end_t, lat, lng, patient_id, staff_id in rows:
        f_lat = _as_float(lat)
        f_lng = _as_float(lng)
        if f_lat is None or f_lng is None:
            continue  # 座標欠損は移動 / 衝突判定に使えないため除外
        staff_visits.setdefault(staff_id, []).append(
            ExistingVisit(
                start_time=start_t,
                end_time=end_t,
                lat=f_lat,
                lng=f_lng,
                service_minutes=_duration_min(start_t, end_t),
                patient_id=str(patient_id),
            )
        )

    return _EvalContext(
        weekday=weekday,
        week_monday=week_monday,
        staff_pool=staff_pool,
        events_by_staff=events_by_staff,
        recent_by_patient=recent_by_patient,
        staff_visits=staff_visits,
    )


def _hard_constraint_reason(
    staff: StaffInfo,
    vc: _VisitContext,
    ctx: _EvalContext,
    *,
    extra_others: list[ExistingVisit] | None = None,
    config: SchedulingConfig,
) -> str | None:
    """ハード制約を優先順で判定し、最初に破れた理由コードを返す (満たせば None).

    順序は各制約を独立にテストできるよう固定:
    no_work_day → sex_mismatch → office_mismatch → event_overlap →
    trainee_solo → time_conflict.
    """
    # 1. 当日出勤 (shift + 当週 off を反映済みの work_days)
    if ctx.weekday not in staff.work_days:
        return REASON_NO_WORK_DAY

    # 2. 性別 (患者の sex_restriction を満たすか)
    if not sex_satisfies_restrictions(staff.sex, _restrictions_of(vc.sex_restriction)):
        return REASON_SEX_MISMATCH

    # 3. 拠点 (実効拠点がコース拠点と一致. office_id 不明の visit は skip)
    if vc.office_id is not None:
        if staff.effective_office_for_weekday(ctx.weekday) != vc.office_id:
            return REASON_OFFICE_MISMATCH

    # 4. StaffEvent ± バッファ (visit 単位で判定 = 単一 VisitTimeSlot のコースを合成)
    synthetic_course = CourseAssignmentTarget(
        course_id=vc.visit_id,
        weekday=ctx.weekday,
        course_code="_substitute",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        visits=[VisitTimeSlot(start_time=vc.start_time, end_time=vc.end_time)],
        office_id=vc.office_id,
    )
    if _has_event_overlap_with_buffer(
        staff_id=staff.staff_id,
        course=synthetic_course,
        weekday=ctx.weekday,
        events_by_staff=ctx.events_by_staff,
        week_monday=ctx.week_monday,
    ):
        return REASON_EVENT_OVERLAP

    # 5. trainee 単独禁止 (1 名体制に新人は単独で入れない)
    if staff.is_trainee and vc.required_staff_count == 1:
        return REASON_TRAINEE_SOLO

    # 6. 時間衝突 (候補の当日既存 visit + intra-batch との移動 + バッファ / 90 分占有)
    if vc.patient_lat is not None and vc.patient_lng is not None:
        proposed = ExistingVisit(
            start_time=vc.start_time,
            end_time=vc.end_time,
            lat=vc.patient_lat,
            lng=vc.patient_lng,
            service_minutes=_duration_min(vc.start_time, vc.end_time),
            patient_id=str(vc.patient_id),
        )
        others = list(ctx.staff_visits.get(staff.staff_id, []))
        if extra_others:
            others.extend(extra_others)
        if _find_conflict(proposed, others, config=config) is not None:
            return REASON_TIME_CONFLICT

    return None


def _score_candidate(
    staff: StaffInfo,
    vc: _VisitContext,
    ctx: _EvalContext,
    *,
    config: SchedulingConfig,
) -> SubstituteScoreBreakdown:
    """ソフトスコア内訳を算出 (設計書 §3)."""
    # 継続性: 患者の直近担当リスト内の index → ボーナス.
    continuity = 0.0
    recent = ctx.recent_by_patient.get(vc.patient_id, [])
    for idx, sid in enumerate(recent):
        if sid == staff.staff_id:
            continuity = _CONTINUITY_BY_INDEX.get(idx, 0.0)
            break

    existing = ctx.staff_visits.get(staff.staff_id, [])

    # 追加移動: 当日既存 visit のうち最も近い地点への移動時間 (分). 既存なしは 0.
    travel_penalty = 0.0
    if existing and vc.patient_lat is not None and vc.patient_lng is not None:
        min_travel = min(
            haversine_minutes(
                haversine_km(vc.patient_lat, vc.patient_lng, ev.lat, ev.lng),
                speed_kmh=config.travel_speed_kmh,
            )
            for ev in existing
        )
        travel_penalty = WEIGHT_TRAVEL * float(min_travel)

    # 負荷: 当日既存 visit 件数.
    load_penalty = WEIGHT_LOAD * float(len(existing))

    return SubstituteScoreBreakdown(
        continuity_bonus=continuity,
        travel_penalty=travel_penalty,
        load_penalty=load_penalty,
    )


def _enumerate_candidates(
    vc: _VisitContext,
    ctx: _EvalContext,
    *,
    absent_staff_id: UUID,
    config: SchedulingConfig,
) -> tuple[list[SubstituteCandidate], list[SubstituteNoCandidateReason]]:
    """1 visit の候補ランキング + 除外理由集計を返す."""
    candidates: list[SubstituteCandidate] = []
    reasons: Counter[str] = Counter()

    for staff_id, staff in ctx.staff_pool.items():
        if staff_id == absent_staff_id:
            continue
        if vc.partner_staff_id is not None and staff_id == vc.partner_staff_id:
            continue  # 2 名体制の相方は候補から除外 (二重割当防止)
        reason = _hard_constraint_reason(staff, vc, ctx, config=config)
        if reason is not None:
            reasons[reason] += 1
            continue
        breakdown = _score_candidate(staff, vc, ctx, config=config)
        score = (
            breakdown.continuity_bonus + breakdown.travel_penalty + breakdown.load_penalty
        )
        candidates.append(
            SubstituteCandidate(
                staff_id=staff.staff_id,
                staff_name=staff.name,
                staff_sex=staff.sex,
                score=score,
                score_breakdown=breakdown,
            )
        )

    # score 降順 → staff_id 昇順で安定ソート.
    candidates.sort(key=lambda c: (-c.score, str(c.staff_id)))

    no_reasons = [
        SubstituteNoCandidateReason(code=code, count=count)
        for code, count in sorted(reasons.items())
    ]
    return candidates, no_reasons


async def _load_absent_visit_contexts(
    db: AsyncSession, *, absent_staff_id: UUID, target_date: date_cls
) -> list[_VisitContext]:
    """欠勤スタッフの当日 planned visit を _VisitContext 列に組む (グループは代表 1 行)."""
    # 欠勤スタッフが VSA で割り当てられている当日 planned visit.
    absent_visit_ids = (
        await db.scalars(
            select(VisitStaffAssignment.visit_id).where(
                VisitStaffAssignment.staff_id == absent_staff_id
            )
        )
    ).all()
    if not absent_visit_ids:
        return []

    visits = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.id.in_(list(absent_visit_ids)),
                    Visit.visit_date == target_date,
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if not visits:
        return []

    # patient (lat/lng/sex_restriction/name) と course (office_id) を bulk fetch.
    patient_ids = {v.patient_id for v in visits}
    course_ids = {v.course_id for v in visits if v.course_id is not None}
    patients = {
        p.id: p
        for p in (
            await db.scalars(select(Patient).where(Patient.id.in_(list(patient_ids))))
        ).all()
    }
    offices_by_course: dict[UUID, UUID] = {}
    if course_ids:
        for cid, oid in (
            await db.execute(
                select(Course.id, Course.office_id).where(Course.id.in_(list(course_ids)))
            )
        ).all():
            offices_by_course[cid] = oid

    # visit_group ごとに代表 1 行へ集約 (同一 group の 2 行 = 同一患者 2 名体制).
    by_group: dict[object, list[Visit]] = {}
    for v in visits:
        key = v.visit_group_id if v.visit_group_id is not None else v.id
        by_group.setdefault(key, []).append(v)

    contexts: list[_VisitContext] = []
    for _key, group_visits in by_group.items():
        rep = min(group_visits, key=lambda v: str(v.id))
        patient = patients.get(rep.patient_id)
        office_id = offices_by_course.get(rep.course_id) if rep.course_id is not None else None
        is_secondary = rep.secondary_staff_id == absent_staff_id
        # 相方 = 当該グループの非欠勤メンバー.
        partner_staff_id: UUID | None = None
        if rep.required_staff_count == 2 or rep.visit_group_id is not None:
            if is_secondary:
                partner_staff_id = rep.primary_staff_id
            else:
                partner_staff_id = rep.secondary_staff_id
        contexts.append(
            _VisitContext(
                visit_id=rep.id,
                patient_id=rep.patient_id,
                patient_name=patient.name if patient is not None else "",
                patient_lat=_as_float(patient.lat) if patient is not None else None,
                patient_lng=_as_float(patient.lng) if patient is not None else None,
                sex_restriction=patient.sex_restriction if patient is not None else None,
                office_id=office_id,
                required_staff_count=rep.required_staff_count,
                start_time=rep.start_time,
                end_time=rep.end_time,
                visit_group_id=rep.visit_group_id,
                is_secondary=is_secondary,
                partner_staff_id=partner_staff_id,
            )
        )

    # 開始時刻順に並べる (UI 都合).
    contexts.sort(key=lambda c: (c.start_time, str(c.visit_id)))
    return contexts


# ---------------------------------------------------------------------------
# 公開 API: 候補列挙 (GET, read-only)
# ---------------------------------------------------------------------------


async def find_substitute_candidates(
    db: AsyncSession,
    absent_staff_id: UUID,
    target_date: date_cls,
    *,
    config: SchedulingConfig,
) -> SubstituteCandidatesResponse:
    """欠勤スタッフの当日 planned visit ごとに代替候補をランキングして返す (read-only)."""
    absent = await db.scalar(select(Staff).where(Staff.id == absent_staff_id))
    if absent is None:
        raise StaffSubstituteError("欠勤スタッフが見つかりません", http_status=404)

    ctx = await _load_eval_context(db, target_date)
    visit_contexts = await _load_absent_visit_contexts(
        db, absent_staff_id=absent_staff_id, target_date=target_date
    )

    visits_out: list[SubstituteVisitCandidates] = []
    for vc in visit_contexts:
        candidates, no_reasons = _enumerate_candidates(
            vc, ctx, absent_staff_id=absent_staff_id, config=config
        )
        visits_out.append(
            SubstituteVisitCandidates(
                visit_id=vc.visit_id,
                patient_id=vc.patient_id,
                patient_name=vc.patient_name,
                start_time=_hhmm(vc.start_time),
                end_time=_hhmm(vc.end_time),
                sex_restriction=vc.sex_restriction,
                visit_group_id=vc.visit_group_id,
                required_staff_count=vc.required_staff_count,
                is_secondary=vc.is_secondary,
                candidates=candidates,
                no_candidate_reasons=no_reasons,
            )
        )

    return SubstituteCandidatesResponse(
        absent_staff_id=absent_staff_id,
        absent_staff_name=absent.name,
        target_date=target_date,
        visits=visits_out,
    )


# ---------------------------------------------------------------------------
# 公開 API: 適用 (POST, all-or-nothing)
# ---------------------------------------------------------------------------


def _swap_staff_on_visit(visit: Visit, absent_staff_id: UUID, substitute_staff_id: UUID) -> None:
    """1 visit 行の primary/secondary を absent→substitute に差替え + 保護フラグ設定.

    VSA の差替えは呼出側 (``apply_substitutions``) が別途行う. 本関数はレガシー互換の
    primary/secondary 同期と ``manual_staff_override`` 設定のみを担う.
    """
    if visit.primary_staff_id == absent_staff_id:
        visit.primary_staff_id = substitute_staff_id
    if visit.secondary_staff_id == absent_staff_id:
        visit.secondary_staff_id = substitute_staff_id
    visit.manual_staff_override = True


async def apply_substitutions(
    db: AsyncSession,
    request: SubstituteApplyRequest,
    *,
    config: SchedulingConfig,
) -> SubstituteApplyResponse:
    """差替えを all-or-nothing で適用する.

    N-4 再検証 (GET と同一ハード制約を適用直前に再実行) → all-or-nothing →
    VSA 差替え + Visit primary/secondary 同期 + visit_group partner 同期 +
    manual_staff_override=True + (任意) register_absence.

    Raises:
        StaffSubstituteError: 404 (visit なし) / 409 (status 遷移済) / 422 (制約違反).
    """
    absent_staff_id = request.absent_staff_id
    target_date = request.target_date

    requested = {item.visit_id: item.substitute_staff_id for item in request.substitutions}

    # ---- 対象 visit をロード (id 指定) ----
    visits = {
        v.id: v
        for v in (
            await db.scalars(select(Visit).where(Visit.id.in_(list(requested.keys()))))
        ).all()
    }
    for vid in requested:
        v = visits.get(vid)
        if v is None or v.deleted_at is not None:
            raise StaffSubstituteError(f"visit が見つかりません: {vid}", http_status=404)
        # status 遷移済 (planned でない) は 409.
        if v.status != VISIT_STATUS_PLANNED:
            raise StaffSubstituteError(
                f"visit は既に {v.status} のため差替えできません: {vid}",
                http_status=409,
            )

    # ---- 同一 visit_group_id の visit が複数含まれる場合は 422 (GET dedup を API 層でも強制) ----
    seen_groups: set[UUID] = set()
    for v in visits.values():
        if v.visit_group_id is not None:
            if v.visit_group_id in seen_groups:
                raise StaffSubstituteError(
                    "同一 visit_group の visit が複数含まれています (代表 1 件のみを送信してください)",
                    http_status=422,
                )
            seen_groups.add(v.visit_group_id)

    # ---- グループ全行 (partner) をロード ----
    group_ids = {v.visit_group_id for v in visits.values() if v.visit_group_id is not None}
    group_rows: dict[UUID, list[Visit]] = {}
    _skipped_nonplanned_partner_count = 0
    if group_ids:
        for gv in (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_group_id.in_(list(group_ids)),
                    Visit.deleted_at.is_(None),
                )
            )
        ).all():
            if gv.visit_group_id is None:
                continue
            if gv.status != VISIT_STATUS_PLANNED:
                # Partner が planned でない → 適用スキップ, 後で警告.
                _skipped_nonplanned_partner_count += 1
                continue
            group_rows.setdefault(gv.visit_group_id, []).append(gv)

    # ---- 評価コンテキスト + patient/course 情報 ----
    ctx = await _load_eval_context(db, target_date)
    patient_ids = {v.patient_id for v in visits.values()}
    patients = {
        p.id: p
        for p in (await db.scalars(select(Patient).where(Patient.id.in_(list(patient_ids))))).all()
    }
    course_ids = {v.course_id for v in visits.values() if v.course_id is not None}
    offices_by_course: dict[UUID, UUID] = {}
    if course_ids:
        for cid, oid in (
            await db.execute(
                select(Course.id, Course.office_id).where(Course.id.in_(list(course_ids)))
            )
        ).all():
            offices_by_course[cid] = oid

    # intra-batch: 同一 substitute に複数 visit を割り当てる場合の相互衝突も見る.
    batch_by_staff: dict[UUID, list[ExistingVisit]] = {}
    for vid, sub_id in requested.items():
        v = visits[vid]
        p = patients.get(v.patient_id)
        f_lat = _as_float(p.lat) if p is not None else None
        f_lng = _as_float(p.lng) if p is not None else None
        if f_lat is None or f_lng is None:
            continue
        batch_by_staff.setdefault(sub_id, []).append(
            ExistingVisit(
                start_time=v.start_time,
                end_time=v.end_time,
                lat=f_lat,
                lng=f_lng,
                service_minutes=_duration_min(v.start_time, v.end_time),
                patient_id=str(v.patient_id),
            )
        )

    # ---- N-4 再検証 (書込前に全件. 1 件でも違反なら 422 all-or-nothing) ----
    for vid, sub_id in requested.items():
        v = visits[vid]
        # 欠勤スタッフが実際に当該 visit に割当たっていること.
        if absent_staff_id not in (v.primary_staff_id, v.secondary_staff_id):
            raise StaffSubstituteError(
                f"欠勤スタッフは当該 visit に割り当てられていません: {vid}",
                http_status=422,
            )
        sub_staff = ctx.staff_pool.get(sub_id)
        if sub_staff is None:
            raise StaffSubstituteError(
                f"代替スタッフが当日稼働スタッフに含まれていません: {sub_id}",
                http_status=422,
            )
        p = patients.get(v.patient_id)
        is_secondary = v.secondary_staff_id == absent_staff_id
        partner_staff_id = v.primary_staff_id if is_secondary else v.secondary_staff_id
        vc = _VisitContext(
            visit_id=v.id,
            patient_id=v.patient_id,
            patient_name=p.name if p is not None else "",
            patient_lat=_as_float(p.lat) if p is not None else None,
            patient_lng=_as_float(p.lng) if p is not None else None,
            sex_restriction=p.sex_restriction if p is not None else None,
            office_id=offices_by_course.get(v.course_id) if v.course_id is not None else None,
            required_staff_count=v.required_staff_count,
            start_time=v.start_time,
            end_time=v.end_time,
            visit_group_id=v.visit_group_id,
            is_secondary=is_secondary,
            partner_staff_id=partner_staff_id,
        )
        # intra-batch: 同一 substitute の他 visit を time-conflict の others に加える
        # (自分自身は除外).
        extra = [
            ev
            for ev in batch_by_staff.get(sub_id, [])
            if ev.patient_id != str(v.patient_id) or ev.start_time != v.start_time
        ]
        reason = _hard_constraint_reason(sub_staff, vc, ctx, extra_others=extra, config=config)
        if reason is not None:
            raise StaffSubstituteError(
                f"代替スタッフがハード制約を満たしません ({reason}): visit={vid}",
                http_status=422,
            )

    # ---- 全件検証 pass → 書込 (VSA 差替え + primary/secondary 同期 + partner 同期) ----
    applied: list[SubstituteApplied] = []
    for vid, sub_id in requested.items():
        v = visits[vid]
        # グループ全行 (代表 + partner) を対象に absent→substitute を適用.
        group_targets: list[Visit] = [v]
        if v.visit_group_id is not None:
            group_targets = group_rows.get(v.visit_group_id, [v])
        partner_visit_id: UUID | None = None
        for gv in group_targets:
            if gv.id != v.id:
                partner_visit_id = gv.id
            # VSA: absent 行を delete → substitute 行を add (冪等).
            existing_vsa = (
                await db.scalars(
                    select(VisitStaffAssignment).where(
                        VisitStaffAssignment.visit_id == gv.id
                    )
                )
            ).all()
            has_absent = any(a.staff_id == absent_staff_id for a in existing_vsa)
            has_sub = any(a.staff_id == sub_id for a in existing_vsa)
            if has_absent:
                await db.execute(
                    delete(VisitStaffAssignment).where(
                        VisitStaffAssignment.visit_id == gv.id,
                        VisitStaffAssignment.staff_id == absent_staff_id,
                    )
                )
            if not has_sub:
                db.add(VisitStaffAssignment(visit_id=gv.id, staff_id=sub_id))
            _swap_staff_on_visit(gv, absent_staff_id, sub_id)

        applied.append(
            SubstituteApplied(
                visit_id=v.id,
                original_staff_id=absent_staff_id,
                substitute_staff_id=sub_id,
                visit_group_partner_visit_id=partner_visit_id,
            )
        )

    warnings: list[str] = []

    # ---- register_absence: 欠勤スタッフの当日 off を StaffWeeklyOverride で登録 ----
    if request.register_absence:
        iso_year, iso_week, _ = target_date.isocalendar()
        weekday = target_date.weekday()
        existing_ov = await db.scalar(
            select(StaffWeeklyOverride).where(
                StaffWeeklyOverride.staff_id == absent_staff_id,
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
                StaffWeeklyOverride.weekday == weekday,
            )
        )
        if existing_ov is None:
            db.add(
                StaffWeeklyOverride(
                    staff_id=absent_staff_id,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    weekday=weekday,
                    override_type="off",
                    reason="当日欠勤 (代替提案適用)",
                )
            )
        else:
            warnings.append("既存の週次上書きがあるため欠勤登録はスキップしました")

    if _skipped_nonplanned_partner_count:
        warnings.append(
            f"グループ内の非 planned partner {_skipped_nonplanned_partner_count} 件はスキップしました"
        )

    await db.flush()

    return SubstituteApplyResponse(applied=applied, warnings=warnings)


__all__ = [
    "REASON_EVENT_OVERLAP",
    "REASON_NO_WORK_DAY",
    "REASON_OFFICE_MISMATCH",
    "REASON_SEX_MISMATCH",
    "REASON_TIME_CONFLICT",
    "REASON_TRAINEE_SOLO",
    "StaffSubstituteError",
    "apply_substitutions",
    "find_substitute_candidates",
]
