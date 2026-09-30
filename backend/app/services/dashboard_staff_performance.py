"""スタッフ別の実績 (ダッシュボード) の集計.

正典設計書: ``docs/plans/dashboard-staff-performance-design-2026-09-30.md`` (§3 数字の決め方・
§5 PO 確認済み)。1 か月で約 600 訪問なので、期間内の訪問・実績時刻・座標・イベントを
読み、Python 側で 職員 × 日 → 期間 / 週 に集計する。

数字の決め方 (§3):

* 訪問の持ち主 = 主担当 (``primary_staff_id``)。空ならコースの担当 (訪問モニターの行と
  同じ条件: 手動差し替えでない・コースが未削除)。取消・削除は除く。予定外の訪問は含む。
* 出勤日数 = 訪問が 1 件以上あった日数。1 日あたり = 件数 ÷ 出勤日数。
* 実績 = ``services/checkin/actuals.py`` (調整後) の到着〜退出 (``stay_minutes``)。
  到着・退出が揃った訪問が ``MIN_ACTUAL_SAMPLES`` 件以上あるときだけ平均を出す。
* 移動距離 = 所属拠点 → 1 件目 → … → 最後 → 所属拠点 を直線でつないだ合計 (日ごと)。
  座標の無い区間は飛ばし、その数を ``skipped_legs`` で返す。
* 1 日の内訳 = 訪問 (予定の合計) / 移動 (距離 ÷ ``scheduling_settings`` の移動速度) /
  会議・研修など (休み以外のイベントのうち、最初の訪問開始〜最後の訪問終了に重なる分) /
  合間 (訪問と訪問の間 − 訪問間の移動 − 会議・研修など。0 未満は 0)。
* チーム平均 = 表示中の拠点・期間で訪問のあった人の平均。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.schemas.dashboard import (
    PerformanceMetrics,
    PerformanceOffice,
    PerformanceWeek,
    StaffPerformanceResponse,
    StaffPerformanceRow,
    StaffPerformanceTeam,
)
from app.services.checkin.actuals import load_actuals, stay_minutes
from app.services.checkin.monitor import office_short, staff_code_sort_key
from app.services.scheduling.config import load_scheduling_config
from app.utils.geo import haversine_km

#: 実績の平均を出すのに要る「到着・退出が揃った訪問」の最小件数 (§3)。
MIN_ACTUAL_SAMPLES = 5
#: 1 回に集計できる最長の期間 (日)。
MAX_PERIOD_DAYS = 93
#: 業務ロールのうち「管理者」の札を付けるもの (``staff.role``・スケジュールエンジン用の軸)。
_MANAGER_ROLES = frozenset({"manager", "admin"})
#: 資格のうち「准看護師」の札を付けるもの。
_QUALIFICATION_ASSISTANT_NURSE = "准看護師"

LatLng = tuple[float, float]


def is_leave_event(title: str | None) -> bool:
    """休み (休み・有休・公休・午前休 など) のイベントか。

    ``staff_events`` に休みの種別は無く、カイポケ取り込みの件名 (「休み」「有休」…) で
    表れるため件名で判定する。「休憩」は休みに数えない (会議・研修などの側に入る)。
    """
    return "休" in (title or "").replace("休憩", "")


def _minute_of_day(t: time) -> int:
    return t.hour * 60 + t.minute


def _union_minutes(intervals: Iterable[tuple[int, int]], lo: int, hi: int) -> int:
    """区間の和集合のうち [lo, hi) に入る分数 (重なりは 1 回だけ数える)。"""
    clipped = sorted((max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo))
    total = 0
    cur_s: int | None = None
    cur_e = 0
    for s, e in clipped:
        if cur_s is None or s > cur_e:
            if cur_s is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_s is not None:
        total += cur_e - cur_s
    return total


@dataclass
class DayVisit:
    """集計に使う 1 訪問ぶんの材料。"""

    start: int  # 開始 (その日の分)
    end: int
    patient_id: UUID
    point: LatLng | None
    #: 到着・退出が揃っているときの滞在分 (実績)。
    actual_min: int | None = None
    arrival_only: bool = False


@dataclass
class DaySummary:
    """1 人 × 1 日の集計。"""

    day: date
    visits: int
    patients: set[UUID]
    plan_min: int
    actual_sum: int
    actual_n: int
    arrival_only: int
    km: float
    skipped_legs: int
    travel_min: float
    meeting_min: int
    idle_min: float


def summarize_day(
    day: date,
    visits: list[DayVisit],
    office_point: LatLng | None,
    events: list[tuple[int, int]],
    speed_kmh: float,
) -> DaySummary:
    """1 人 × 1 日の訪問から、件数・距離・1 日の内訳を出す (``visits`` は 1 件以上)。"""
    vs = sorted(visits, key=lambda v: (v.start, v.end))
    # 距離: 拠点 → 1 件目 → … → 最後 → 拠点。座標の無い端点を含む区間は飛ばす。
    points: list[LatLng | None] = [office_point, *(v.point for v in vs), office_point]
    km = 0.0
    between_km = 0.0
    skipped = 0
    last_leg = len(points) - 2
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        if a is None or b is None:
            skipped += 1
            continue
        d = haversine_km(a[0], a[1], b[0], b[1])
        km += d
        if 0 < i < last_leg:
            between_km += d  # 訪問と訪問の間の区間 (拠点との行き帰りは合間に入らない)
    # 訪問と訪問の間 (重なる訪問は重ねて 1 本の帯として扱う)。
    gap = 0
    run_end = vs[0].end
    for v in vs[1:]:
        if v.start > run_end:
            gap += v.start - run_end
        run_end = max(run_end, v.end)
    first_start = vs[0].start
    meeting = _union_minutes(events, first_start, run_end)
    idle = max(0.0, gap - between_km / speed_kmh * 60 - meeting)
    actuals = [v.actual_min for v in vs if v.actual_min is not None]
    return DaySummary(
        day=day,
        visits=len(vs),
        patients={v.patient_id for v in vs},
        plan_min=sum(max(0, v.end - v.start) for v in vs),
        actual_sum=sum(actuals),
        actual_n=len(actuals),
        arrival_only=sum(1 for v in vs if v.arrival_only),
        km=km,
        skipped_legs=skipped,
        travel_min=km / speed_kmh * 60,
        meeting_min=meeting,
        idle_min=idle,
    )


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def metrics_of(days: list[DaySummary]) -> PerformanceMetrics:
    """日ごとの集計を、1 人の期間 (全体 or 1 週) の数字にまとめる。"""
    n = len(days)
    visits = sum(d.visits for d in days)
    actual_n = sum(d.actual_n for d in days)
    actual_sum = sum(d.actual_sum for d in days)
    km = sum(d.km for d in days)
    patients: set[UUID] = set()
    for d in days:
        patients |= d.patients
    return PerformanceMetrics(
        days=n,
        visits=visits,
        patients=len(patients),
        per_day=_r(_div(visits, n), 2),
        plan_min=_r(_div(sum(d.plan_min for d in days), visits)),
        actual_min=_r(actual_sum / actual_n) if actual_n >= MIN_ACTUAL_SAMPLES else None,
        actual_samples=actual_n,
        arrival_only=sum(d.arrival_only for d in days),
        qr_ratio=_r(_div(actual_n, visits), 3),
        km_total=round(km, 2),
        km_per_day=_r(_div(km, n), 2),
        km_per_visit=_r(_div(km, visits), 2),
        skipped_legs=sum(d.skipped_legs for d in days),
        visit_min_per_day=_r(_div(sum(d.plan_min for d in days), n)),
        travel_min_per_day=_r(_div(sum(d.travel_min for d in days), n)),
        meeting_min_per_day=_r(_div(sum(d.meeting_min for d in days), n)),
        idle_min_per_day=_r(_div(sum(d.idle_min for d in days), n)),
        meeting_min_total=sum(d.meeting_min for d in days),
    )


_MEAN_FIELDS = (
    ("per_day", 2),
    ("plan_min", 1),
    ("km_per_day", 2),
    ("km_per_visit", 2),
    ("visit_min_per_day", 1),
    ("travel_min_per_day", 1),
    ("meeting_min_per_day", 1),
    ("idle_min_per_day", 1),
)


def team_metrics_of(people: list[PerformanceMetrics], days: list[DaySummary]) -> PerformanceMetrics:
    """チーム平均 = 訪問のあった人の平均 (§3)。

    件数・日数などの数は合計。実績の平均だけは、1 人ずつの平均 (5 件未満は出ない) を
    並べると記録の多い人に偏らないため、全員の揃った訪問を合わせて 5 件以上のとき出す。
    """
    active = [m for m in people if m.visits > 0]
    base = metrics_of(days)
    values: dict[str, float | None] = {}
    for name, digits in _MEAN_FIELDS:
        xs = [getattr(m, name) for m in active if getattr(m, name) is not None]
        values[name] = round(sum(xs) / len(xs), digits) if xs else None
    return base.model_copy(update={**values, "staff_count": len(active)})


def week_ranges(date_from: date, date_to: date) -> list[tuple[date, date]]:
    """期間を週 (月〜日) に分ける。両端の週は期間に合わせて切る。"""
    weeks: list[tuple[date, date]] = []
    monday = date_from - timedelta(days=date_from.weekday())
    while monday <= date_to:
        weeks.append((max(monday, date_from), min(monday + timedelta(days=6), date_to)))
        monday += timedelta(days=7)
    return weeks


def _point(lat: object, lng: object) -> LatLng | None:
    if lat is None or lng is None:
        return None
    return (float(lat), float(lng))  # type: ignore[arg-type]


def _wall_clock(value: datetime) -> datetime:
    """イベントの時刻は壁時計 (JST) のまま保存されている (``api/v1/staff_events._combine``)。"""
    return value.replace(tzinfo=None)


async def build_staff_performance(
    db: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    office_id: UUID | None = None,
) -> StaffPerformanceResponse:
    """期間 [date_from, date_to] のスタッフ別の実績を組み立てる (DB 読み取りのみ)。"""
    speed = (await load_scheduling_config(db)).travel_speed_kmh

    visit_rows = (
        await db.execute(
            select(
                Visit.id,
                Visit.visit_date,
                Visit.start_time,
                Visit.end_time,
                Visit.patient_id,
                Visit.primary_staff_id,
                Visit.course_id,
                Visit.manual_staff_override,
                Patient.lat,
                Patient.lng,
            )
            .join(Patient, Patient.id == Visit.patient_id)
            .where(
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
                Visit.visit_date >= date_from,
                Visit.visit_date <= date_to,
            )
        )
    ).all()

    # コースの担当 (主担当が空の訪問の持ち主)。
    course_ids = {r.course_id for r in visit_rows if r.course_id is not None}
    course_owner: dict[UUID, UUID] = {}
    if course_ids:
        for cid, assigned, deleted_at in (
            await db.execute(
                select(Course.id, Course.assigned_staff_id, Course.deleted_at).where(
                    Course.id.in_(course_ids)
                )
            )
        ).all():
            if assigned is not None and deleted_at is None:
                course_owner[cid] = assigned

    def owner_of(r) -> UUID | None:
        if r.primary_staff_id is not None:
            return r.primary_staff_id
        if r.manual_staff_override or r.course_id is None:
            return None
        return course_owner.get(r.course_id)

    owned = [(owner_of(r), r) for r in visit_rows]
    owned = [(sid, r) for sid, r in owned if sid is not None]

    staff_by_id: dict[UUID, Staff] = {}
    owner_ids = {sid for sid, _ in owned}
    if owner_ids:
        staff_by_id = {
            s.id: s for s in (await db.scalars(select(Staff).where(Staff.id.in_(owner_ids)))).all()
        }
    if office_id is not None:
        staff_by_id = {k: s for k, s in staff_by_id.items() if s.primary_office_id == office_id}
    owned = [(sid, r) for sid, r in owned if sid in staff_by_id]

    offices = (await db.scalars(select(Office))).all()
    office_by_id = {o.id: o for o in offices}

    actuals = await load_actuals(db, [r.id for _, r in owned])

    # 休み以外のイベント (取消を除く): (職員, 日) → [(開始分, 終了分)]。日を跨ぐものは日ごとに切る。
    events_by_day: dict[tuple[UUID, date], list[tuple[int, int]]] = defaultdict(list)
    if staff_by_id:
        range_lo = datetime.combine(date_from, time.min)
        range_hi = datetime.combine(date_to + timedelta(days=1), time.min)
        for sid, starts_at, ends_at, title in (
            await db.execute(
                select(
                    StaffEvent.staff_id, StaffEvent.starts_at, StaffEvent.ends_at, StaffEvent.title
                ).where(
                    StaffEvent.staff_id.in_(list(staff_by_id)),
                    StaffEvent.cancelled_at.is_(None),
                    StaffEvent.starts_at < range_hi,
                    StaffEvent.ends_at > range_lo,
                )
            )
        ).all():
            if is_leave_event(title):
                continue
            s, e = _wall_clock(starts_at), _wall_clock(ends_at)
            d = s.date()
            while d <= e.date():
                day_lo = datetime.combine(d, time.min)
                lo = max(s, day_lo)
                hi = min(e, day_lo + timedelta(days=1))
                if hi > lo:
                    events_by_day[(sid, d)].append(
                        (
                            int((lo - day_lo).total_seconds() // 60),
                            int((hi - day_lo).total_seconds() // 60),
                        )
                    )
                d += timedelta(days=1)

    # 職員 × 日 → 訪問。
    by_staff_day: dict[UUID, dict[date, list[DayVisit]]] = defaultdict(lambda: defaultdict(list))
    for sid, r in owned:
        a = actuals.get(r.id)
        actual_min: int | None = None
        arrival_only = False
        if a is not None and a.arrival is not None:
            if a.departure is not None:
                actual_min = stay_minutes(a.arrival.at, a.departure.at)
            else:
                arrival_only = True
        by_staff_day[sid][r.visit_date].append(
            DayVisit(
                start=_minute_of_day(r.start_time),
                end=_minute_of_day(r.end_time),
                patient_id=r.patient_id,
                point=_point(r.lat, r.lng),
                actual_min=actual_min,
                arrival_only=arrival_only,
            )
        )

    weeks = week_ranges(date_from, date_to)
    all_days: list[DaySummary] = []
    rows: list[tuple[tuple, StaffPerformanceRow, list[DaySummary]]] = []
    for sid, days_map in by_staff_day.items():
        staff = staff_by_id[sid]
        office = office_by_id.get(staff.primary_office_id) if staff.primary_office_id else None
        office_point = _point(office.lat, office.lng) if office is not None else None
        days = [
            summarize_day(d, vs, office_point, events_by_day.get((sid, d), []), speed)
            for d, vs in sorted(days_map.items())
        ]
        all_days.extend(days)
        rows.append(
            (
                (
                    office is None,
                    office.sort_order
                    if office is not None and office.sort_order is not None
                    else 0,
                    office.name if office is not None else "",
                    staff_code_sort_key(staff.code, staff.name),
                ),
                StaffPerformanceRow(
                    staff_id=sid,
                    name=staff.name,
                    office_id=staff.primary_office_id,
                    office_short=office_short(office.short_label, office.name) if office else None,
                    is_manager=staff.role in _MANAGER_ROLES,
                    is_trainee=bool(staff.is_trainee),
                    qualification=staff.qualification,
                    period=metrics_of(days),
                    weeks=[metrics_of([d for d in days if ws <= d.day <= we]) for ws, we in weeks],
                ),
                days,
            )
        )
    rows.sort(key=lambda x: x[0])
    staff_rows = [row for _, row, _ in rows]

    team = StaffPerformanceTeam(
        period=team_metrics_of([row.period for row in staff_rows], all_days),
        weeks=[
            team_metrics_of(
                [row.weeks[i] for row in staff_rows],
                [d for d in all_days if ws <= d.day <= we],
            )
            for i, (ws, we) in enumerate(weeks)
        ],
    )

    return StaffPerformanceResponse(
        date_from=date_from,
        date_to=date_to,
        office_id=office_id,
        travel_speed_kmh=speed,
        min_actual_samples=MIN_ACTUAL_SAMPLES,
        weeks=[PerformanceWeek(start=ws, end=we) for ws, we in weeks],
        offices=[
            PerformanceOffice(id=o.id, name=o.name, short_label=office_short(o.short_label, o.name))
            for o in sorted(
                (o for o in offices if o.deleted_at is None),
                key=lambda o: (o.sort_order is None, o.sort_order or 0, o.name),
            )
        ],
        team=team,
        staff=staff_rows,
    )


__all__ = [
    "MAX_PERIOD_DAYS",
    "MIN_ACTUAL_SAMPLES",
    "DayVisit",
    "DaySummary",
    "build_staff_performance",
    "is_leave_event",
    "metrics_of",
    "summarize_day",
    "team_metrics_of",
    "week_ranges",
]
