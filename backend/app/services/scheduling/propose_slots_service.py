"""提案 API (Phase2-2) オーケストレーション層.

``POST /api/v1/schedule/v2/propose-slots`` の本体ロジック:
    1. 対象週 × 拠点の実 Visit を 1 回ロードし、(office, weekday, course_code)
       単位の既存訪問列・担当スタッフ・拠点名を構築する (N+1 回避).
    2. 各 (拠点開講コース × 候補希望曜日) について Stage1 純ソルバ
       (``find_available_slots_for_candidate``) を呼び、実現可能な開始時刻のみ取得.
    3. 移動効率 (近接) / 希望適合 / 空きの平準化 を合成スコアでランキング.
       同住所ペア成立は最優先.
    4. 各スロットに理由バッジ・警告・ミニスケジュールを付与して返す.

read-only: DB は読むだけ. 実現不能な時刻は一切返さない (ソルバが弾く).

設計方針:
    - 既存ローダ (schedule.py の ``Visit JOIN Course OUTER JOIN Staff`` 集計) と
      同じ手法で当週 visit を読む.
    - 距離 / 移動 / バッファー / 昼休み / 18:00 / 容量 / time_type / 同住所は
      すべて ``proposal_solver`` (= auto_allocator_v2 と同手法) に委譲する.
    - 昼休み window は自動割当と同じ ``compute_lunch_window`` をコース単位で算出.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.patient_excel.schema import OFFICE_CODE_TO_SHORT
from app.services.scheduling.auto_allocator_v2 import (
    NOON_HOUR,
    _address_bucket,
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit as _V2Visit,
)
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    Slot,
    find_available_slots_for_candidate,
)

# ランキング合成スコアの重み.
# 移動効率 (近接): そのコース既存訪問への最小距離が小さいほど高.
# 希望適合: 希望曜日 / 時間帯 / time_type 一致で加点.
# 空きの平準化: コース残容量 (件数 + 分) が多いほど高.
_W_PROXIMITY: float = 50.0
_W_PREFERENCE: float = 30.0
_W_BALANCE: float = 20.0
# 同住所ペア成立は最優先で大きなボーナスを与える.
_PAIR_BONUS: float = 1000.0
# 近接スコアの飽和距離 (km). これ以上離れると近接スコア 0.
_PROXIMITY_SAT_KM: float = 5.0

_COURSE_MAX_MINUTES: int = 480
_MAX_PATIENTS_PER_COURSE: int = 6


def _fmt_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass
class _CourseBucket:
    """対象週 1 コース (office × weekday × course_code) の集計."""

    office_id: UUID
    weekday: int
    course_code: str
    office_code: str | None = None
    staff_name: str | None = None
    visits: list[_V2Visit] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateInput:
    """提案算出に渡す候補患者 (座標確定済み)."""

    lat: float
    lng: float
    service_minutes: int
    time_type: str | None
    preferred_start: time | None
    preferred_end: time | None
    preferred_weekdays: frozenset[int]
    requires_multiple_staff: bool
    existing_patient_id: UUID | None


@dataclass
class ProposedSlot:
    """ランキング済み提案スロット (API schema に詰める前の内部表現)."""

    office_id: UUID
    office_name: str | None
    weekday: int
    course_code: str
    course_label: str
    staff_name: str | None
    start: time
    end: time
    score: float
    reasons: list[str]
    warnings: list[str]
    is_pair: bool
    pair_partner: str | None
    mini_schedule: list[dict[str, object]]


async def load_week_course_buckets(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> tuple[dict[tuple[UUID, int, str], _CourseBucket], dict[UUID, str], dict[UUID, str | None]]:
    """対象週 × 拠点の実 Visit を 1 回ロードし、コース単位に集計する.

    schedule.py の ``Visit JOIN Course OUTER JOIN Staff`` と同じ読み方:
        - planned かつ未削除の visit のみ.
        - Course (= 当該週 weekday/code/office, 担当 staff) 経由でグルーピング.
        - patient 座標 (lat/lng) は Patient から引く (visit には無い).

    Returns:
        ``({(office_id, weekday, course_code): _CourseBucket}, {office_id: name},
        {office_id: code})``. ``code`` は course_label 用の正準短縮
        (OFFICE_CODE_TO_SHORT) を引くために使う (board と同一短縮へ統一).
    """
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday_plus1 = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc
    # week_sunday_plus1 を排他上限にするため +1 日.
    from datetime import timedelta

    week_upper = week_sunday_plus1 + timedelta(days=1)

    stmt = (
        select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status == VISIT_STATUS_PLANNED,
            Visit.visit_date >= week_monday,
            Visit.visit_date < week_upper,
            Course.deleted_at.is_(None),
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
        )
    )
    if office_ids:
        stmt = stmt.where(Course.office_id.in_(office_ids))

    rows = (await db.execute(stmt)).all()

    # patient 座標を 1 回でまとめてロード.
    patient_ids = {v.patient_id for (v, _c, _s) in rows}
    patients_by_id: dict[UUID, Patient] = {}
    if patient_ids:
        prows = await db.scalars(
            select(Patient).where(
                Patient.id.in_(patient_ids),
                Patient.deleted_at.is_(None),
            )
        )
        patients_by_id = {p.id: p for p in prows.all()}

    # 拠点 name / code map (対象 office + バケットに出た office) を先に 1 回ロード.
    # code は course_label の正準短縮 (OFFICE_CODE_TO_SHORT) を引くために bucket へ持たせる.
    office_ids_in_use: set[UUID] = {course.office_id for (_v, course, _s) in rows} | set(office_ids)
    office_name_by_id: dict[UUID, str] = {}
    office_code_by_id: dict[UUID, str | None] = {}
    if office_ids_in_use:
        orows = await db.scalars(select(Office).where(Office.id.in_(office_ids_in_use)))
        for o in orows.all():
            office_name_by_id[o.id] = o.name
            office_code_by_id[o.id] = o.code

    buckets: dict[tuple[UUID, int, str], _CourseBucket] = {}
    for v, course, staff in rows:
        patient = patients_by_id.get(v.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        code = course.code or "M"
        key = (course.office_id, course.weekday, code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _CourseBucket(
                office_id=course.office_id,
                weekday=course.weekday,
                course_code=code,
                office_code=office_code_by_id.get(course.office_id),
                staff_name=staff.name if staff is not None else None,
            )
            buckets[key] = bucket
        start_min = _time_to_min(v.start_time)
        end_min = _time_to_min(v.end_time)
        duration = end_min - start_min if end_min > start_min else 30
        am_pm = "am" if v.start_time.hour < NOON_HOUR else "pm"
        bucket.visits.append(
            _V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=course.weekday,
                start_time=v.start_time,
                end_time=v.end_time,
                service_minutes=duration,
                lat=float(patient.lat),
                lng=float(patient.lng),
                office_id=course.office_id,
                am_pm=am_pm,  # type: ignore[arg-type]
                source_kind="fixed",
                course_code=code,
            )
        )

    return buckets, office_name_by_id, office_code_by_id


def _to_existing_visits(bucket: _CourseBucket) -> list[ExistingVisit]:
    """コースの V2Visit 列を solver 用 ``ExistingVisit`` 列に変換 (start 昇順)."""
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
    ]
    out.sort(key=lambda e: _time_to_min(e.start_time))
    return out


def _min_distance_km(bucket: _CourseBucket, lat: float, lng: float) -> float | None:
    """候補とコース既存訪問群との最小直線距離 (km). 既存 0 件なら None."""
    if not bucket.visits:
        return None
    return min(haversine_km(lat, lng, v.lat, v.lng) for v in bucket.visits)


def _course_label(office_code: str | None, course_code: str) -> str:
    """UI 表示用ラベル (拠点短縮 + コード, 例: 稲A / 津A).

    office_code 基準の正準短縮 (``patient_excel.schema.OFFICE_CODE_TO_SHORT``,
    INAGE→稲 / TSUGA→津) を使い board / 患者 Excel / グリッド集計と同一表記へ統一する.
    マップに無い拠点コードはコードそのものを使う. office_code が無い場合は code のみ.
    """
    if not office_code:
        return course_code
    short = OFFICE_CODE_TO_SHORT.get(office_code, office_code)
    return f"{short}{course_code}"


def _build_mini_schedule(
    bucket: _CourseBucket,
    slot: Slot,
    *,
    candidate_name: str,
    pair_partner: str | None,
) -> list[dict[str, object]]:
    """コース当日の既存訪問 + 提案枠を時刻順に並べたミニスケジュール."""
    entries: list[dict[str, object]] = []
    for v in bucket.visits:
        entries.append(
            {
                "time": _fmt_hhmm(v.start_time),
                "name": v.patient_name,
                "ins": None,
                "is_here": False,
                "is_pair": bool(pair_partner is not None and v.patient_name == pair_partner),
                "_sort": _time_to_min(v.start_time),
            }
        )
    entries.append(
        {
            "time": _fmt_hhmm(slot.start),
            "name": candidate_name,
            "ins": None,
            "is_here": True,
            "is_pair": slot.same_address_pair,
            "_sort": _time_to_min(slot.start),
        }
    )
    entries.sort(key=lambda e: e["_sort"])  # type: ignore[index,arg-type,return-value]
    for e in entries:
        e.pop("_sort", None)
    return entries


# 警告種別の日本語ラベル (UI バッジ用).
_WARNING_LABEL_JA: dict[str, str] = {
    "travel_time_shortage": "移動時間がやや不足 (前訪問の早終わりで吸収)",
}


def _slot_reasons_and_warnings(
    slot: Slot,
    candidate: CandidateInput,
    *,
    min_dist_km: float | None,
    remaining_count: int,
    matched_weekday: bool,
) -> tuple[list[str], list[str]]:
    """スロットの理由バッジ + 警告ラベルを組み立てる."""
    reasons: list[str] = []
    warnings: list[str] = []

    if slot.same_address_pair:
        reasons.append("同住所 / 移動0")
    elif min_dist_km is not None and min_dist_km <= 1.0:
        reasons.append("近い")

    # 希望適合: 曜日一致 + time_type / 時間帯一致.
    if matched_weekday and candidate.preferred_weekdays:
        reasons.append("希望曜日")
    if candidate.time_type in ("固定", "時間帯", "午前", "午後"):
        reasons.append("希望時間帯一致")

    # 空きの平準化: 残容量が多い (= 余裕).
    if remaining_count >= 3:
        reasons.append("余裕あり")

    if slot.warning:
        warnings.append(_WARNING_LABEL_JA.get(slot.warning, slot.warning))
    return reasons, warnings


def _score_slot(
    slot: Slot,
    *,
    min_dist_km: float | None,
    remaining_count: int,
    remaining_minutes: int,
    matched_weekday: bool,
    matched_time_type: bool,
) -> float:
    """合成スコア (降順用). 同住所ペアは大ボーナスで最優先."""
    score = 0.0
    if slot.same_address_pair:
        score += _PAIR_BONUS

    # 移動効率 (近接): 最小距離が小さいほど高. 既存 0 件は中立 (満点の半分).
    if min_dist_km is None:
        proximity = 0.5
    else:
        proximity = max(0.0, 1.0 - min(min_dist_km, _PROXIMITY_SAT_KM) / _PROXIMITY_SAT_KM)
    score += _W_PROXIMITY * proximity

    # 希望適合.
    pref = 0.0
    if matched_weekday:
        pref += 0.5
    if matched_time_type:
        pref += 0.5
    score += _W_PREFERENCE * pref

    # 空きの平準化: 残件数 + 残分の平均的余裕度 (0..1).
    count_ratio = remaining_count / _MAX_PATIENTS_PER_COURSE
    min_ratio = remaining_minutes / _COURSE_MAX_MINUTES
    balance = max(0.0, min(1.0, (count_ratio + min_ratio) / 2.0))
    score += _W_BALANCE * balance

    # 警告ありは僅かに減点 (実現可能だが注意).
    if slot.warning:
        score -= 1.0
    return score


def compute_all_proposed_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None = None,
    candidate_name: str = "(提案)",
) -> list[ProposedSlot]:
    """全 (開講コース × 候補希望曜日) でソルバを回し、ランキング済み全スロットを返す.

    ``compute_proposed_slots`` の limit なし版. slots[] と coverage で同一の
    実現可能性判定 / ランキングを共有するための内部 API.

    実現不能なコース / 曜日は候補に出さない (= 入らない時間は提案しない).
    """
    target_weekdays = candidate.preferred_weekdays or frozenset(range(7))
    cand = Candidate(
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        time_type=candidate.time_type,
        preferred_start=candidate.preferred_start,
        preferred_end=candidate.preferred_end,
        patient_id=str(candidate.existing_patient_id) if candidate.existing_patient_id else None,
    )

    results: list[ProposedSlot] = []
    for (office_id, weekday, course_code), bucket in buckets.items():
        if office_ids and office_id not in office_ids:
            continue
        if weekday not in target_weekdays:
            continue

        existing = _to_existing_visits(bucket)
        lunch = compute_lunch_window(bucket.visits, warnings=None, weekday=weekday)

        slots = find_available_slots_for_candidate(
            existing,
            cand,
            lunch_window=lunch,
            weekday=weekday,
        )
        if not slots:
            continue

        min_dist = _min_distance_km(bucket, candidate.lat, candidate.lng)
        remaining_count = max(0, _MAX_PATIENTS_PER_COURSE - len(bucket.visits))
        used_minutes = sum(v.service_minutes for v in bucket.visits)
        remaining_minutes = max(0, _COURSE_MAX_MINUTES - used_minutes)
        matched_weekday = (
            bool(candidate.preferred_weekdays) and weekday in candidate.preferred_weekdays
        )
        matched_time_type = candidate.time_type in ("固定", "時間帯", "午前", "午後")
        office_name = office_name_by_id.get(office_id)
        # course_label は office_code 基準の正準短縮 (稲A/津A) を使う. bucket が持つ
        # code を優先し、無ければ map から引く (どちらも無ければ code のみ).
        office_code = bucket.office_code
        if office_code is None and office_code_by_id is not None:
            office_code = office_code_by_id.get(office_id)
        label = _course_label(office_code, course_code)

        for slot in slots:
            # 同住所ペア相手の名前を引く (slot.start に既存単独患者がいる).
            pair_partner: str | None = None
            if slot.same_address_pair:
                for v in bucket.visits:
                    if _address_bucket(v.lat, v.lng) == _address_bucket(
                        candidate.lat, candidate.lng
                    ):
                        pair_partner = v.patient_name
                        break

            reasons, warnings = _slot_reasons_and_warnings(
                slot,
                candidate,
                min_dist_km=min_dist,
                remaining_count=remaining_count,
                matched_weekday=matched_weekday,
            )
            score = _score_slot(
                slot,
                min_dist_km=min_dist,
                remaining_count=remaining_count,
                remaining_minutes=remaining_minutes,
                matched_weekday=matched_weekday,
                matched_time_type=matched_time_type,
            )
            mini = _build_mini_schedule(
                bucket,
                slot,
                candidate_name=candidate_name,
                pair_partner=pair_partner,
            )
            results.append(
                ProposedSlot(
                    office_id=office_id,
                    office_name=office_name,
                    weekday=weekday,
                    course_code=course_code,
                    course_label=label,
                    staff_name=bucket.staff_name,
                    start=slot.start,
                    end=slot.end,
                    score=round(score, 4),
                    reasons=reasons,
                    warnings=warnings,
                    is_pair=slot.same_address_pair,
                    pair_partner=pair_partner,
                    mini_schedule=mini,
                )
            )

    # 合成スコア降順 → 同点は (近さ, 早い時刻, 拠点, コード) で安定化.
    results.sort(
        key=lambda r: (
            -r.score,
            _time_to_min(r.start),
            str(r.office_id),
            r.weekday,
            r.course_code,
        )
    )
    return results


def compute_proposed_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None = None,
    candidate_name: str = "(提案)",
    limit: int = 10,
) -> list[ProposedSlot]:
    """全 (開講コース × 候補希望曜日) でソルバを回し、上位 ``limit`` 件を返す.

    実現不能なコース / 曜日は候補に出さない (= 入らない時間は提案しない).
    """
    return compute_all_proposed_slots(
        buckets,
        office_name_by_id,
        candidate,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        candidate_name=candidate_name,
    )[:limit]


@dataclass
class CoverageDay:
    """カバレッジ 1 曜日ぶん (実現可否 + その曜日の最良枠)."""

    weekday: int
    has_slot: bool
    best_slot: ProposedSlot | None


@dataclass
class Coverage:
    """週N日カバレッジ集計 (API schema に詰める前の内部表現)."""

    required_days: int
    requested_weekdays: list[int]
    per_day: list[CoverageDay]
    covered_days: int
    fully_covered: bool


def compute_coverage(
    all_proposed: list[ProposedSlot],
    *,
    requested_weekdays: frozenset[int],
    required_days: int,
) -> Coverage:
    """ランキング済み全スロットを曜日ごとにグルーピングしカバレッジを算出する.

    ``all_proposed`` はスコア降順済み (``compute_all_proposed_slots`` の戻り値)
    を想定. 各希望曜日について、その曜日の最上位スロットを最良枠とし、
    1 つでもあれば has_slot=True とする (実現可能性判定自体は流用; 再計算しない).

    Args:
        all_proposed: limit なしのランキング済みスロット列.
        requested_weekdays: 希望曜日 (空なら全曜日を対象にする).
        required_days: 必要日数 (frequency_per_week 優先, 無ければ希望曜日数).
    """
    target_weekdays = sorted(requested_weekdays) if requested_weekdays else list(range(7))

    # 曜日 → その曜日の最上位スロット (all_proposed はスコア降順なので最初に出た物).
    best_by_weekday: dict[int, ProposedSlot] = {}
    for slot in all_proposed:
        best_by_weekday.setdefault(slot.weekday, slot)

    per_day: list[CoverageDay] = []
    covered_days = 0
    for wd in target_weekdays:
        best = best_by_weekday.get(wd)
        has_slot = best is not None
        if has_slot:
            covered_days += 1
        per_day.append(CoverageDay(weekday=wd, has_slot=has_slot, best_slot=best))

    fully_covered = required_days > 0 and covered_days >= required_days
    return Coverage(
        required_days=required_days,
        requested_weekdays=target_weekdays,
        per_day=per_day,
        covered_days=covered_days,
        fully_covered=fully_covered,
    )


__all__ = [
    "CandidateInput",
    "Coverage",
    "CoverageDay",
    "ProposedSlot",
    "compute_all_proposed_slots",
    "compute_coverage",
    "compute_proposed_slots",
    "load_week_course_buckets",
]
