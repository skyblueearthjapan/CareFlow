"""改善提案エンジン (P2-B).

配置済み患者の各固定枠 (PFV) について、「今の位置」と「入れ替え候補の位置」の
**限界コスト** を比べ、週あたりの移動 (+バッファー) / 距離を縮められる移動先を
提案する純ロジック + DB 集約層.

設計書: docs/plans/p2-improvement-mvp-design.md §2.

方針 (Phase 0-1 / propose-slots の資産を再利用. コピー禁止):
  - 限界コスト = ``travel(prev→X)+travel(X→next) − travel(prev→next)``. 先頭/末尾は
    該当辺のみ. 同住所は 0. 距離/移動/バッファーは ``proposal_solver`` の
    ``_travel_buffer_between`` / ``_is_same_address`` と ``auto_allocator_v2`` の
    ``haversine_km`` をそのまま使う (= 健康診断 / 自動割当と同一物差し).
  - 候補列挙は propose-slots と同じ週バケット (``load_week_course_buckets``) に対し、
    **自分の visit を除いた** existing で ``find_available_slots_for_candidate`` を呼ぶ.
  - スタッフ実態警告は P0-1 (propose-slots) の bucket 情報を流用する.
  - 計算ベースは PFV (恒久パターン). 当週 Visit ベースは Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
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
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.config import SchedulingConfig
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    _is_same_address,
    _travel_buffer_between,
    find_available_slots_for_candidate,
)
from app.services.scheduling.propose_slots_service import (
    _course_label,
    _CourseBucket,
    _fmt_hhmm,
    _staff_sex_mismatch,
    load_week_course_buckets,
)

# 改善とみなす最小効果 (分/週). config 化は Phase 3 (設計書 §2.1).
IMPROVEMENT_THRESHOLD_MIN: int = 10

# 患者 1 名あたりの最大提案件数.
MAX_SUGGESTIONS_PER_PATIENT: int = 5

# スタッフ実態警告コード (propose-slots P0-1 と同一 raw code. FE が日本語化する).
_WARN_STAFF_UNASSIGNED = "staff_unassigned"
_WARN_STAFF_ABSENT = "staff_absent"
_WARN_STAFF_SEX_MISMATCH = "staff_sex_mismatch"

# 曜日ラベル (0=Mon..6=Sun).
_WEEKDAY_JA: tuple[str, ...] = ("月", "火", "水", "木", "金", "土", "日")
_WEEKDAY_CODE: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# 座標 (lat, lng). 先頭/末尾は None.
Coord = tuple[float, float]


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


# ---------------------------------------------------------------------------
# 限界コスト
# ---------------------------------------------------------------------------


def _edge_minutes(a: Coord | None, b: Coord | None, *, config: SchedulingConfig | None) -> int:
    """1 辺の移動 + バッファー (分). 端が無ければ 0. 同住所は 0 (`_travel_buffer_between`)."""
    if a is None or b is None:
        return 0
    return _travel_buffer_between(a[0], a[1], b[0], b[1], config=config)


def _edge_km(a: Coord | None, b: Coord | None) -> float:
    """1 辺の直線距離 (km). 端が無ければ 0. 同住所は 0."""
    if a is None or b is None:
        return 0.0
    if _is_same_address(a[0], a[1], b[0], b[1]):
        return 0.0
    return haversine_km(a[0], a[1], b[0], b[1])


def compute_marginal_cost(
    target: Coord,
    prev: Coord | None,
    next: Coord | None,  # noqa: A002 - 設計書 §2.1 の signature に合わせる
    *,
    config: SchedulingConfig | None = None,
) -> tuple[int, float]:
    """限界コスト (分, km) を返す.

    ``marginal = travel(prev→target) + travel(target→next) − travel(prev→next)``.
    先頭 (prev=None) / 末尾 (next=None) は該当辺のみ (無い辺は 0). 同住所は 0.

    Args:
        target: 対象訪問の (lat, lng).
        prev: 直前訪問の (lat, lng). コース先頭なら None.
        next: 直後訪問の (lat, lng). コース末尾なら None.

    Returns:
        ``(minutes, km)``. minutes は移動 + バッファー (分), km は直線距離.
    """
    minutes = (
        _edge_minutes(prev, target, config=config)
        + _edge_minutes(target, next, config=config)
        - _edge_minutes(prev, next, config=config)
    )
    km = _edge_km(prev, target) + _edge_km(target, next) - _edge_km(prev, next)
    return minutes, km


# ---------------------------------------------------------------------------
# 内部データ構造 (API schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass
class ImprovementCandidateData:
    """改善提案 1 件 (内部表現)."""

    kind: str  # 'time_change' | 'day_change'
    target_weekday: int  # 却下指紋の対象曜日 (= 現在枠の曜日)
    # 現在枠.
    current_office_id: UUID
    current_weekday: int
    current_start: time
    current_end: time
    current_course_label: str
    current_staff_name: str | None
    # 候補枠.
    cand_office_id: UUID
    cand_office_name: str | None
    cand_weekday: int
    cand_start: time
    cand_end: time
    cand_course_code: str
    cand_course_label: str
    cand_staff_name: str | None
    # 効果 / 付帯情報.
    delta_minutes: int
    delta_km: float
    staff_warnings: list[str]
    requires_patient_confirmation: bool
    changes: list[str]
    unchanged: list[str]


@dataclass
class FilteredSummaryData:
    """提案を出さなかった内訳 (N-6). 内部表現.

    各カウンタの単位は ImprovementFilteredSummary の description を参照.
    カテゴリ間は重複しない (1 PFV / 候補は 1 カテゴリのみに計上).
    """

    pinned: int = 0
    locked: int = 0
    no_current_visit: int = 0
    dismissed: int = 0
    below_threshold: int = 0
    day_restricted: int = 0


@dataclass
class _CurrentPlacement:
    """PFV の当週実配置 (bucket + 自 visit の座標)."""

    bucket: _CourseBucket
    office_code: str | None
    start_time: time
    end_time: time
    marginal_min: int
    marginal_km: float


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _bucket_existing_excluding(
    bucket: _CourseBucket, patient_id: UUID
) -> list[ExistingVisit]:
    """bucket の既存訪問から対象患者自身を除いた ExistingVisit 列 (start 昇順)."""
    out = [
        ExistingVisit(
            start_time=v.start_time,
            end_time=v.end_time,
            lat=v.lat,
            lng=v.lng,
            service_minutes=v.service_minutes,
            patient_id=str(v.patient_id),
        )
        for v in bucket.visits
        if v.patient_id != patient_id
    ]
    out.sort(key=lambda e: _time_to_min(e.start_time))
    return out


def _neighbors_at(
    existing_sorted: list[ExistingVisit], at_min: int
) -> tuple[Coord | None, Coord | None]:
    """時刻 ``at_min`` の直前 / 直後の既存訪問座標を返す (同時刻はどちらでもない)."""
    prev: Coord | None = None
    nxt: Coord | None = None
    for e in existing_sorted:
        sm = _time_to_min(e.start_time)
        if sm < at_min:
            prev = (e.lat, e.lng)
        elif sm > at_min and nxt is None:
            nxt = (e.lat, e.lng)
    return prev, nxt


def _find_current_placement(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    *,
    patient_id: UUID,
    patient_coord: Coord,
    weekday: int,
    pfv_start: time,
    config: SchedulingConfig | None,
) -> _CurrentPlacement | None:
    """対象患者の PFV (weekday) が当週どのコースに配置されているかを特定し限界コストを算出.

    当週 visit が見つからない (未展開等) 場合は None (評価不能として skip).
    weekday 内に複数 visit がある場合は start_time が PFV と一致する行を優先する.
    """
    matched: tuple[_CourseBucket, object] | None = None
    for (_oid, wd, _code), bucket in buckets.items():
        if wd != weekday:
            continue
        for v in bucket.visits:
            if v.patient_id != patient_id:
                continue
            if matched is None or v.start_time == pfv_start:
                matched = (bucket, v)
            if v.start_time == pfv_start:
                break
    if matched is None:
        return None
    bucket, visit = matched
    existing = _bucket_existing_excluding(bucket, patient_id)
    prev, nxt = _neighbors_at(existing, _time_to_min(visit.start_time))  # type: ignore[attr-defined]
    marginal_min, marginal_km = compute_marginal_cost(
        patient_coord, prev, nxt, config=config
    )
    return _CurrentPlacement(
        bucket=bucket,
        office_code=bucket.office_code,
        start_time=visit.start_time,  # type: ignore[attr-defined]
        end_time=visit.end_time,  # type: ignore[attr-defined]
        marginal_min=marginal_min,
        marginal_km=marginal_km,
    )


def _staff_warnings_for_bucket(
    bucket: _CourseBucket, sex_restriction: str | None
) -> list[str]:
    """候補コースのスタッフ実態警告 (P0-1 の 3 コード). 除外はせず注意喚起のみ."""
    warnings: list[str] = []
    if bucket.assigned_staff_id is None:
        warnings.append(_WARN_STAFF_UNASSIGNED)
    if bucket.staff_absent:
        warnings.append(_WARN_STAFF_ABSENT)
    if _staff_sex_mismatch(bucket.staff_sex, sex_restriction):
        warnings.append(_WARN_STAFF_SEX_MISMATCH)
    return warnings


def _build_changes(
    *,
    current_weekday: int,
    current_start: time,
    current_course_label: str,
    current_staff_name: str | None,
    cand_weekday: int,
    cand_start: time,
    cand_course_label: str,
    cand_staff_name: str | None,
) -> tuple[list[str], list[str]]:
    """曜日 / 時刻 / コース / 担当の差分を日本語で組み立てる."""
    changes: list[str] = []
    unchanged: list[str] = []

    def _emit(label: str, cur: str, nxt: str) -> None:
        if cur == nxt:
            unchanged.append(f"{label}: {cur}")
        else:
            changes.append(f"{label}: {cur} → {nxt}")

    _emit("曜日", _WEEKDAY_JA[current_weekday], _WEEKDAY_JA[cand_weekday])
    _emit("時刻", _fmt_hhmm(current_start), _fmt_hhmm(cand_start))
    _emit("コース", current_course_label, cand_course_label)
    _emit(
        "担当",
        current_staff_name or "未割当",
        cand_staff_name or "未割当",
    )
    return changes, unchanged


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


async def find_improvement_candidates(
    db: AsyncSession,
    *,
    patient: Patient,
    iso_year: int,
    iso_week: int,
    config: SchedulingConfig | None = None,
) -> tuple[list[ImprovementCandidateData], FilteredSummaryData]:
    """対象患者の各 PFV について改善提案を算出する.

    read-only: DB は読むだけ.

    Returns:
        ``(suggestions, filtered_summary)``. suggestions は delta 降順・最大
        ``MAX_SUGGESTIONS_PER_PATIENT`` 件. filtered_summary は「黙って消さない」内訳.
    """
    summary = FilteredSummaryData()

    if patient.lat is None or patient.lng is None:
        return [], summary
    patient_coord: Coord = (float(patient.lat), float(patient.lng))

    # 当週の実 Visit をコース単位に集計 (propose-slots と同じローダ. 全拠点対象).
    buckets, office_name_by_id, _office_code_by_id = await load_week_course_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=[]
    )

    # 対象患者の PFV (通常週・slot_index=0).
    pfvs = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
            )
        )
    ).all()

    # 却下記憶 (未失効) の指紋集合 (kind, target_weekday).
    now = datetime.now()
    dismissals = (
        await db.scalars(
            select(SuggestionDismissal).where(
                SuggestionDismissal.patient_id == patient.id
            )
        )
    ).all()
    dismissed_fp: set[tuple[str, int]] = {
        (d.kind, d.target_weekday)
        for d in dismissals
        if d.expires_at is None or _as_naive(d.expires_at) > now
    }

    # 昼休み window 算出パラメータ (propose-slots と同一. config 優先, 既定は module 定数).
    lunch_duration = (
        config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    )
    lunch_window_start = (
        config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    )
    lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END

    candidates: list[ImprovementCandidateData] = []

    for pfv in pfvs:
        weekday = pfv.weekday

        # movability / is_pinned の除外 (設計書 §2.2).
        if pfv.is_pinned:
            summary.pinned += 1
            continue
        if pfv.movability == "locked":
            summary.locked += 1
            continue
        movability = pfv.movability  # 'unknown' | 'time_flexible' | 'day_flexible'
        day_change_allowed = movability == "day_flexible"
        requires_confirmation = movability == "unknown"

        # 当週の実配置 (= 現在の限界コスト). 見つからなければ評価不能で skip.
        current = _find_current_placement(
            buckets,
            patient_id=patient.id,
            patient_coord=patient_coord,
            weekday=weekday,
            pfv_start=pfv.start_time,
            config=config,
        )
        if current is None:
            # 当週 visit が未展開等で評価不能 (N-6 で明示する).
            summary.no_current_visit += 1
            continue
        current_course_label = _course_label(
            current.office_code, current.bucket.course_code
        )

        # 候補走査用の Candidate. movability が可否の権威なので time_type は自由
        # (None) にして営業枠内を素直に探索する (固定希望に縛らない).
        cand = Candidate(
            lat=patient_coord[0],
            lng=patient_coord[1],
            service_minutes=pfv.duration_min,
            time_type=None,
            preferred_start=None,
            preferred_end=None,
            patient_id=str(patient.id),
        )

        # 却下指紋に一致する kind は先に抑制カウント (生成前).
        time_dismissed = ("time_change", weekday) in dismissed_fp
        day_dismissed = ("day_change", weekday) in dismissed_fp
        if time_dismissed:
            summary.dismissed += 1
        if day_change_allowed and day_dismissed:
            summary.dismissed += 1

        for (office_id, wd, course_code), bucket in buckets.items():
            existing = _bucket_existing_excluding(bucket, patient.id)
            lunch = compute_lunch_window(
                bucket.visits,
                warnings=None,
                weekday=wd,
                duration=lunch_duration,
                window_start=lunch_window_start,
                window_end=lunch_window_end,
            )
            slots = find_available_slots_for_candidate(
                existing,
                cand,
                lunch_window=lunch,
                weekday=wd,
                config=config,
            )
            if not slots:
                continue

            is_same_weekday = wd == weekday
            kind = "time_change" if is_same_weekday else "day_change"

            for slot in slots:
                # 現在位置そのもの (同拠点・同曜日・同コース・同時刻) は候補にしない.
                if (
                    is_same_weekday
                    and office_id == current.bucket.office_id
                    and course_code == current.bucket.course_code
                    and slot.start == current.start_time
                ):
                    continue

                prev, nxt = _neighbors_at(existing, _time_to_min(slot.start))
                cand_min, cand_km = compute_marginal_cost(
                    patient_coord, prev, nxt, config=config
                )
                delta_min = current.marginal_min - cand_min
                delta_km = current.marginal_km - cand_km

                # 曜日変更が許されない movability の場合、閾値超の改善は
                # day_restricted として抑制カウント (黙って消さない).
                if kind == "day_change" and not day_change_allowed:
                    if delta_min >= IMPROVEMENT_THRESHOLD_MIN:
                        summary.day_restricted += 1
                    continue

                # 却下指紋一致 (この kind は上で 1 回カウント済) → 生成しない.
                if kind == "time_change" and time_dismissed:
                    continue
                if kind == "day_change" and day_dismissed:
                    continue

                if delta_min < IMPROVEMENT_THRESHOLD_MIN:
                    summary.below_threshold += 1
                    continue

                cand_label = _course_label(bucket.office_code, course_code)
                changes, unchanged = _build_changes(
                    current_weekday=weekday,
                    current_start=current.start_time,
                    current_course_label=current_course_label,
                    current_staff_name=current.bucket.staff_name,
                    cand_weekday=wd,
                    cand_start=slot.start,
                    cand_course_label=cand_label,
                    cand_staff_name=bucket.staff_name,
                )
                candidates.append(
                    ImprovementCandidateData(
                        kind=kind,
                        target_weekday=weekday,
                        current_office_id=current.bucket.office_id,
                        current_weekday=weekday,
                        current_start=current.start_time,
                        current_end=current.end_time,
                        current_course_label=current_course_label,
                        current_staff_name=current.bucket.staff_name,
                        cand_office_id=office_id,
                        cand_office_name=office_name_by_id.get(office_id),
                        cand_weekday=wd,
                        cand_start=slot.start,
                        cand_end=slot.end,
                        cand_course_code=course_code,
                        cand_course_label=cand_label,
                        cand_staff_name=bucket.staff_name,
                        delta_minutes=delta_min,
                        delta_km=round(delta_km, 2),
                        staff_warnings=_staff_warnings_for_bucket(
                            bucket, patient.sex_restriction
                        ),
                        requires_patient_confirmation=(
                            requires_confirmation and kind == "time_change"
                        ),
                        changes=changes,
                        unchanged=unchanged,
                    )
                )

    # delta (分) 降順 → 同点は (距離降順, 早い時刻) で安定化. 患者あたり最大 N 件.
    candidates.sort(
        key=lambda c: (-c.delta_minutes, -c.delta_km, _time_to_min(c.cand_start))
    )
    return candidates[:MAX_SUGGESTIONS_PER_PATIENT], summary


def _as_naive(dt: datetime) -> datetime:
    """tz-aware / naive 混在に備え naive (UTC 相当) に揃えて比較する."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def weekday_code(weekday: int) -> str:
    """0=Mon..6=Sun を曜日コードに変換する (endpoint 用)."""
    return _WEEKDAY_CODE[weekday]


__all__ = [
    "IMPROVEMENT_THRESHOLD_MIN",
    "MAX_SUGGESTIONS_PER_PATIENT",
    "FilteredSummaryData",
    "ImprovementCandidateData",
    "compute_marginal_cost",
    "find_improvement_candidates",
    "weekday_code",
]
