"""勤務表 (従業者の勤務の体制及び勤務形態一覧表) の値を作る純粋な関数 (設計 §7-1).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-1。

* 日ごとのセル: 出勤・時間変更 = 時刻の長さ − 休憩 (6 時間を超える日だけ 60 分・仮決め)、
  午前休 = 13:00〜終業・午後休 = 始業〜12:30 (休憩なし)、公休・有給・型で勤務しない曜日 = 「休」。
  祝日・事業所の休みでも休みの登録が無ければ出勤 (時間を書く)。働ける時間が残らない半休
  (勤務が 13:00 までの人の午前休など) は終日の休みとして扱う。
* 管理者 (``admin_hours_per_day`` > 0) は「予定」「実績」= 勤務時間 − 管理時間。
  管理時間の行は勤務した日だけ。半休の日の管理時間は 0 (仮決め)。
* 休みの番号: 終日の休み (公休・型で勤務しない曜日) を週 (日曜〜土曜・前の月の日を含む) の中で
  日付の順に 1・2、3 つ目からは「祝n」(n はその月の中の通し番号)。半休は「0,5」、有給は「有給」
  (どちらも番号を取らない)。
* 合計 = その行のセルの合計 (シートでは ``=SUM`` の式・ここの値は試験の比べる相手)。
  常勤換算 = 合計 ÷ 勤務すべき時間数を小数第 2 位で切り捨て (小数 1 桁・シートでは ``ROUNDDOWN``)。
* 実績の行は「照合を済ませた休み」(``actual_overrides``) で予定と同じ計算をし直したもの。
  番号の行と休日数は、実績があれば実績の休みで、無ければ予定の休みで作る。
* 休職・非常勤の扱いは ``KinmuRules`` で切り替える (ディレクター決定・お客様の確認待ち)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final, Literal

from app.core.leave import (
    DEFAULT_BUSINESS_END,
    DEFAULT_BUSINESS_START,
    FULL_DAY_OFF,
    HALF_DAY_OFF,
    leave_window,
)
from app.services.leave_calendar import HolidayCount, count_holidays, month_days, month_holidays

#: 休憩を引く勤務の長さ (これを超える日だけ)・引く分 (仮決め・設計 §10-2)。
BREAK_AFTER_MINUTES: Final = 6 * 60
BREAK_MINUTES: Final = 60

#: セルの文字。
OFF_CELL: Final = "休"
HALF_MARK: Final = "0,5"
PAID_MARK: Final = "有給"
EXTRA_OFF_PREFIX: Final = "祝"
#: 番号の行・集計に書く「数えない」の印。
NOT_APPLICABLE: Final = "対象外"
ON_LEAVE_NOTE: Final = "休職"

_WEEKDAY_LABELS: Final = "月火水木金土日"
_SNAPSHOT_VERSION: Final = 1

#: 日ごとのセル (時間 = float・「休」= str)。
CellValue = float | str | None
_DayKind = Literal["work", "half", "off", "paid"]
Shifts = Mapping[int, tuple[time, time]]


@dataclass(frozen=True)
class KinmuRules:
    """お客様の確認待ちの扱いの切り替え (既定 = ディレクター決定 2026-10-21).

    * ``on_leave_all_off``: 休職の職員は全日「休」・時間を入れない・常勤換算に入れない。
    * ``part_time_marks``: 非常勤にも休みの番号と休日数を付ける (False = 「対象外」)。
    * ``include_retired_with_activity``: 退職者でも、その月に訪問か休みの登録があれば載せる
      (``load_kinmu_inputs`` が使う)。
    """

    on_leave_all_off: bool = True
    part_time_marks: bool = False
    include_retired_with_activity: bool = True


@dataclass(frozen=True)
class DayOverride:
    """その日の登録 (``staff_weekly_overrides`` の 1 行)。時刻は ``custom_time`` のときだけ使う."""

    type: str
    start: time | None = None
    end: time | None = None


@dataclass(frozen=True)
class KinmuStaffInput:
    """勤務表の職員 1 人の入力.

    * ``shifts``: 型で勤務する曜日 (0=月) → (始業, 終業)。無い曜日は勤務しない。
    * ``overrides``: 予定の休み・時間変更。番号を正しく付けるため、月の 1 日を含む週の日曜からの
      日を入れる (前の月の日はセルには出ない)。
    * ``actual_overrides``: 実績用の休み (照合を済ませたもの)。None なら実績の行は空 (予定版)。
    * ``actual_shifts``: 実績の計算に使う型 (None なら ``shifts``)。予定を写しから作るとき、
      実績はいまの型で計算する。
    * ``on_leave``: 休職中 (``staff.status='on_leave'``)。
    """

    key: str
    name: str
    job: str
    title: str | None
    work_form: str | None
    office_label: str
    full_time: bool
    admin_hours_per_day: float | None
    shifts: Shifts
    overrides: Mapping[date, DayOverride]
    actual_overrides: Mapping[date, DayOverride] | None = None
    actual_shifts: Shifts | None = None
    on_leave: bool = False

    @property
    def is_manager(self) -> bool:
        return self.admin_hours_per_day is not None and self.admin_hours_per_day > 0


@dataclass(frozen=True)
class DayHeader:
    """見出しの 1 日 (日付・曜日・祝日の名前・事業所の休みの名前)."""

    day: date
    weekday_label: str
    holiday: str | None
    closed: str | None


@dataclass(frozen=True)
class WeekBand:
    """週の帯 (第1週は 1 日から最初の土曜まで、第2週から日曜始まり)."""

    label: str
    first: date
    last: date


@dataclass(frozen=True)
class KinmuStaffRows:
    """職員 1 人の行 (予定・管理時間・実績・番号) と合計.

    ``marks`` が None のときは番号を付けない (``marks_note`` =「対象外」「休職」を書く)。
    ``plan_fte`` が None の人 (休職) は常勤換算に入れない。
    """

    staff: KinmuStaffInput
    plan: list[CellValue]
    admin: list[float | None] | None
    actual: list[CellValue] | None
    marks: list[str | None] | None
    marks_note: str | None
    plan_total: float
    admin_total: float | None
    actual_total: float | None
    plan_fte: float | None
    admin_fte: float | None
    #: 実績の常勤換算。シートには出さない (見本どおり)・参考の値。
    actual_fte: float | None
    holidays: HolidayCount | None


@dataclass(frozen=True)
class KinmuMonth:
    """1 か月の勤務表の値."""

    month: date
    required_hours: float
    days: list[DayHeader]
    weeks: list[WeekBand]
    staff: list[KinmuStaffRows]
    plan_day_totals: list[float]
    actual_day_totals: list[float] | None
    plan_total: float
    actual_total: float | None
    plan_fte: float
    #: 実績の合計の常勤換算。シートには出さない (見本どおり)・参考の値。
    actual_fte: float | None


@dataclass(frozen=True)
class HolidaySummary:
    """「集計」シート: その年の 1 月〜その月の、職員×月の休日取得数 (有給は数えない).

    行の値が文字 (「対象外」= 非常勤・「休職」) の職員は合計に入れない。
    """

    year: int
    upto: date
    months: list[date]
    rows: list[tuple[str, list[float | str | None], float | str]]
    month_totals: list[float | None]
    grand_total: float


# ---------------------------------------------------------------------------
# 日・週
# ---------------------------------------------------------------------------


def week_sunday(day: date) -> date:
    """その日を含む週 (日曜〜土曜) の日曜."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def week_bands(month: date) -> list[WeekBand]:
    """月の週の帯 (日曜〜土曜で区切る)."""
    bands: list[WeekBand] = []
    for d in month_days(month):
        if not bands or d.weekday() == 6:
            bands.append(WeekBand(f"第{len(bands) + 1}週", d, d))
        else:
            bands[-1] = WeekBand(bands[-1].label, bands[-1].first, d)
    return bands


# ---------------------------------------------------------------------------
# 1 日ぶん
# ---------------------------------------------------------------------------


def _minutes_between(start: time, end: time) -> int:
    return max(0, (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute))


def worked_minutes(start: time, end: time) -> int:
    """時刻の長さ − 休憩 (6 時間を超える日だけ 60 分)."""
    span = _minutes_between(start, end)
    return span - BREAK_MINUTES if span > BREAK_AFTER_MINUTES else span


def classify_day(shifts: Shifts, override: DayOverride | None, day: date) -> tuple[_DayKind, int]:
    """その日の (種類, 勤務の分)。種類は work・half (半休)・off (終日の休み)・paid (有給)."""
    otype = override.type if override is not None else None
    shift = shifts.get(day.weekday())
    if otype == "paid_off":
        return ("paid", 0)
    if otype in FULL_DAY_OFF:
        return ("off", 0)
    if otype in HALF_DAY_OFF:
        # 型で勤務しない曜日の半休・働ける時間が残らない半休は終日の休み。
        window = leave_window(otype, shift[0], shift[1]) if shift is not None else None
        if window is None:
            return ("off", 0)
        return ("half", _minutes_between(*window))
    if otype == "custom_time" and override is not None:
        if override.start is not None and override.end is not None:
            return ("work", worked_minutes(override.start, override.end))
        start, end = shift or (DEFAULT_BUSINESS_START, DEFAULT_BUSINESS_END)
        return ("work", worked_minutes(start, end))
    if shift is None:
        return ("off", 0)
    return ("work", worked_minutes(shift[0], shift[1]))


def _hours(minutes: int) -> float:
    return round(minutes / 60, 2)


def fte(total_hours: float, required_hours: float) -> float:
    """常勤換算 = 合計 ÷ 勤務すべき時間数 (小数第 2 位で切り捨て)."""
    value = Decimal(str(total_hours)) / Decimal(str(required_hours))
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_DOWN))


def _sum_cells(cells: Iterable[CellValue]) -> float:
    """セルの数だけを足す (シートの ``=SUM`` と同じ)."""
    return round(sum(v for v in cells if isinstance(v, float)), 2)


def _cells(
    staff: KinmuStaffInput,
    shifts: Shifts,
    overrides: Mapping[date, DayOverride],
    days: Sequence[date],
    rules: KinmuRules,
) -> tuple[list[CellValue], list[float | None]]:
    """(予定か実績のセル, 管理時間のセル)."""
    if staff.on_leave and rules.on_leave_all_off:
        return [OFF_CELL] * len(days), [None] * len(days)
    admin_per_day = round((staff.admin_hours_per_day or 0) * 60) if staff.is_manager else 0
    cells: list[CellValue] = []
    admin_cells: list[float | None] = []
    for d in days:
        kind, minutes = classify_day(shifts, overrides.get(d), d)
        if kind in ("off", "paid"):
            cells.append(OFF_CELL)
            admin_cells.append(None)
            continue
        admin = min(admin_per_day, minutes) if kind == "work" else 0
        cells.append(_hours(minutes - admin))
        admin_cells.append(_hours(admin))
    return cells, admin_cells


def holiday_marks(
    shifts: Shifts, overrides: Mapping[date, DayOverride], month: date
) -> list[str | None]:
    """休みの番号の行 (その月の日ごと)."""
    days = month_days(month)
    first, last = days[0], days[-1]
    marks: dict[date, str | None] = {}
    week_count: dict[date, int] = {}
    extra = 0
    d = week_sunday(first)
    while d <= last:
        kind, _ = classify_day(shifts, overrides.get(d), d)
        mark: str | None = None
        if kind == "off":
            sunday = week_sunday(d)
            week_count[sunday] = week_count.get(sunday, 0) + 1
            nth = week_count[sunday]
            if nth <= 2:
                mark = str(nth)
            elif d >= first:
                extra += 1
                mark = f"{EXTRA_OFF_PREFIX}{extra}"
        elif kind == "half":
            mark = HALF_MARK
        elif kind == "paid":
            mark = PAID_MARK
        marks[d] = mark
        d += timedelta(days=1)
    return [marks[d] for d in days]


# ---------------------------------------------------------------------------
# 1 か月
# ---------------------------------------------------------------------------


def _group(staff: KinmuStaffInput) -> int:
    """並び: 管理者 → 役職のある人 → ほかの常勤 → 非常勤."""
    if staff.is_manager:
        return 0
    if staff.title:
        return 1
    return 2 if staff.full_time else 3


def _staff_rows(
    s: KinmuStaffInput, month: date, days: list[date], required: float, rules: KinmuRules
) -> KinmuStaffRows:
    plan, admin_cells = _cells(s, s.shifts, s.overrides, days, rules)
    actual_shifts = s.actual_shifts if s.actual_shifts is not None else s.shifts
    actual: list[CellValue] | None = None
    if s.actual_overrides is not None:
        actual, _ = _cells(s, actual_shifts, s.actual_overrides, days, rules)
    effective, eff_shifts = (
        (s.actual_overrides, actual_shifts)
        if s.actual_overrides is not None
        else (s.overrides, s.shifts)
    )
    in_fte = not (s.on_leave and rules.on_leave_all_off)
    note: str | None = None
    if not in_fte:
        note = ON_LEAVE_NOTE
    elif not s.full_time and not rules.part_time_marks:
        note = NOT_APPLICABLE
    plan_total = _sum_cells(plan)
    admin_total = _sum_cells(admin_cells) if s.is_manager else None
    actual_total = _sum_cells(actual) if actual is not None else None
    return KinmuStaffRows(
        staff=s,
        plan=plan,
        admin=admin_cells if s.is_manager else None,
        actual=actual,
        marks=holiday_marks(eff_shifts, effective, month) if note is None else None,
        marks_note=note,
        plan_total=plan_total,
        admin_total=admin_total,
        actual_total=actual_total,
        plan_fte=fte(plan_total, required) if in_fte else None,
        admin_fte=fte(admin_total, required) if admin_total is not None and in_fte else None,
        actual_fte=fte(actual_total, required) if actual_total is not None and in_fte else None,
        holidays=(
            count_holidays(days, eff_shifts.keys(), {d: o.type for d, o in effective.items()})
            if note is None
            else None
        ),
    )


def build_kinmu_month(
    month: date,
    staff_rows: Iterable[KinmuStaffInput],
    required_hours: float | None,
    closed_days: Mapping[date, str] | None = None,
    rules: KinmuRules | None = None,
) -> KinmuMonth:
    """職員の入力 → 1 か月の勤務表の値.

    ``required_hours`` (常勤の従業者が当該月勤務すべき時間数) が未入力なら作らない (ValueError)。
    ``closed_days`` (事業所の休み) は見出しに名前を出すだけで、セルは変えない (登録がある日だけ「休」)。
    """
    if required_hours is None or required_hours <= 0:
        raise ValueError("勤務すべき時間数が未入力の月は勤務表を作れません")
    rules = rules or KinmuRules()
    days = month_days(month)
    holidays = month_holidays(month)
    closed = closed_days or {}
    headers = [
        DayHeader(d, _WEEKDAY_LABELS[d.weekday()], holidays.get(d), closed.get(d)) for d in days
    ]
    rows = [
        _staff_rows(s, month, days, required_hours, rules) for s in sorted(staff_rows, key=_group)
    ]

    def day_totals(cols: list[list[CellValue]]) -> list[float]:
        return [_sum_cells(c[i] for c in cols) for i in range(len(days))]

    plan_total = round(sum(r.plan_total for r in rows), 2)
    has_actual = any(r.actual is not None for r in rows)
    actual_total = round(sum(r.actual_total or 0.0 for r in rows), 2) if has_actual else None
    return KinmuMonth(
        month=days[0],
        required_hours=required_hours,
        days=headers,
        weeks=week_bands(month),
        staff=rows,
        plan_day_totals=day_totals([r.plan for r in rows]),
        actual_day_totals=(
            day_totals([r.actual for r in rows if r.actual is not None]) if has_actual else None
        ),
        plan_total=plan_total,
        actual_total=actual_total,
        plan_fte=fte(plan_total, required_hours),
        actual_fte=fte(actual_total, required_hours) if actual_total is not None else None,
    )


def build_holiday_summary(
    month: date, rows: Iterable[tuple[str, Mapping[date, float] | str]]
) -> HolidaySummary:
    """「集計」: その年の 1 月〜``month`` の、職員×月の休日取得数.

    ``rows`` は (職員名, {月の 1 日: 休日数})。数えない職員は休日数の代わりに文字
    (``NOT_APPLICABLE`` =「対象外」・``ON_LEAVE_NOTE`` =「休職」) を渡し、その文字を書く。
    ``month`` より後の月は空 (None)。
    """
    upto = month.replace(day=1)
    months = [date(upto.year, m, 1) for m in range(1, 13)]
    out_rows: list[tuple[str, list[float | str | None], float | str]] = []
    for name, counts in rows:
        if isinstance(counts, str):
            out_rows.append((name, [counts if m <= upto else None for m in months], counts))
            continue
        values: list[float | str | None] = [
            float(counts.get(m, 0.0)) if m <= upto else None for m in months
        ]
        out_rows.append((name, values, sum(v for v in values if isinstance(v, float))))
    month_totals: list[float | None] = [
        sum(v for r in out_rows if isinstance(v := r[1][i], float)) if m <= upto else None
        for i, m in enumerate(months)
    ]
    return HolidaySummary(
        year=upto.year,
        upto=upto,
        months=months,
        rows=out_rows,
        month_totals=month_totals,
        grand_total=sum(r[2] for r in out_rows if isinstance(r[2], float)),
    )


# ---------------------------------------------------------------------------
# 予定版の写し (kinmu_sheets.plan_snapshot)
# ---------------------------------------------------------------------------


def _hhmm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def snapshot_plan(staff: Iterable[KinmuStaffInput]) -> dict[str, Any]:
    """予定版を作ったときの職員×日の休みと型 → ``kinmu_sheets.plan_snapshot`` に置く JSON."""
    return {
        "version": _SNAPSHOT_VERSION,
        "staff": {
            s.key: {
                "shifts": {str(wd): [_hhmm(a), _hhmm(b)] for wd, (a, b) in s.shifts.items()},
                "overrides": {
                    d.isoformat(): {"type": o.type, "start": _hhmm(o.start), "end": _hhmm(o.end)}
                    for d, o in s.overrides.items()
                },
            }
            for s in staff
        },
    }


def plan_from_snapshot(
    snapshot: Mapping[str, Any], key: str
) -> tuple[dict[int, tuple[time, time]], dict[date, DayOverride]] | None:
    """写しから職員 1 人の (型, 予定の休み).

    写しに居ない職員・``version`` が違う写し・壊れた写しは None (呼び出し側はいまの予定に落とす)。
    """
    if not isinstance(snapshot, Mapping) or snapshot.get("version") != _SNAPSHOT_VERSION:
        return None
    staff = snapshot.get("staff", {})
    entry = staff.get(key) if isinstance(staff, Mapping) else None
    if not isinstance(entry, Mapping):
        return None
    try:
        shifts: dict[int, tuple[time, time]] = {}
        for wd, (a, b) in entry.get("shifts", {}).items():
            start, end = _time(a), _time(b)
            if start is not None and end is not None:
                shifts[int(wd)] = (start, end)
        overrides = {
            date.fromisoformat(d): DayOverride(
                o["type"], _time(o.get("start")), _time(o.get("end"))
            )
            for d, o in entry.get("overrides", {}).items()
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return shifts, overrides


__all__ = [
    "BREAK_AFTER_MINUTES",
    "BREAK_MINUTES",
    "NOT_APPLICABLE",
    "ON_LEAVE_NOTE",
    "CellValue",
    "DayHeader",
    "DayOverride",
    "HolidaySummary",
    "KinmuMonth",
    "KinmuRules",
    "KinmuStaffInput",
    "KinmuStaffRows",
    "WeekBand",
    "build_holiday_summary",
    "build_kinmu_month",
    "classify_day",
    "fte",
    "holiday_marks",
    "plan_from_snapshot",
    "snapshot_plan",
    "week_bands",
    "week_sunday",
    "worked_minutes",
]
