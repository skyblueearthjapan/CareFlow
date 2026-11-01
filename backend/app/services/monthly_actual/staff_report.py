"""月の実績の看護師別 (訪問記録の「月の実績」タブの「一覧を出す」・管理者のみ).

数え方はダッシュボードのスタッフ別の実績 (``services/dashboard_staff_performance.py``) と
同じ入口 ``load_staff_days`` を使う。月の範囲 (1 日〜末日) を渡すだけで、ここでは
月の実績に要る数を足すだけにする。

* 訪問の持ち主 = 主担当 (空ならコース担当)。取消・削除は除く。予定外の訪問は含む。
* 2 名訪問・同行 = 副担当・メンター・同行リンクで入った件数 (持ち主本人は除く)。
  件数・稼働日数・患者数・距離には入れない (ダッシュボードと同じ)。
* 稼働日数 = 訪問のあった日数。1 日あたり = 件数 ÷ 稼働日数。
* 週の平均患者数 = 月の中の週 (月曜はじまり・その月に入る日だけ) ごとに担当した
  患者様の異なる人数を、訪問のあった週で平均した値。
* 提供時間 = らく助の実績 (``load_actuals``・後から合わせた時刻を含む) の到着〜退出。
  30 分未満と退出の QR が無い訪問は、決まりどおり到着＋35 分にした後の値。到着の
  QR が無い訪問は提供時間に入れない。
* 移動距離 = 所属拠点 → 1 件目 → … → 最後 → 所属拠点 の直線の合計 (日ごと)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.services.checkin.actuals import JST, as_utc, stay_minutes
from app.services.dashboard_staff_performance import (
    DayVisit,
    load_staff_days,
    metrics_of,
    week_ranges,
    with_accompany,
)
from app.services.monthly_actual import judge as rules
from app.services.office_labels import office_short

#: 到着＋35 分にした理由。
RULE_SHORT_STAY = "short_stay"
RULE_NO_DEPARTURE = "no_departure"


def month_range(month: str) -> tuple[date, date]:
    """``YYYY-MM`` → (1 日, 末日)。"""
    year, mon = (int(x) for x in month.split("-"))
    first = date(year, mon, 1)
    nxt = date(year + (mon == 12), mon % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def provided_minutes(
    arrival_at: datetime | None, departure_at: datetime | None
) -> tuple[int | None, str | None]:
    """提供時間 (分) と、到着＋35 分にした理由。

    到着が無ければ (None, None)。退出が無い・滞在が 30 分未満なら到着＋35 分
    (``judge._new_times`` と同じ決まり)。
    """
    if arrival_at is None:
        return None, None
    if departure_at is None:
        return rules.SHORT_STAY_END_MINUTES, RULE_NO_DEPARTURE
    stay = stay_minutes(arrival_at, departure_at) or 0
    if stay < rules.SHORT_STAY_MINUTES:
        return rules.SHORT_STAY_END_MINUTES, RULE_SHORT_STAY
    return stay, None


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _hm(value: datetime | None) -> str | None:
    return as_utc(value).astimezone(JST).strftime("%H:%M") if value is not None else None


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass
class VisitLine:
    """全件の一覧の 1 行 (1 訪問)。"""

    visit_id: UUID | None
    day: date
    staff_id: UUID
    staff_name: str
    patient: str
    plan: str
    arrival: str | None
    departure: str | None
    #: らく助の実績の滞在 (到着・退出が揃ったときだけ)。
    stay_min: int | None
    #: 提供時間 (到着＋35 分の決まりを当てはめた後)。
    provided_min: int | None
    rule: str | None
    no_show: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "visit_id": str(self.visit_id) if self.visit_id else None,
            "date": self.day.isoformat(),
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "patient": self.patient,
            "plan": self.plan,
            "arrival": self.arrival,
            "departure": self.departure,
            "stay_min": self.stay_min,
            "provided_min": self.provided_min,
            "rule": self.rule,
            "no_show": self.no_show,
        }


@dataclass
class StaffWeek:
    """1 人 × 1 週。"""

    start: date
    end: date
    days: int
    visits: int
    patients: int
    provided_min: int
    km: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "visits": self.visits,
            "patients": self.patients,
            "provided_min": self.provided_min,
            "km": self.km,
        }


@dataclass
class StaffMonth:
    """1 人の月の実績。"""

    staff_id: UUID
    name: str
    office_short: str | None
    visits: int
    days: int
    per_day: float | None
    patients: int
    weekly_patients_avg: float | None
    accompany_visits: int
    provided_min_total: int
    provided_samples: int
    avg_stay_min: float | None
    short_stay_count: int
    no_departure_count: int
    no_arrival_count: int
    km_total: float
    km_per_day: float | None
    km_per_visit: float | None
    skipped_legs: int
    weeks: list[StaffWeek] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "staff_id": str(self.staff_id),
            "name": self.name,
            "office_short": self.office_short,
            "visits": self.visits,
            "days": self.days,
            "per_day": self.per_day,
            "patients": self.patients,
            "weekly_patients_avg": self.weekly_patients_avg,
            "accompany_visits": self.accompany_visits,
            "provided_min_total": self.provided_min_total,
            "provided_samples": self.provided_samples,
            "avg_stay_min": self.avg_stay_min,
            "short_stay_count": self.short_stay_count,
            "no_departure_count": self.no_departure_count,
            "no_arrival_count": self.no_arrival_count,
            "km_total": self.km_total,
            "km_per_day": self.km_per_day,
            "km_per_visit": self.km_per_visit,
            "skipped_legs": self.skipped_legs,
            "weeks": [w.as_dict() for w in self.weeks],
        }


@dataclass
class MonthStaffReport:
    """月の看護師別の実績 (JSON・A4・Excel が共用)。"""

    month: str
    date_from: date
    date_to: date
    weeks: list[tuple[date, date]]
    staff: list[StaffMonth]
    visits: list[VisitLine]

    def totals(self) -> dict[str, Any]:
        return {
            "staff_count": sum(1 for s in self.staff if s.visits > 0),
            "visits": sum(s.visits for s in self.staff),
            "accompany_visits": sum(s.accompany_visits for s in self.staff),
            "provided_min_total": sum(s.provided_min_total for s in self.staff),
            "short_stay_count": sum(s.short_stay_count for s in self.staff),
            "no_departure_count": sum(s.no_departure_count for s in self.staff),
            "no_arrival_count": sum(s.no_arrival_count for s in self.staff),
            "km_total": round(sum(s.km_total for s in self.staff), 2),
            "skipped_legs": sum(s.skipped_legs for s in self.staff),
        }

    def as_summary(self) -> dict[str, Any]:
        """``GET /monthly-actual/{month}/staff-summary`` の本文 (全件の一覧は含めない)。"""
        return {
            "month": self.month,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "short_stay_minutes": rules.SHORT_STAY_MINUTES,
            "short_stay_end_minutes": rules.SHORT_STAY_END_MINUTES,
            "weeks": [{"start": ws.isoformat(), "end": we.isoformat()} for ws, we in self.weeks],
            "totals": self.totals(),
            "staff": [s.as_dict() for s in self.staff],
        }


def _line(v: DayVisit, day: date, sid: UUID, name: str, patients: dict[UUID, str]) -> VisitLine:
    provided, rule = provided_minutes(v.arrival_at, v.departure_at)
    stay = (
        stay_minutes(v.arrival_at, v.departure_at)
        if v.arrival_at is not None and v.departure_at is not None
        else None
    )
    return VisitLine(
        visit_id=v.visit_id,
        day=day,
        staff_id=sid,
        staff_name=name,
        patient=patients.get(v.patient_id, ""),
        plan=f"{_clock(v.start)}–{_clock(v.end)}",
        arrival=_hm(v.arrival_at),
        departure=_hm(v.departure_at),
        stay_min=stay,
        provided_min=provided,
        rule=rule,
        no_show=v.no_show,
    )


async def build_month_staff_report(db: AsyncSession, month: str) -> MonthStaffReport:
    """月の看護師別の実績を組み立てる (DB 読み取りのみ)。"""
    date_from, date_to = month_range(month)
    data = await load_staff_days(db, date_from, date_to)
    weeks = week_ranges(date_from, date_to)

    patient_ids = {
        v.patient_id for days in data.by_staff_day.values() for vs in days.values() for v in vs
    }
    patients: dict[UUID, str] = {}
    if patient_ids:
        patients = {
            pid: name
            for pid, name in (
                await db.execute(
                    select(Patient.id, Patient.name).where(Patient.id.in_(patient_ids))
                )
            ).all()
        }

    staff_rows: list[StaffMonth] = []
    lines: list[VisitLine] = []
    for sid in sorted(data.staff_ids(), key=data.sort_key):
        staff = data.staff_by_id[sid]
        office = data.office_of(sid)
        days = data.days_of(sid)
        period = with_accompany(metrics_of(days), data.accompany.get(sid, {}), date_from, date_to)
        own = [
            _line(v, d, sid, staff.name, patients)
            for d, vs in sorted(data.by_staff_day.get(sid, {}).items())
            for v in sorted(vs, key=lambda x: (x.start, x.end))
        ]
        lines.extend(own)
        provided = [ln.provided_min for ln in own if ln.provided_min is not None]

        staff_weeks: list[StaffWeek] = []
        for ws, we in weeks:
            m = metrics_of([d for d in days if ws <= d.day <= we])
            staff_weeks.append(
                StaffWeek(
                    start=ws,
                    end=we,
                    days=m.days,
                    visits=m.visits,
                    patients=m.patients,
                    provided_min=sum(ln.provided_min or 0 for ln in own if ws <= ln.day <= we),
                    km=m.km_total,
                )
            )
        active_weeks = [w.patients for w in staff_weeks if w.visits > 0]

        staff_rows.append(
            StaffMonth(
                staff_id=sid,
                name=staff.name,
                office_short=office_short(office.short_label, office.name) if office else None,
                visits=period.visits,
                days=period.days,
                per_day=period.per_day,
                patients=period.patients,
                weekly_patients_avg=(
                    _r(sum(active_weeks) / len(active_weeks)) if active_weeks else None
                ),
                accompany_visits=period.accompany_visits,
                provided_min_total=sum(provided),
                provided_samples=len(provided),
                avg_stay_min=_r(sum(provided) / len(provided)) if provided else None,
                short_stay_count=sum(1 for ln in own if ln.rule == RULE_SHORT_STAY),
                no_departure_count=sum(1 for ln in own if ln.rule == RULE_NO_DEPARTURE),
                no_arrival_count=sum(1 for ln in own if ln.arrival is None),
                km_total=period.km_total,
                km_per_day=period.km_per_day,
                km_per_visit=period.km_per_visit,
                skipped_legs=period.skipped_legs,
                weeks=staff_weeks,
            )
        )

    lines.sort(key=lambda ln: (ln.day, ln.plan, ln.staff_name))
    return MonthStaffReport(
        month=month,
        date_from=date_from,
        date_to=date_to,
        weeks=weeks,
        staff=staff_rows,
        visits=lines,
    )


__all__ = [
    "RULE_NO_DEPARTURE",
    "RULE_SHORT_STAY",
    "MonthStaffReport",
    "StaffMonth",
    "StaffWeek",
    "VisitLine",
    "build_month_staff_report",
    "month_range",
    "provided_minutes",
]
