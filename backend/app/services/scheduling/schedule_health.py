"""スケジュール健康診断 (schedule-advisor Phase 1「診る」) の純集計サービス.

``GET /api/v1/schedule/v2/schedule-health`` の本体ロジック (read-only):
    1. 対象 ISO 週 × 拠点の実 Visit を 1 回ロードし、Course (course_code・割付
       スタッフ名) / Patient (lat/lng) と JOIN して ``(office_id, weekday,
       course_code)`` 単位に集計する。
    2. 各コース内で start_time 昇順に整列し、連続ペアごとに移動時間・距離・
       バッファー・隙間 (gap) を計算する。
    3. 曜日小計・週小計を積み上げ、空のコース / 曜日は出力しない。

効果の物差しは提案エンジンと同一の travel モデル (単一ソース):
    - 距離 = ``auto_allocator_v2.haversine_km``
    - 移動時間 = ``auto_allocator_v2.haversine_minutes(距離, speed_kmh=config.travel_speed_kmh)``
    - 同住所判定 = ``auto_allocator_v2._address_bucket`` (``SAME_ADDRESS_TOLERANCE``)
    - バッファー = ``config.visit_buffer_min`` (異住所遷移のみ)
    距離ロジックはコピーせず import して再利用する (自動割当 / 提案と挙動一致)。

ローダ方針 (独自ローダを新設):
    ``propose_slots_service.load_week_course_buckets`` の
    ``Visit JOIN Course OUTER JOIN Staff`` 集計パターンを出典に、本サービス専用の
    ローダを新設した。理由は 2 点で既存ローダと要件が異なるため:
      - status フィルタが ``planned`` のみでなく ``planned/in_progress/completed``。
      - 座標欠損 (lat/lng None) 患者を **除外せずレスポンスに含める** 必要がある
        (既存ローダは座標欠損 visit を skip する)。
    既存ローダには手を入れず propose-slots の挙動を一切変えないため、独自ローダとした。

座標欠損の扱い:
    lat/lng いずれか欠損の患者を含む連続遷移は、距離を算出できないため
    ``travel=0`` とし ``buffer=config.visit_buffer_min`` を計上する (例外は出さない)。
    当該 visit はレスポンスの集計に含める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import (
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.schemas.v2.schedule_health import (
    ScheduleHealthCourse,
    ScheduleHealthOffice,
    ScheduleHealthResponse,
    ScheduleHealthTotals,
    ScheduleHealthTrendResponse,
    ScheduleHealthTrendTotals,
    ScheduleHealthTrendWeek,
    ScheduleHealthWeekday,
)
from app.services.scheduling.auto_allocator_v2 import (
    _address_bucket,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.config import SchedulingConfig

# 集計対象の visit ステータス (schedule-advisor §3 Phase 1).
# cancelled / deleted_at は対象外。in_progress / completed は含める。
_HEALTH_STATUSES: tuple[str, ...] = (
    VISIT_STATUS_PLANNED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_COMPLETED,
)


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass
class _HealthVisit:
    """健康診断用の 1 訪問 (座標は欠損しうる)."""

    patient_id: UUID
    start_time: time
    end_time: time
    lat: float | None
    lng: float | None


@dataclass
class _HealthCourse:
    """対象週 1 コース (office × weekday × course_code) の集計."""

    office_id: UUID
    weekday: int
    course_code: str
    staff_name: str | None = None
    visits: list[_HealthVisit] = field(default_factory=list)


async def _load_health_buckets(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> tuple[dict[tuple[UUID, int, str], _HealthCourse], dict[UUID, str]]:
    """対象週 × 拠点の実 Visit を 1 回ロードし、コース単位に集計する.

    出典: ``propose_slots_service.load_week_course_buckets`` (Visit JOIN Course
    OUTER JOIN Staff)。本サービス要件に合わせ status を拡張し、座標欠損 patient も
    除外せず含める点が異なる (詳細はモジュール docstring)。

    Returns:
        ``({(office_id, weekday, course_code): _HealthCourse}, {office_id: name})``。
    """
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc
    week_upper = week_sunday + timedelta(days=1)  # 排他上限.

    stmt = (
        select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status.in_(_HEALTH_STATUSES),
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

    # patient 座標を 1 回でまとめてロード (座標欠損 patient も含める).
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

    # 拠点 name map (バケットに出た office + 明示要求 office).
    office_ids_in_use: set[UUID] = {course.office_id for (_v, course, _s) in rows} | set(
        office_ids
    )
    office_name_by_id: dict[UUID, str] = {}
    if office_ids_in_use:
        orows = await db.scalars(select(Office).where(Office.id.in_(office_ids_in_use)))
        office_name_by_id = {o.id: o.name for o in orows.all()}

    buckets: dict[tuple[UUID, int, str], _HealthCourse] = {}
    for v, course, staff in rows:
        patient = patients_by_id.get(v.patient_id)
        if patient is None:
            continue  # patient soft-deleted 等: 集計対象外.
        code = course.code or "M"
        key = (course.office_id, course.weekday, code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _HealthCourse(
                office_id=course.office_id,
                weekday=course.weekday,
                course_code=code,
                staff_name=staff.name if staff is not None else None,
            )
            buckets[key] = bucket
        bucket.visits.append(
            _HealthVisit(
                patient_id=patient.id,
                start_time=v.start_time,
                end_time=v.end_time,
                # 座標欠損はそのまま None を保持し、遷移計算で travel=0 扱いにする.
                lat=float(patient.lat) if patient.lat is not None else None,
                lng=float(patient.lng) if patient.lng is not None else None,
            )
        )

    return buckets, office_name_by_id


def _compute_course_metrics(
    bucket: _HealthCourse,
    *,
    config: SchedulingConfig,
) -> tuple[ScheduleHealthCourse, float]:
    """1 コースの健康診断メトリクスを算出する (純関数).

    連続ペアごとに:
      - 同住所 (``_address_bucket`` 一致) → travel=0, buffer=0
      - 異住所 → haversine 距離 + ``haversine_minutes`` / buffer=config.visit_buffer_min
      - 座標欠損を含む遷移 → travel=0 / buffer=config.visit_buffer_min (距離算出不能)
    gap = Σ max(0, 次start − 前end − travel − buffer) (負値クランプ)。

    Returns:
        ``(ScheduleHealthCourse, raw_travel_km)`` のタプル。
        ``raw_travel_km`` は丸め前の生値で、呼び出し側が曜日小計・週小計を
        「生値を合計してから1回だけ丸める」ために使う (二重丸め防止)。
        コース単位の表示値 ``ScheduleHealthCourse.travel_km`` は round(,1) 済み。
    """
    sv = sorted(bucket.visits, key=lambda x: _time_to_min(x.start_time))

    service_minutes = sum(
        max(0, _time_to_min(v.end_time) - _time_to_min(v.start_time)) for v in sv
    )
    patient_count = len({v.patient_id for v in sv})

    travel_minutes = 0
    travel_km = 0.0
    buffer_minutes = 0
    gap_minutes = 0
    for i in range(1, len(sv)):
        prev = sv[i - 1]
        cur = sv[i]
        coords_known = (
            prev.lat is not None
            and prev.lng is not None
            and cur.lat is not None
            and cur.lng is not None
        )
        if not coords_known:
            # 座標欠損: 距離算出不能 → travel=0, buffer=config値.
            pair_travel = 0
            pair_km = 0.0
            pair_buffer = config.visit_buffer_min
        elif _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng):  # type: ignore[arg-type]
            # 同住所: 移動なし・バッファーなし.
            pair_travel = 0
            pair_km = 0.0
            pair_buffer = 0
        else:
            # 異住所: travel モデルで距離・移動時間・バッファーを算出.
            pair_km = haversine_km(prev.lat, prev.lng, cur.lat, cur.lng)  # type: ignore[arg-type]
            pair_travel = haversine_minutes(pair_km, speed_kmh=config.travel_speed_kmh)
            pair_buffer = config.visit_buffer_min

        travel_minutes += pair_travel
        travel_km += pair_km
        buffer_minutes += pair_buffer
        # 「移動しても余る待ち時間」: 次開始 − 前終了 − 移動 − バッファー (負値は 0).
        raw_gap = (
            _time_to_min(cur.start_time)
            - _time_to_min(prev.end_time)
            - pair_travel
            - pair_buffer
        )
        gap_minutes += max(0, raw_gap)

    course = ScheduleHealthCourse(
        course_code=bucket.course_code,
        staff_name=bucket.staff_name,
        visit_count=len(sv),
        patient_count=patient_count,
        service_minutes=service_minutes,
        travel_minutes=travel_minutes,
        # コース単位の表示値は round(,1)。生値 travel_km は呼び出し側が小計集計に使う。
        travel_km=round(travel_km, 1),
        buffer_minutes=buffer_minutes,
        gap_minutes=gap_minutes,
    )
    return course, travel_km  # (表示用スキーマ, 生値km) — 小計は生値を合算してから丸める


def _sum_totals(courses: list[ScheduleHealthCourse], raw_travel_km: float) -> ScheduleHealthTotals:
    """コース群の 6 フィールドを合算した小計を返す.

    travel_km は二重丸めを防ぐため、コース単位の丸め済み値ではなく
    ``raw_travel_km`` (生値の合計) を受け取り、ここで **1 回だけ** round(,1) する。
    """
    return ScheduleHealthTotals(
        visit_count=sum(c.visit_count for c in courses),
        service_minutes=sum(c.service_minutes for c in courses),
        travel_minutes=sum(c.travel_minutes for c in courses),
        # 「生値を合計してから1回だけ丸める」方針: コース単位の c.travel_km は使わない。
        travel_km=round(raw_travel_km, 1),
        buffer_minutes=sum(c.buffer_minutes for c in courses),
        gap_minutes=sum(c.gap_minutes for c in courses),
    )


async def compute_schedule_health(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    config: SchedulingConfig,
) -> ScheduleHealthResponse:
    """対象週の健康診断レスポンスを組み立てる (read-only 集計).

    空のコース / 曜日 / 拠点は出力しない。拠点は office_name → office_id、曜日は
    昇順、コースは course_code 昇順で安定ソートする。
    """
    buckets, office_name_by_id = await _load_health_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=office_ids
    )

    # office_id → weekday → (コースメトリクス, 生値km) を積み上げる.
    # travel_km は「生値を合計してから1回だけ丸める」ため生値を別途保持する (二重丸め防止).
    by_office: dict[UUID, dict[int, list[tuple[ScheduleHealthCourse, float]]]] = {}
    for bucket in buckets.values():
        if not bucket.visits:
            continue  # 空コースは出力しない.
        metrics, raw_km = _compute_course_metrics(bucket, config=config)
        by_office.setdefault(bucket.office_id, {}).setdefault(bucket.weekday, []).append(
            (metrics, raw_km)
        )

    offices: list[ScheduleHealthOffice] = []
    for office_id, weekdays_map in by_office.items():
        weekdays: list[ScheduleHealthWeekday] = []
        all_courses_in_office: list[ScheduleHealthCourse] = []
        office_raw_km = 0.0
        for weekday in sorted(weekdays_map.keys()):
            pairs = sorted(weekdays_map[weekday], key=lambda p: p[0].course_code)
            courses = [p[0] for p in pairs]
            wd_raw_km = sum(p[1] for p in pairs)
            weekdays.append(
                ScheduleHealthWeekday(
                    weekday=weekday,
                    courses=courses,
                    totals=_sum_totals(courses, wd_raw_km),
                )
            )
            all_courses_in_office.extend(courses)
            office_raw_km += wd_raw_km
        offices.append(
            ScheduleHealthOffice(
                office_id=office_id,
                office_name=office_name_by_id.get(office_id, ""),
                weekdays=weekdays,
                week_totals=_sum_totals(all_courses_in_office, office_raw_km),
            )
        )

    offices.sort(key=lambda o: (o.office_name, str(o.office_id)))

    return ScheduleHealthResponse(
        iso_year=iso_year,
        iso_week=iso_week,
        offices=offices,
    )


# ---------------------------------------------------------------------------
# 見直しどきトレンド (schedule-advisor §3 Phase 3「見直しどき通知」)
#   履歴テーブルは作らず、過去週の実 Visit に対し compute_schedule_health を
#   週ごとに呼び直してトレンドを組み立てる (既存関数の挙動不変)。
# ---------------------------------------------------------------------------

# 遡り週数の上限 (安全弁: 1 週=DB 数クエリのため過大要求を防ぐ).
SCHEDULE_HEALTH_TREND_MAX_WEEKS = 12


def _sum_trend_totals(health: ScheduleHealthResponse) -> ScheduleHealthTrendTotals:
    """1 週の健康診断レスポンスから office 横断合計 (4 指標) を積み上げる.

    travel_km は各 office の week_totals (小数1桁済み) を合算してから 1 回丸める。
    visit ゼロの週は offices=[] なので全 0 になる。
    """
    return ScheduleHealthTrendTotals(
        visit_count=sum(o.week_totals.visit_count for o in health.offices),
        travel_minutes=sum(o.week_totals.travel_minutes for o in health.offices),
        travel_km=round(sum(o.week_totals.travel_km for o in health.offices), 1),
        gap_minutes=sum(o.week_totals.gap_minutes for o in health.offices),
    )


async def compute_schedule_health_trend(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    weeks: int,
    office_ids: list[UUID],
    config: SchedulingConfig,
) -> ScheduleHealthTrendResponse:
    """指定週から遡って ``weeks`` 週分の office 横断合計を古→新順で返す.

    各週について ``compute_schedule_health`` を 1 回呼び、その週の全拠点
    ``week_totals`` を横断合計する薄いループ。visit ゼロの週も totals 全 0 で
    含める (欠測が見えるように)。``weeks`` は ``[1, SCHEDULE_HEALTH_TREND_MAX_WEEKS]``
    にクランプする。

    週境界は ``propose_slots_service`` と同方式で、指定週の月曜から 7 日ずつ遡る
    (``date.fromisocalendar`` → ``timedelta(days=7)``)。年跨ぎは各週月曜の
    ``isocalendar()`` から (iso_year, iso_week) を再導出するため自然に扱える。
    """
    weeks = max(1, min(weeks, SCHEDULE_HEALTH_TREND_MAX_WEEKS))
    try:
        target_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc

    # 古→新順: 最古週 (weeks-1 週前) を先頭、指定週を末尾にする.
    week_mondays = [target_monday - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]

    trend_weeks: list[ScheduleHealthTrendWeek] = []
    for monday in week_mondays:
        iso = monday.isocalendar()
        health = await compute_schedule_health(
            db,
            iso_year=iso.year,
            iso_week=iso.week,
            office_ids=office_ids,
            config=config,
        )
        trend_weeks.append(
            ScheduleHealthTrendWeek(
                iso_year=iso.year,
                iso_week=iso.week,
                totals=_sum_trend_totals(health),
            )
        )

    return ScheduleHealthTrendResponse(weeks=trend_weeks)


__all__ = [
    "SCHEDULE_HEALTH_TREND_MAX_WEEKS",
    "compute_schedule_health",
    "compute_schedule_health_trend",
]
