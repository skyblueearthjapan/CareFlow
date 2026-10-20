"""休みの段 #2: core.leave の足し (visit_hits_leave・kaipoke_send_window) と必要休日数・休日数.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-1・§2-2・§11-1「数え方」。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.core.leave import kaipoke_send_window, visit_hits_leave
from app.models import OfficeClosedDay, Staff, StaffShift, StaffWeeklyOverride
from app.services.leave_calendar import (
    HolidayCount,
    count_holidays,
    iso_key,
    month_days,
    required_holiday_dates,
    required_holidays,
    required_holidays_for_month,
    staff_holiday_counts,
)


def t(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


# ---------------------------------------------------------------------------
# visit_hits_leave (境目ちょうど)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("leave_type", "start", "end", "expected"),
    [
        ("off", "09:00", "10:00", True),
        ("paid_off", "16:00", "17:00", True),
        # 午前休 = 13:00 から勤務: 開始 12:59 は対象・13:00 は対象外
        ("am_off", "12:59", "13:30", True),
        ("am_off", "13:00", "14:00", False),
        ("am_off", "09:00", "10:00", True),
        # 午後休 = 12:30 から休み: 終了 12:30 は対象外・12:31 は対象
        ("pm_off", "11:30", "12:30", False),
        ("pm_off", "11:31", "12:31", True),
        ("pm_off", "15:00", "16:00", True),
        # 休みではない
        ("custom_time", "09:00", "10:00", False),
        (None, "09:00", "10:00", False),
    ],
)
def test_visit_hits_leave_boundaries(leave_type, start, end, expected) -> None:
    assert visit_hits_leave(leave_type, t(start), t(end)) is expected


def test_visit_hits_leave_without_end_uses_start_for_pm_off() -> None:
    assert visit_hits_leave("pm_off", t("12:30"), None) is True
    assert visit_hits_leave("pm_off", t("12:29"), None) is False
    assert visit_hits_leave("am_off", t("12:00"), None) is True
    assert visit_hits_leave("off", None, None) is True


# ---------------------------------------------------------------------------
# kaipoke_send_window
# ---------------------------------------------------------------------------


def test_kaipoke_send_window_by_type() -> None:
    s, e = t("08:30"), t("17:30")
    assert kaipoke_send_window("off", s, e, True) == (s, e)
    assert kaipoke_send_window("paid_off", s, e, True) == (s, e)
    assert kaipoke_send_window("am_off", s, e, True) == (s, t("13:00"))
    assert kaipoke_send_window("pm_off", s, e, True) == (t("12:30"), e)


def test_kaipoke_send_window_non_working_day_uses_business_hours() -> None:
    assert kaipoke_send_window("off", None, None, False) == (t("09:30"), t("18:00"))
    assert kaipoke_send_window("paid_off", t("08:30"), t("17:30"), False) == (
        t("09:30"),
        t("18:00"),
    )
    assert kaipoke_send_window(
        "off", None, None, False, business_hours=(t("09:00"), t("17:00"))
    ) == (t("09:00"), t("17:00"))


def test_kaipoke_send_window_rejects_custom_time() -> None:
    with pytest.raises(ValueError):
        kaipoke_send_window("custom_time", t("09:00"), t("18:00"), True)


# ---------------------------------------------------------------------------
# 必要休日数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 9, 11),  # 土 4・日 4・祝 3 (9/21・9/22 国民の休日・9/23)
        (2026, 10, 10),  # 土 5・日 4・祝 1 (10/12)
        (2026, 11, 11),  # 土 4・日 5・祝 2 (11/3・11/23)
        (2026, 2, 10),  # 2 月: 土 4・日 4・祝 2 (2/11・2/23)
        (2026, 12, 8),  # 祝日のない月: 土 4・日 4
        (2026, 5, 13),  # 日曜の祝日 5/3 → 振替休日 5/6 も数える: 土 5・日 5・祝 3
        (2027, 3, 9),  # 3/21 (日) 春分の日 → 3/22 振替休日
        (2028, 1, 11),  # 1/1 が土曜 = 土曜として 1 日: 土 5・日 5・成人の日 1
    ],
)
def test_required_holidays(year: int, month: int, expected: int) -> None:
    assert required_holidays(date(year, month, 1)) == expected


def test_required_holidays_counts_saturday_holiday_once() -> None:
    dates = required_holiday_dates(date(2028, 1, 15))
    assert dates.count(date(2028, 1, 1)) == 1
    # 土曜の祝日に振替休日は無い
    assert date(2028, 1, 3) not in dates


def test_required_holidays_sunday_holiday_counts_substitute() -> None:
    dates = required_holiday_dates(date(2026, 5, 1))
    assert date(2026, 5, 3) in dates  # 日曜
    assert date(2026, 5, 6) in dates  # 振替休日


def test_required_holidays_with_office_closed_days() -> None:
    # 12 月: 年末年始 12/29〜12/31 (平日) と土曜の 12/26 (土曜は二重に数えない)
    closed = [date(2026, 12, 26), date(2026, 12, 29), date(2026, 12, 30), date(2026, 12, 31)]
    assert required_holidays(date(2026, 12, 1), closed) == 11
    # 1 月: 元日 (祝日と重なる) は二重に数えない・1/4 (月) は足す
    closed_jan = [date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 4)]
    assert required_holidays(date(2027, 1, 1)) == 12
    assert required_holidays(date(2027, 1, 1), closed_jan) == 13


@pytest.mark.asyncio
async def test_required_holidays_for_month_reads_closed_days(db) -> None:
    db.add_all(
        [
            OfficeClosedDay(date=date(2026, 12, 30), label="年末年始"),
            OfficeClosedDay(date=date(2026, 12, 31), label="年末年始"),
            OfficeClosedDay(date=date(2027, 1, 4), label="年末年始"),
        ]
    )
    await db.flush()
    assert await required_holidays_for_month(db, date(2026, 12, 1)) == 10
    assert await required_holidays_for_month(db, date(2027, 1, 1)) == 13


# ---------------------------------------------------------------------------
# 休日数
# ---------------------------------------------------------------------------

MON_FRI = {0, 1, 2, 3, 4}


def test_count_holidays_pattern_only() -> None:
    # 2026 年 10 月・月〜金勤務 → 型の休み = 土 5・日 4 = 9 (祝日は勤務日のまま)
    assert count_holidays(month_days(date(2026, 10, 1)), MON_FRI, {}) == HolidayCount(9.0, 0)


def test_count_holidays_mixed() -> None:
    days = month_days(date(2026, 10, 1))
    overrides = {
        date(2026, 10, 5): "off",  # 月・公休 → 1
        date(2026, 10, 6): "am_off",  # 火・半休 → 0.5
        date(2026, 10, 7): "pm_off",  # 水・半休 → 0.5
        date(2026, 10, 8): "paid_off",  # 木・有給 → 0・有給 1
        date(2026, 10, 9): "custom_time",  # 金・時間変更 → 0
        date(2026, 10, 10): "am_off",  # 土 (型で勤務しない) の半休 → 1
        date(2026, 10, 12): "off",  # 祝日の公休 → 1 (祝日は勤務日)
    }
    got = count_holidays(days, MON_FRI, overrides)
    # 型の休み 9 (土 10/10 を含む) ＋ 公休 2 ＋ 半休 1.0 = 12.0
    assert got == HolidayCount(12.0, 1)


@pytest.mark.asyncio
async def test_staff_holiday_counts_reads_overrides_across_iso_year(db) -> None:
    """1 月 1 日が前の年の ISO 週 (2027-W53) に入る月でも落とさない (L2)."""
    staff = Staff(name="数え 花子")
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=wd < 5))
    # 2027-01-01 (金) は ISO 2026-W53
    y, w, wd = iso_key(date(2027, 1, 1))
    assert (y, w) == (2026, 53)
    db.add(
        StaffWeeklyOverride(
            staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="off"
        )
    )
    y2, w2, wd2 = iso_key(date(2027, 1, 4))
    db.add(
        StaffWeeklyOverride(
            staff_id=staff.id, iso_year=y2, iso_week=w2, weekday=wd2, override_type="paid_off"
        )
    )
    # 12 月の行 (2026-12-31・同じ ISO 週) は 1 月に数えない
    y3, w3, wd3 = iso_key(date(2026, 12, 31))
    db.add(
        StaffWeeklyOverride(
            staff_id=staff.id, iso_year=y3, iso_week=w3, weekday=wd3, override_type="off"
        )
    )
    await db.flush()

    counts = await staff_holiday_counts(db, date(2027, 1, 1), [staff.id])
    # 1 月: 型の休み (土 5・日 5) = 10 ＋ 公休 1/1 = 11・有給 1
    assert counts[staff.id] == HolidayCount(11.0, 1)
