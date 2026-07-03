"""範囲最適化エンジン (scope-optimization W1 BE-1).

管理者が選んだ範囲 (拠点 × 曜日集合 × コース集合) の中で、move (時刻/曜日変更) と
swap (2 患者入れ替え) を **貪欲反復** で積み上げ、「手順 1: ○○様を火 10:00 へ
(−15分/週) → 手順 2: …」という説明可能な手順列と、最適化前後の健康診断メトリクス
(scope 内合計) を返す純ロジック + DB 集約層。

設計書: docs/plans/scope-optimization-design.md §3。

方針 (正典再利用・コピー禁止):
  - 1 手の候補列挙 / 効果 / 実行可能性は improvement_engine の部品
    (``compute_exact_marginal`` (W3: コース合計の差の厳密計算) /
    ``_slot_within_preference`` / ``_swap_candidates_for_pfv`` /
    ``_bucket_existing_excluding``) と proposal_solver
    (``find_available_slots_for_candidate``) をそのまま使う。
  - 前後メトリクスは schedule_health の ``_compute_course_metrics`` (純関数) を
    模擬バケットに適用する (= 健康診断と同一物差し)。
  - read-only: DB は読むだけ。手の適用はすべてメモリ上の模擬状態 (_SimState)。

承認済み設計判断 (設計書 §0):
  - D-1: pinned / movability='locked' の枠は動かさない (障害物としては存在し続ける)。
  - D-2: 既定は「患者確認不要の手」のみ。希望内 (within_preference) はすべて可、
    希望外は movability が確認不要で許す手 (time_flexible=同曜日 / day_flexible=曜日も)
    のみ。unknown の希望外候補は生成しない (confirmation_required_excluded で会計)。
  - D-3: 却下記憶 (suggestion_dismissals) を尊重し蒸し返さない。

excluded_summary の会計 (N-6「黙って消さない」):
  - pinned / locked / no_current_visit … 初期 movable 集合の導出時に
    「scope 内に配置がある (patient × weekday)」単位で 1 回。
  - dismissed / confirmation_required_excluded … **初回イテレーションのみ**
    kind × PFV 単位で最大 1 回 (improvement_engine の filtered_summary と同粒度)。
    2 巡目以降は模擬状態が変わり同じ枠を重複カウントしてしまうため数えない。
  - truncated … SCOPE_MAX_STEPS 到達で打ち切ったとき True (黙って切らない)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.suggestion_dismissal import SuggestionDismissal
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_DURATION_PREFERRED as _LUNCH_DURATION_PREFERRED,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_EARLIEST_START as _LUNCH_EARLIEST_START,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_LATEST_END as _LUNCH_LATEST_END,
)
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit,
    compute_lunch_window,
)
from app.services.scheduling.config import SchedulingConfig
from app.services.scheduling.improvement_engine import (
    IMPROVEMENT_THRESHOLD_MIN,
    CourseSnapshotData,
    CourseSnapshotVisitData,
    ImprovementCandidateData,
    _bucket_existing_excluding,
    _build_changes,
    _CurrentPlacement,
    _slot_within_preference,
    _staff_warnings_for_bucket,
    _swap_candidates_for_pfv,
    compute_exact_marginal,
    snapshot_course_bucket,
)
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    find_available_slots_for_candidate,
)
from app.services.scheduling.propose_slots_service import (
    _course_label,
    _CourseBucket,
    load_week_course_buckets,
)
from app.services.scheduling.schedule_health import (
    _compute_course_metrics,
    _HealthCourse,
    _HealthVisit,
)

# 1 手とみなす最小効果 (分/週)。IMPROVEMENT_THRESHOLD_MIN と同値の独立定数
# (範囲最適化だけ現場調整する余地を残す。設計書 §3.4)。
SCOPE_STEP_THRESHOLD_MIN: int = IMPROVEMENT_THRESHOLD_MIN

# 手順列の上限 (安全弁)。到達時は excluded_summary.truncated=True で明示する。
SCOPE_MAX_STEPS: int = 30


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _add_min(t: time, minutes: int) -> time:
    total = _time_to_min(t) + minutes
    return time(hour=(total // 60) % 24, minute=total % 60)


# ---------------------------------------------------------------------------
# 公開データ構造 (API schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationScope:
    """最適化の対象範囲。移動元も移動先もこの範囲内に限る (設計書 §2)."""

    office_id: UUID
    # None = 全曜日 / 全コース。
    weekdays: frozenset[int] | None = None
    course_codes: frozenset[str] | None = None

    def contains(self, office_id: UUID, weekday: int, course_code: str) -> bool:
        if office_id != self.office_id:
            return False
        if self.weekdays is not None and weekday not in self.weekdays:
            return False
        if self.course_codes is not None and course_code not in self.course_codes:
            return False
        return True


# UI 統一: スナップショットの実体は improvement_engine の正典を共有する
# (患者詳細の改善提案と範囲最適化で同一の表示データ構造)。旧名は互換エイリアス。
ScopeSnapshotVisitData = CourseSnapshotVisitData
ScopeCourseSnapshotData = CourseSnapshotData


@dataclass
class ScopeStepData:
    """手順 1 件 (move または swap)。視点主の患者情報と累積効果つき.

    ``candidate`` は improvement_engine の 1 手表現をそのまま持つ (kind / 現在枠 /
    候補枠 / delta / within_preference / staff_warnings / swap_counterpart_*)。
    ``source_course`` / ``destination_course`` はこの手を適用する前の対象コースの
    スナップショット (同一コース内の移動は destination_course=None)。
    """

    seq: int  # 1 始まり
    patient_id: UUID
    patient_name: str
    candidate: ImprovementCandidateData
    cumulative_delta_minutes: int
    cumulative_delta_km: float
    source_course: ScopeCourseSnapshotData | None = None
    destination_course: ScopeCourseSnapshotData | None = None


@dataclass
class ScopeMetricsData:
    """scope 内合計の健康診断メトリクス (schedule_health と同一物差し)."""

    visit_count: int = 0
    travel_minutes: int = 0
    travel_km: float = 0.0
    buffer_minutes: int = 0
    gap_minutes: int = 0


@dataclass
class ScopeExcludedSummaryData:
    """動かさなかった / 数えなかった内訳 (N-6)。粒度はモジュール docstring 参照."""

    pinned: int = 0
    locked: int = 0
    no_current_visit: int = 0
    dismissed: int = 0
    confirmation_required_excluded: int = 0
    truncated: bool = False


@dataclass
class ScopeOptimizationResult:
    """simulate の結果一式."""

    steps: list[ScopeStepData]
    before: ScopeMetricsData
    after: ScopeMetricsData
    excluded: ScopeExcludedSummaryData
    state_token: str


@dataclass
class _ScopeCandidate:
    """列挙された 1 手 + 視点主の患者 (適用時の特定に使う)."""

    patient_id: UUID
    patient_name: str
    data: ImprovementCandidateData


# ---------------------------------------------------------------------------
# 模擬状態 (_SimState)
# ---------------------------------------------------------------------------


@dataclass
class _SimPfv:
    """PFV の模擬コピー (手を適用すると weekday / start_time が動く).

    ``_swap_candidates_for_pfv`` が読む属性 (is_pinned / movability) を持たせ、
    PatientFixedVisit の代役として渡す (実行時 duck-typing / 型は cast)。
    """

    patient_id: UUID
    weekday: int
    start_time: time
    duration_min: int
    is_pinned: bool
    movability: str


@dataclass
class _SimState:
    """scope 内バケットのミュータブルなコピー + PFV 模擬コピー.

    バケット dict のキーは load_week_course_buckets と同じ
    ``(office_id, weekday, course_code)``。visits の要素 (V2Visit) は元
    オブジェクトを共有し、移動時は remove + ``dataclasses.replace`` の複製 insert
    で元を汚さない。
    """

    buckets: dict[tuple[UUID, int, str], _CourseBucket] = field(default_factory=dict)
    pfv_by_pw: dict[tuple[UUID, int], _SimPfv] = field(default_factory=dict)
    occupied_weekdays: dict[UUID, set[int]] = field(default_factory=dict)


def _copy_bucket(b: _CourseBucket) -> _CourseBucket:
    """visits リストだけ複製した浅いコピー (V2Visit 要素は共有)."""
    return _CourseBucket(
        office_id=b.office_id,
        weekday=b.weekday,
        course_code=b.course_code,
        office_code=b.office_code,
        staff_name=b.staff_name,
        assigned_staff_id=b.assigned_staff_id,
        staff_sex=b.staff_sex,
        staff_absent=b.staff_absent,
        visits=list(b.visits),
    )


# ---------------------------------------------------------------------------
# state_token (apply の楽観ロック用指紋)
# ---------------------------------------------------------------------------


def compute_state_token(pfvs: list[PatientFixedVisit]) -> str:
    """scope 患者の PFV 行から順序不変の指紋を作る (設計書 §4.1).

    simulate 時点と apply 時点で同じ集合から再計算し、不一致なら 409 にする。
    対象は「scope バケットに現れる患者」の normal/slot0 PFV **全行** (scope 外
    曜日も含む — 曜日跨ぎ移動の占有判定に影響するため)。
    """
    lines = sorted(
        f"{p.patient_id}|{p.weekday}|{_hhmm(p.start_time)}|{p.duration_min}"
        f"|{p.course_template_id}|{int(p.is_pinned)}|{p.movability}"
        for p in pfvs
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 前後メトリクス (schedule_health の純関数を模擬バケットへ適用)
# ---------------------------------------------------------------------------


def _metrics_of(sim: _SimState, *, config: SchedulingConfig) -> ScopeMetricsData:
    """模擬バケット群の scope 内合計メトリクスを算出する.

    schedule_health の ``_compute_course_metrics`` に食わせるため、V2Visit →
    _HealthVisit の薄い変換のみ行う (計算式は健康診断と完全同一)。travel_km は
    「生値を合計してから 1 回だけ丸める」(二重丸め防止・health と同方針)。
    """
    out = ScopeMetricsData()
    raw_km = 0.0
    for (office_id, weekday, code), bucket in sim.buckets.items():
        if not bucket.visits:
            continue
        hc = _HealthCourse(
            office_id=office_id,
            weekday=weekday,
            course_code=code,
            staff_name=bucket.staff_name,
            visits=[
                _HealthVisit(
                    patient_id=v.patient_id,
                    start_time=v.start_time,
                    end_time=v.end_time,
                    lat=v.lat,
                    lng=v.lng,
                )
                for v in bucket.visits
            ],
        )
        metrics, km = _compute_course_metrics(hc, config=config)
        out.visit_count += metrics.visit_count
        out.travel_minutes += metrics.travel_minutes
        out.buffer_minutes += metrics.buffer_minutes
        out.gap_minutes += metrics.gap_minutes
        raw_km += km
    out.travel_km = round(raw_km, 1)
    return out


# ---------------------------------------------------------------------------
# 候補列挙 (1 イテレーション分)
# ---------------------------------------------------------------------------


def _placement_of(
    sim: _SimState, pid: UUID, pfv: _SimPfv
) -> tuple[tuple[UUID, int, str], _CourseBucket, V2Visit] | None:
    """模擬 PFV の現在配置 (バケット + visit) を特定する.

    weekday 一致のバケットから patient_id 一致の visit を探し、start_time が
    模擬 PFV と一致する行を優先する (improvement_engine._find_current_placement
    と同規則)。
    """
    matched: tuple[tuple[UUID, int, str], _CourseBucket, V2Visit] | None = None
    for key, bucket in sim.buckets.items():
        if bucket.weekday != pfv.weekday:
            continue
        for v in bucket.visits:
            if v.patient_id != pid:
                continue
            if matched is None or v.start_time == pfv.start_time:
                matched = (key, bucket, v)
            if v.start_time == pfv.start_time:
                return matched
    return matched


def _enumerate_step_candidates(
    sim: _SimState,
    *,
    patient_by_id: dict[UUID, Patient],
    office_name_by_id: dict[UUID, str],
    dismissed_fp: set[tuple[UUID, str, int]],
    swap_dismissed: set[tuple[UUID, int]],
    config: SchedulingConfig | None,
    summary: ScopeExcludedSummaryData | None,
) -> list[_ScopeCandidate]:
    """模擬状態に対する move + swap の全候補 (D-2 確認不要のみ・閾値以上) を列挙する.

    ``summary`` が非 None (= 初回イテレーション) のときだけ dismissed /
    confirmation_required_excluded を kind × PFV 単位で会計する。
    """
    lunch_duration = config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    lunch_window_start = config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END

    candidates: list[_ScopeCandidate] = []

    # 反復順を決定的にするため (patient_id, weekday) でソートして走査する.
    for (pid, wd_src), pfv in sorted(
        sim.pfv_by_pw.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
    ):
        if pfv.is_pinned or pfv.movability == "locked":
            continue  # movable 集合の会計は simulate 本体で実施済み.
        patient = patient_by_id.get(pid)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        placed = _placement_of(sim, pid, pfv)
        if placed is None:
            continue  # scope 外配置 (or 配置なし)。会計は simulate 本体で実施済み.
        src_key, src_bucket, src_visit = placed
        patient_coord = (float(patient.lat), float(patient.lng))

        # 現在枠の限界コスト (W3: 厳密計算 = コース合計の差。同時刻ペアも正しく扱う).
        existing_src = _bucket_existing_excluding(src_bucket, pid)
        self_ev = ExistingVisit(
            start_time=src_visit.start_time,
            end_time=src_visit.end_time,
            lat=patient_coord[0],
            lng=patient_coord[1],
            service_minutes=src_visit.service_minutes,
            patient_id=str(pid),
        )
        cur_min, cur_km = compute_exact_marginal(existing_src, self_ev, config=config)
        current = _CurrentPlacement(
            bucket=src_bucket,
            office_code=src_bucket.office_code,
            start_time=src_visit.start_time,
            end_time=src_visit.end_time,
            marginal_min=cur_min,
            marginal_km=cur_km,
        )
        current_course_label = _course_label(src_bucket.office_code, src_bucket.course_code)

        movability = pfv.movability  # 'unknown' | 'time_flexible' | 'day_flexible'
        day_flexible = movability == "day_flexible"
        time_dismissed = (pid, "time_change", wd_src) in dismissed_fp
        day_dismissed = (pid, "day_change", wd_src) in dismissed_fp
        counted_kinds: set[str] = set()  # summary の kind × PFV 二重計上防止.

        # ---- move 候補 (時刻変更 / 曜日変更) ----
        cand = Candidate(
            lat=patient_coord[0],
            lng=patient_coord[1],
            service_minutes=pfv.duration_min,
            time_type=None,
            preferred_start=None,
            preferred_end=None,
            patient_id=str(pid),
        )
        for (office_id, wd_dst, code), bucket in sim.buckets.items():
            is_same_weekday = wd_dst == wd_src
            kind = "time_change" if is_same_weekday else "day_change"

            # 曜日跨ぎ: 移動先曜日を既に占有していたら apply 不能 (デッドエンド防止).
            if not is_same_weekday and wd_dst in sim.occupied_weekdays.get(pid, set()):
                continue

            existing = _bucket_existing_excluding(bucket, pid)
            lunch = compute_lunch_window(
                bucket.visits,
                warnings=None,
                weekday=wd_dst,
                duration=lunch_duration,
                window_start=lunch_window_start,
                window_end=lunch_window_end,
            )
            slots = find_available_slots_for_candidate(
                existing, cand, lunch_window=lunch, weekday=wd_dst, config=config
            )
            for slot in slots:
                # 現在位置そのものは候補にしない.
                if (
                    is_same_weekday
                    and (office_id, wd_dst, code) == src_key
                    and slot.start == src_visit.start_time
                ):
                    continue

                cand_ev = ExistingVisit(
                    start_time=slot.start,
                    end_time=slot.end,
                    lat=patient_coord[0],
                    lng=patient_coord[1],
                    service_minutes=pfv.duration_min,
                    patient_id=str(pid),
                )
                cand_min, cand_km = compute_exact_marginal(existing, cand_ev, config=config)
                delta_min = cur_min - cand_min
                delta_km = cur_km - cand_km
                if delta_min < SCOPE_STEP_THRESHOLD_MIN:
                    continue

                within_pref = _slot_within_preference(
                    patient, wd_dst, slot.start, slot.end, config=config
                )

                # D-2: 確認不要の手のみ. 希望内は無条件可 / 希望外は movability 次第.
                if not within_pref:
                    allowed = (
                        movability in ("time_flexible", "day_flexible")
                        if is_same_weekday
                        else day_flexible
                    )
                    if not allowed:
                        if summary is not None and kind not in counted_kinds:
                            counted_kinds.add(kind)
                            summary.confirmation_required_excluded += 1
                        continue

                # D-3: 却下記憶.
                if (kind == "time_change" and time_dismissed) or (
                    kind == "day_change" and day_dismissed
                ):
                    if summary is not None and kind not in counted_kinds:
                        counted_kinds.add(kind)
                        summary.dismissed += 1
                    continue

                cand_label = _course_label(bucket.office_code, code)
                changes, unchanged = _build_changes(
                    current_weekday=wd_src,
                    current_start=src_visit.start_time,
                    current_course_label=current_course_label,
                    current_staff_name=src_bucket.staff_name,
                    cand_weekday=wd_dst,
                    cand_start=slot.start,
                    cand_course_label=cand_label,
                    cand_staff_name=bucket.staff_name,
                )
                candidates.append(
                    _ScopeCandidate(
                        patient_id=pid,
                        patient_name=patient.name,
                        data=ImprovementCandidateData(
                            kind=kind,
                            target_weekday=wd_src,
                            current_office_id=src_bucket.office_id,
                            current_weekday=wd_src,
                            current_start=src_visit.start_time,
                            current_end=src_visit.end_time,
                            current_course_label=current_course_label,
                            current_staff_name=src_bucket.staff_name,
                            cand_office_id=office_id,
                            cand_office_name=office_name_by_id.get(office_id),
                            cand_weekday=wd_dst,
                            cand_start=slot.start,
                            cand_end=slot.end,
                            cand_course_code=code,
                            cand_course_label=cand_label,
                            cand_staff_name=bucket.staff_name,
                            delta_minutes=delta_min,
                            delta_km=round(delta_km, 2),
                            staff_warnings=_staff_warnings_for_bucket(
                                bucket, patient.sex_restriction
                            ),
                            requires_patient_confirmation=False,  # D-2: 確認不要のみ.
                            within_preference=within_pref,
                            changes=changes,
                            unchanged=unchanged,
                        ),
                    )
                )

        # ---- swap 候補 (正典 _swap_candidates_for_pfv 再利用) ----
        swaps = _swap_candidates_for_pfv(
            patient=patient,
            patient_coord=patient_coord,
            wx=wd_src,
            sx=src_visit.start_time,
            x_duration=pfv.duration_min,
            x_movability=movability,
            x_day_flexible=day_flexible,
            x_occupied_weekdays=sim.occupied_weekdays.get(pid, set()),
            current=current,
            current_course_label=current_course_label,
            buckets=sim.buckets,  # scope 内バケットのみ → 範囲制約はここで担保.
            office_name_by_id=office_name_by_id,
            pfv_by_pw=cast("dict[tuple[UUID, int], PatientFixedVisit]", sim.pfv_by_pw),
            patient_by_id=patient_by_id,
            swap_dismissed=swap_dismissed,
            config=config,
        )
        for s in swaps:
            # D-2: どちらか一方でも要確認なら生成しない.
            if s.requires_patient_confirmation or s.swap_counterpart_requires_confirmation:
                if summary is not None and "swap" not in counted_kinds:
                    counted_kinds.add("swap")
                    summary.confirmation_required_excluded += 1
                continue
            # ミラー重複 (X↔Y と Y↔X) の除去: id 昇順の視点のみ採用.
            # (両患者とも非 pinned/locked のときのみ swap は生成されるため、
            #  必ず両視点から列挙され、片側だけ落とす取りこぼしは起きない)
            if s.swap_counterpart_patient_id is not None and str(pid) > str(
                s.swap_counterpart_patient_id
            ):
                continue
            if s.delta_minutes < SCOPE_STEP_THRESHOLD_MIN:
                continue
            candidates.append(_ScopeCandidate(patient_id=pid, patient_name=patient.name, data=s))

    return candidates


# ---------------------------------------------------------------------------
# 手の適用 (模擬状態の更新)
# ---------------------------------------------------------------------------


def _capture_step_context(
    sim: _SimState, sc: _ScopeCandidate
) -> tuple[ScopeCourseSnapshotData | None, ScopeCourseSnapshotData | None]:
    """この手が触るコースの適用前スナップショットを (移動元, 移動先) で返す.

    同一コース内の手 (移動先 = 移動元) は destination=None (FE は 1 枚のタイムラインで
    「ここへ移動」を描く)。別コースへの手は 2 枚を並列表示できるよう両方返す。
    """
    c = sc.data
    src: ScopeCourseSnapshotData | None = None
    src_key: tuple[UUID, int, str] | None = None
    pfv = sim.pfv_by_pw.get((sc.patient_id, c.current_weekday))
    if pfv is not None:
        placed = _placement_of(sim, sc.patient_id, pfv)
        if placed is not None:
            src_key, src_bucket, _v = placed
            src = snapshot_course_bucket(src_bucket)
    dst_key = (c.cand_office_id, c.cand_weekday, c.cand_course_code)
    dst: ScopeCourseSnapshotData | None = None
    if dst_key != src_key:
        dst_bucket = sim.buckets.get(dst_key)
        if dst_bucket is not None:
            dst = snapshot_course_bucket(dst_bucket)
    return src, dst


def _remove_visit(bucket: _CourseBucket, pid: UUID, start: time) -> V2Visit | None:
    for i, v in enumerate(bucket.visits):
        if v.patient_id == pid and v.start_time == start:
            return bucket.visits.pop(i)
    return None


def _insert_visit(bucket: _CourseBucket, v: V2Visit) -> None:
    bucket.visits.append(v)
    bucket.visits.sort(key=lambda x: _time_to_min(x.start_time))


def _move_sim_pfv(
    sim: _SimState, pid: UUID, *, from_weekday: int, to_weekday: int, to_start: time
) -> None:
    """模擬 PFV と占有曜日集合を移動後の状態へ更新する."""
    pfv = sim.pfv_by_pw.pop((pid, from_weekday))
    pfv.weekday = to_weekday
    pfv.start_time = to_start
    sim.pfv_by_pw[(pid, to_weekday)] = pfv
    occ = sim.occupied_weekdays.setdefault(pid, set())
    occ.discard(from_weekday)
    occ.add(to_weekday)


def _apply_move(sim: _SimState, sc: _ScopeCandidate) -> bool:
    """move 1 手を模擬状態へ適用する。整合が取れないときは False (候補を捨てる)."""
    c = sc.data
    pfv = sim.pfv_by_pw.get((sc.patient_id, c.current_weekday))
    if pfv is None:
        return False
    placed = _placement_of(sim, sc.patient_id, pfv)
    if placed is None:
        return False
    _key, src_bucket, visit = placed
    dst_bucket = sim.buckets.get((c.cand_office_id, c.cand_weekday, c.cand_course_code))
    if dst_bucket is None:
        return False

    removed = _remove_visit(src_bucket, sc.patient_id, visit.start_time)
    if removed is None:
        return False
    _insert_visit(
        dst_bucket,
        replace(
            removed,
            weekday=c.cand_weekday,
            start_time=c.cand_start,
            end_time=c.cand_end,
            course_code=c.cand_course_code,
            office_id=c.cand_office_id,
        ),
    )
    _move_sim_pfv(
        sim,
        sc.patient_id,
        from_weekday=c.current_weekday,
        to_weekday=c.cand_weekday,
        to_start=c.cand_start,
    )
    return True


def _apply_swap(sim: _SimState, sc: _ScopeCandidate) -> bool:
    """swap 1 手を模擬状態へ適用する (X↔Y の 2 visit / 2 PFV を同時更新)."""
    c = sc.data
    x_pid = sc.patient_id
    y_pid = c.swap_counterpart_patient_id
    if (
        y_pid is None
        or c.swap_counterpart_current_start is None
        or c.swap_counterpart_current_weekday is None
        or c.swap_counterpart_new_start is None
        or c.swap_counterpart_new_weekday is None
    ):
        return False

    x_pfv = sim.pfv_by_pw.get((x_pid, c.current_weekday))
    y_pfv = sim.pfv_by_pw.get((y_pid, c.swap_counterpart_current_weekday))
    if x_pfv is None or y_pfv is None:
        return False
    x_placed = _placement_of(sim, x_pid, x_pfv)
    y_placed = _placement_of(sim, y_pid, y_pfv)
    if x_placed is None or y_placed is None:
        return False
    _xk, x_bucket, x_visit = x_placed
    _yk, y_bucket, y_visit = y_placed

    x_removed = _remove_visit(x_bucket, x_pid, x_visit.start_time)
    y_removed = _remove_visit(y_bucket, y_pid, y_visit.start_time)
    if x_removed is None or y_removed is None:
        # 片方だけ抜けた場合は戻す (理論上到達しない安全弁).
        if x_removed is not None:
            _insert_visit(x_bucket, x_removed)
        if y_removed is not None:
            _insert_visit(y_bucket, y_removed)
        return False

    # X は Y の旧位置 (cand_weekday/cand_start) へ、Y は X の旧位置へ.
    # office_id も移動先バケットに合わせる (_apply_move と対称。現状 scope は
    # 単一拠点のため同値だが、非対称を残さない — レビュー LOW-1).
    _insert_visit(
        y_bucket,
        replace(
            x_removed,
            weekday=c.cand_weekday,
            start_time=c.cand_start,
            end_time=_add_min(c.cand_start, x_removed.service_minutes),
            course_code=y_bucket.course_code,
            office_id=y_bucket.office_id,
        ),
    )
    _insert_visit(
        x_bucket,
        replace(
            y_removed,
            weekday=c.swap_counterpart_new_weekday,
            start_time=c.swap_counterpart_new_start,
            end_time=_add_min(c.swap_counterpart_new_start, y_removed.service_minutes),
            course_code=x_bucket.course_code,
            office_id=x_bucket.office_id,
        ),
    )

    _move_sim_pfv(
        sim,
        x_pid,
        from_weekday=c.current_weekday,
        to_weekday=c.cand_weekday,
        to_start=c.cand_start,
    )
    _move_sim_pfv(
        sim,
        y_pid,
        from_weekday=c.swap_counterpart_current_weekday,
        to_weekday=c.swap_counterpart_new_weekday,
        to_start=c.swap_counterpart_new_start,
    )
    return True


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


async def _load_scope_buckets_and_pfvs(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    scope: OptimizationScope,
) -> tuple[
    dict[tuple[UUID, int, str], _CourseBucket],
    dict[UUID, str],
    list[PatientFixedVisit],
]:
    """scope 内バケットと scope 患者の PFV (normal/slot0 全曜日) をロードする.

    simulate と apply の state_token 再計算で **同一のロード規約** を共有するための
    ヘルパ (集合がずれると楽観ロックが誤作動するため、必ずこれを使う)。
    """
    all_buckets, office_name_by_id, _office_codes = await load_week_course_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=[scope.office_id]
    )
    scope_buckets = {
        key: b for key, b in all_buckets.items() if scope.contains(key[0], key[1], key[2])
    }
    scope_pids: set[UUID] = {v.patient_id for b in scope_buckets.values() for v in b.visits}
    pfv_rows: list[PatientFixedVisit] = []
    if scope_pids:
        pfv_rows = list(
            (
                await db.scalars(
                    select(PatientFixedVisit).where(
                        PatientFixedVisit.patient_id.in_(scope_pids),
                        PatientFixedVisit.mode == "normal",
                        PatientFixedVisit.slot_index == 0,
                    )
                )
            ).all()
        )
    return scope_buckets, office_name_by_id, pfv_rows


async def compute_current_state_token(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    scope: OptimizationScope,
) -> str:
    """現時点の DB 状態から state_token を再計算する (apply の楽観ロック判定用).

    simulate 時と同一のロード規約 (`_load_scope_buckets_and_pfvs`) で PFV 集合を
    取り直すため、simulate 以降に scope 患者の PFV が 1 行でも変われば不一致になる。
    """
    _buckets, _names, pfv_rows = await _load_scope_buckets_and_pfvs(
        db, iso_year=iso_year, iso_week=iso_week, scope=scope
    )
    return compute_state_token(pfv_rows)


async def simulate_scope_optimization(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    scope: OptimizationScope,
    config: SchedulingConfig,
) -> ScopeOptimizationResult:
    """選択範囲の一括改善を貪欲反復で算出する (read-only).

    Returns:
        手順列 (最大 SCOPE_MAX_STEPS 件・各手 SCOPE_STEP_THRESHOLD_MIN 分/週以上) と
        前後メトリクス・除外内訳・state_token。
    """
    scope_buckets, office_name_by_id, pfv_rows = await _load_scope_buckets_and_pfvs(
        db, iso_year=iso_year, iso_week=iso_week, scope=scope
    )

    sim = _SimState(buckets={key: _copy_bucket(b) for key, b in scope_buckets.items()})

    excluded = ScopeExcludedSummaryData()

    # scope バケットに現れる患者集合.
    scope_pids: set[UUID] = {v.patient_id for b in sim.buckets.values() for v in b.visits}
    if not scope_pids:
        empty = _metrics_of(sim, config=config)
        return ScopeOptimizationResult(
            steps=[],
            before=empty,
            after=empty,
            excluded=excluded,
            state_token=compute_state_token([]),
        )

    state_token = compute_state_token(pfv_rows)

    for row in pfv_rows:
        sim.pfv_by_pw[(row.patient_id, row.weekday)] = _SimPfv(
            patient_id=row.patient_id,
            weekday=row.weekday,
            start_time=row.start_time,
            duration_min=row.duration_min,
            is_pinned=row.is_pinned,
            movability=row.movability,
        )
        sim.occupied_weekdays.setdefault(row.patient_id, set()).add(row.weekday)

    # movable 集合の会計 (scope 内に配置がある patient × weekday 単位で 1 回).
    scope_weekday_pairs: set[tuple[UUID, int]] = {
        (v.patient_id, b.weekday) for b in sim.buckets.values() for v in b.visits
    }
    for pid, wd in sorted(scope_weekday_pairs, key=lambda x: (str(x[0]), x[1])):
        pfv = sim.pfv_by_pw.get((pid, wd))
        if pfv is None:
            excluded.no_current_visit += 1  # visit はあるが PFV 対応が取れず評価不能.
        elif pfv.is_pinned:
            excluded.pinned += 1
        elif pfv.movability == "locked":
            excluded.locked += 1

    # Patient (weekly_pattern / 座標 / sex_restriction) を 1 クエリでロード.
    patient_rows = (await db.scalars(select(Patient).where(Patient.id.in_(scope_pids)))).all()
    patient_by_id: dict[UUID, Patient] = {p.id: p for p in patient_rows}

    # 却下記憶 (D-3)。move 指紋は scope 患者ぶん / swap 指紋は正典と同じ全患者ぶん.
    now = datetime.now()
    dismissal_rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id.in_(scope_pids))
        )
    ).all()
    dismissed_fp: set[tuple[UUID, str, int]] = {
        (d.patient_id, d.kind, d.target_weekday)
        for d in dismissal_rows
        if (d.expires_at is None or _as_naive(d.expires_at) > now)
        and d.kind in ("time_change", "day_change")
    }
    swap_rows = (
        await db.scalars(select(SuggestionDismissal).where(SuggestionDismissal.kind == "swap"))
    ).all()
    swap_dismissed: set[tuple[UUID, int]] = {
        (d.patient_id, d.target_weekday)
        for d in swap_rows
        if d.expires_at is None or _as_naive(d.expires_at) > now
    }

    before = _metrics_of(sim, config=config)

    # ---- 貪欲反復 (設計書 §3.4) ----
    steps: list[ScopeStepData] = []
    cum_min = 0
    cum_km = 0.0
    while len(steps) < SCOPE_MAX_STEPS:
        candidates = _enumerate_step_candidates(
            sim,
            patient_by_id=patient_by_id,
            office_name_by_id=office_name_by_id,
            dismissed_fp=dismissed_fp,
            swap_dismissed=swap_dismissed,
            config=config,
            summary=excluded if not steps else None,  # 会計は初回のみ.
        )
        if not candidates:
            break
        # 決定的 tie-break: 希望内優先 → delta 降順 → km 降順 → 早い時刻 → patient id.
        candidates.sort(
            key=lambda sc: (
                not sc.data.within_preference,
                -sc.data.delta_minutes,
                -sc.data.delta_km,
                _time_to_min(sc.data.cand_start),
                str(sc.patient_id),
            )
        )
        applied = False
        for best in candidates:
            # タイムライン表示用スナップショットは適用 **前** に取る (W3).
            src_snap, dst_snap = _capture_step_context(sim, best)
            ok = _apply_swap(sim, best) if best.data.kind == "swap" else _apply_move(sim, best)
            if ok:
                cum_min += best.data.delta_minutes
                cum_km += best.data.delta_km
                steps.append(
                    ScopeStepData(
                        seq=len(steps) + 1,
                        patient_id=best.patient_id,
                        patient_name=best.patient_name,
                        candidate=best.data,
                        cumulative_delta_minutes=cum_min,
                        cumulative_delta_km=round(cum_km, 2),
                        source_course=src_snap,
                        destination_course=dst_snap,
                    )
                )
                applied = True
                break
        if not applied:
            break  # 全候補が適用不能 (理論上到達しない安全弁).

    if len(steps) >= SCOPE_MAX_STEPS:
        excluded.truncated = True

    after = _metrics_of(sim, config=config)

    return ScopeOptimizationResult(
        steps=steps,
        before=before,
        after=after,
        excluded=excluded,
        state_token=state_token,
    )


def _as_naive(dt: datetime) -> datetime:
    """tz-aware / naive 混在に備え naive (UTC 相当) に揃えて比較する."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


__all__ = [
    "SCOPE_MAX_STEPS",
    "SCOPE_STEP_THRESHOLD_MIN",
    "OptimizationScope",
    "ScopeCourseSnapshotData",
    "ScopeExcludedSummaryData",
    "ScopeMetricsData",
    "ScopeOptimizationResult",
    "ScopeSnapshotVisitData",
    "ScopeStepData",
    "compute_current_state_token",
    "compute_state_token",
    "simulate_scope_optimization",
]
