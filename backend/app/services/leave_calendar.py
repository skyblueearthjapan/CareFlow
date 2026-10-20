"""必要休日数・休日数の数え方と、月の休みの読み出し (設計 §2-2).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2-2。

* **必要休日数** (その月) = 土曜・日曜 ＋ 月〜金の祝日 (振替休日・国民の休日を含む)
  ＋ 月〜金の事業所の休み (祝日でない日)。土曜の祝日は土曜として 1 日、日曜の祝日は
  日曜として 1 日 (振替休日が平日に出るので、そちらも数える)。= 「土日・祝日・事業所の
  休み」の日の集合の大きさ。祝日・事業所の休みはそれだけでは誰も休みにならない。
* **休日数** (職員・その月) = Σ 日ごとに: 公休 1・半休 0.5 (型で勤務しない曜日なら 1)・
  有給 0 (有給の日数として別に数える)・登録なしで型で勤務しない曜日 1。
* 月の休みは ISO 週ではなく **その月の日付の範囲から (iso_year, iso_week) の組を作って**
  読む (1 月 1 日が前の年の ISO 週に入る年末年始を落とさない・L2)。

純粋な関数 (``required_holiday_dates``・``count_holidays`` など) は勤務表 (#11) からも使う。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.leave import FULL_DAY_OFF, HALF_DAY_OFF
from app.models.leave import OfficeClosedDay
from app.models.staff import StaffShift, StaffWeeklyOverride
from app.services.scheduling.jp_holidays import holidays_between

#: 有給の値 (``FULL_DAY_OFF`` のうち休日数に数えないもの)。
_PAID = "paid_off"


@dataclass(frozen=True)
class HolidayCount:
    """職員 1 人のその月の休日数と有給の日数."""

    holidays: float
    paid_days: int


def iso_key(day: date) -> tuple[int, int, int]:
    """日付 → ``staff_weekly_overrides`` の (iso_year, iso_week, weekday 0=月)."""
    iso = day.isocalendar()
    return iso.year, iso.week, day.weekday()


def date_of(iso_year: int, iso_week: int, weekday: int) -> date:
    """``iso_key`` の逆 (weekday 0=月)."""
    return date.fromisocalendar(iso_year, iso_week, weekday + 1)


def month_days(month: date) -> list[date]:
    """その月の日付 (1 日〜末日)。``month`` は月の中のどの日でもよい."""
    first = month.replace(day=1)
    last = calendar.monthrange(first.year, first.month)[1]
    return [first + timedelta(days=i) for i in range(last)]


def month_holidays(month: date) -> dict[date, str]:
    """その月の祝日 (振替休日・国民の休日を含む) を {日付: 名前} で返す."""
    days = month_days(month)
    return dict(holidays_between(days[0], days[-1]))


def required_holiday_dates(month: date, closed_days: Iterable[date] = ()) -> list[date]:
    """必要休日数に数える日 (土日・祝日・事業所の休み) を日付順に返す."""
    days = month_days(month)
    holidays = month_holidays(month)
    closed = set(closed_days)
    return [d for d in days if d.weekday() >= 5 or d in holidays or d in closed]


def required_holidays(month: date, closed_days: Iterable[date] = ()) -> int:
    """その月の必要休日数 (常勤)."""
    return len(required_holiday_dates(month, closed_days))


def day_holiday_value(override_type: str | None, is_working_day: bool) -> tuple[float, int]:
    """1 日ぶんの (休日数, 有給の日数)。

    ``override_type`` はその日の休みの登録 (無ければ None・時間変更は休みではない)、
    ``is_working_day`` は型 (``staff_shifts.is_on``) でその曜日に勤務するか。
    """
    if override_type == _PAID:
        return (0.0, 1)
    if override_type in FULL_DAY_OFF:
        return (1.0, 0)
    if override_type in HALF_DAY_OFF:
        return (0.5 if is_working_day else 1.0, 0)
    if override_type is None and not is_working_day:
        return (1.0, 0)
    return (0.0, 0)


def count_holidays(
    days: Iterable[date],
    working_weekdays: Iterable[int],
    overrides: Mapping[date, str],
) -> HolidayCount:
    """日付の並び・型で勤務する曜日 (0=月)・日ごとの登録 → 休日数と有給の日数."""
    working = set(working_weekdays)
    total = 0.0
    paid = 0
    for d in days:
        h, p = day_holiday_value(overrides.get(d), d.weekday() in working)
        total += h
        paid += p
    return HolidayCount(holidays=total, paid_days=paid)


# ---------------------------------------------------------------------------
# DB から読む
# ---------------------------------------------------------------------------


async def load_closed_days(db: AsyncSession, month: date) -> dict[date, str]:
    """その月の事業所の休み {日付: 名前}."""
    days = month_days(month)
    rows = (
        await db.scalars(
            select(OfficeClosedDay)
            .where(OfficeClosedDay.date >= days[0], OfficeClosedDay.date <= days[-1])
            .order_by(OfficeClosedDay.date)
        )
    ).all()
    return {r.date: r.label for r in rows}


async def required_holidays_for_month(db: AsyncSession, month: date) -> int:
    """DB の事業所の休みを入れたその月の必要休日数."""
    return required_holidays(month, await load_closed_days(db, month))


def _iso_week_filter(days: list[date]) -> ColumnElement[bool]:
    """日付の範囲にかかる (iso_year, iso_week) の組の OR 条件."""
    pairs = sorted({(d.isocalendar().year, d.isocalendar().week) for d in days})
    return or_(
        *(
            and_(StaffWeeklyOverride.iso_year == y, StaffWeeklyOverride.iso_week == w)
            for y, w in pairs
        )
    )


async def load_overrides_between(
    db: AsyncSession,
    date_from: date,
    date_to: date,
    staff_ids: Iterable[UUID] | None = None,
) -> dict[UUID, dict[date, StaffWeeklyOverride]]:
    """``date_from``〜``date_to`` (両端含む) の override を {職員: {日付: 行}} で返す.

    時間変更 (``custom_time``) も含む。休みだけが欲しいときは呼び出し側で
    ``LEAVE_TYPES`` に絞る。
    """
    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    if not days:
        return {}
    stmt = select(StaffWeeklyOverride).where(_iso_week_filter(days))
    if staff_ids is not None:
        ids = list(staff_ids)
        if not ids:
            return {}
        stmt = stmt.where(StaffWeeklyOverride.staff_id.in_(ids))
    out: dict[UUID, dict[date, StaffWeeklyOverride]] = {}
    for row in (await db.scalars(stmt)).all():
        d = date_of(row.iso_year, row.iso_week, row.weekday)
        if date_from <= d <= date_to:
            out.setdefault(row.staff_id, {})[d] = row
    return out


async def load_working_weekdays(
    db: AsyncSession, staff_ids: Iterable[UUID]
) -> dict[UUID, set[int]]:
    """職員ごとの型で勤務する曜日 (``staff_shifts.is_on``・行が無い曜日は勤務しない)."""
    ids = list(staff_ids)
    out: dict[UUID, set[int]] = {sid: set() for sid in ids}
    if not ids:
        return out
    rows = (await db.scalars(select(StaffShift).where(StaffShift.staff_id.in_(ids)))).all()
    for sh in rows:
        if sh.is_on:
            out.setdefault(sh.staff_id, set()).add(sh.weekday)
    return out


async def staff_holiday_counts(
    db: AsyncSession, month: date, staff_ids: Iterable[UUID]
) -> dict[UUID, HolidayCount]:
    """職員ごとのその月の休日数と有給の日数 (§2-2)."""
    ids = list(staff_ids)
    days = month_days(month)
    working = await load_working_weekdays(db, ids)
    overrides = await load_overrides_between(db, days[0], days[-1], ids)
    return {
        sid: count_holidays(
            days,
            working.get(sid, set()),
            {d: row.override_type for d, row in overrides.get(sid, {}).items()},
        )
        for sid in ids
    }


__all__ = [
    "HolidayCount",
    "count_holidays",
    "date_of",
    "day_holiday_value",
    "iso_key",
    "load_closed_days",
    "load_overrides_between",
    "load_working_weekdays",
    "month_days",
    "month_holidays",
    "required_holiday_dates",
    "required_holidays",
    "required_holidays_for_month",
    "staff_holiday_counts",
]
