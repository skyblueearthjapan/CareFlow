"""app.core.leave — 休みの種類・境目・カイポケの件名からの判定の試験.

正典: docs/plans/shared-foundation-leave-acceptance-2026-10-20.md §0-1。
"""

from __future__ import annotations

from datetime import time

import pytest

from app.core.leave import (
    AM_OFF_UNTIL,
    FULL_DAY_OFF,
    HALF_DAY_OFF,
    LEAVE_TYPES,
    PM_OFF_FROM,
    classify_kaipoke_leave,
    is_leave_event,
    leave_window,
)

S9, E18 = time(9, 0), time(18, 0)


def t(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


def test_constants() -> None:
    assert FULL_DAY_OFF == {"off", "paid_off"}
    assert HALF_DAY_OFF == {"am_off", "pm_off"}
    assert LEAVE_TYPES == {"off", "paid_off", "am_off", "pm_off"}
    assert "custom_time" not in LEAVE_TYPES
    assert AM_OFF_UNTIL == time(13, 0)
    assert PM_OFF_FROM == time(12, 30)


@pytest.mark.parametrize(
    ("subject", "start", "end", "expected"),
    [
        # 件名「休み」は時刻で決める (勤務 9:00-18:00・§0-1 の表 6〜10)
        ("休み", "9:00", "18:00", "off"),
        ("休み", "9:00", "13:00", "am_off"),
        ("休み", "12:30", "17:30", "pm_off"),
        ("休み", "12:30", "18:00", "pm_off"),
        ("休み", "9:30", "17:00", "off"),  # 始まり+30 分以内・終わり-60 分以降 (境目ちょうど)
        ("休み", "9:31", "18:00", "unknown"),  # 始まりが +30 分を超える
        ("休み", "9:00", "16:59", "unknown"),  # 終わりが -60 分より前
        ("休み", "9:00", "12:00", "am_off"),  # 午前休の終わりは 12:00〜14:00
        ("休み", "9:00", "14:00", "am_off"),
        ("休み", "9:00", "14:01", "unknown"),
        ("休み", "12:00", "18:00", "pm_off"),  # 午後休の始まりは 12:00〜13:30
        ("休み", "13:30", "17:00", "pm_off"),
        ("休み", "13:31", "18:00", "unknown"),
        ("休み", "11:59", "18:00", "unknown"),
        ("休み", "10:00", "11:00", "unknown"),
        ("夏季休暇", "9:00", "18:00", "off"),  # 休みの語があり終日の形
        ("夏季休暇", "10:00", "11:00", "unknown"),
        # 件名で決まるもの (時刻は見ない)
        ("PM休み", "12:30", "17:30", "pm_off"),
        ("ＰＭ休み", "12:30", "17:30", "pm_off"),  # 全角
        ("午後休", "12:30", "18:00", "pm_off"),
        ("AM休み", "9:00", "13:00", "am_off"),
        ("午前休", "9:00", "13:00", "am_off"),
        ("有給", "9:00", "18:00", "paid_off"),
        ("有休", "9:00", "18:00", "paid_off"),
        ("有給休暇", "9:00", "13:00", "paid_off"),
        ("代休", "9:00", "18:00", "off"),
        ("欠勤", "9:00", "18:00", "off"),
        ("病欠", "9:00", "18:00", "off"),
        ("忌引", "9:00", "18:00", "off"),
        ("公休", "9:00", "18:00", "off"),
        ("年休", "9:00", "18:00", "paid_off"),  # 年次有給休暇
        # 休みではない
        ("休憩", "12:00", "13:00", None),
        ("休日出勤", "9:00", "18:00", None),
        ("休日当番", "9:00", "18:00", None),
        ("休薬の確認", "10:00", "10:30", None),
        ("朝会", "8:45", "9:00", None),
        ("", "9:00", "18:00", None),
        ("会議", "10:00", "11:00", None),
    ],
)
def test_classify_kaipoke_leave(subject: str, start: str, end: str, expected: str | None) -> None:
    assert classify_kaipoke_leave(subject, t(start), t(end), S9, E18) == expected


def test_classify_without_times_or_shift() -> None:
    assert classify_kaipoke_leave(None) is None
    assert classify_kaipoke_leave("休み") == "off"  # 時刻なし
    assert classify_kaipoke_leave("夏季休暇") == "off"
    assert classify_kaipoke_leave("PM休み") == "pm_off"
    # 勤務時刻が無い → 営業時間 (既定 9:30〜18:00) で代える
    assert classify_kaipoke_leave("休み", t("9:30"), t("18:00")) == "off"
    assert classify_kaipoke_leave("休み", t("9:00"), t("13:00")) == "am_off"
    assert classify_kaipoke_leave("休み", t("12:30"), t("17:30")) == "pm_off"
    assert classify_kaipoke_leave("休み", t("10:01"), t("18:00")) == "unknown"
    # 呼び出し側が SchedulingConfig の営業時間を渡せる
    hours = (t("8:00"), t("17:00"))
    assert classify_kaipoke_leave("休み", t("8:00"), t("16:00"), business_hours=hours) == "off"


def test_classify_shift_830_1730() -> None:
    # §0-1 の正解: 休み 8:30-17:30 (勤務 8:30-17:30) → off
    assert classify_kaipoke_leave("休み", t("8:30"), t("17:30"), t("8:30"), t("17:30")) == "off"


def test_am_checked_before_pm() -> None:
    assert classify_kaipoke_leave("AM休み・PM出勤", t("9:00"), t("13:00"), S9, E18) == "am_off"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("休み", True),
        ("有給", True),
        ("夏季休暇", True),
        ("休み（通院）", True),
        ("休日出勤 休み", True),  # 除外語を除いても「休み」が残る
        ("休憩", False),
        ("休日当番", False),
        ("朝会", False),
        (None, False),
    ],
)
def test_is_leave_event(title: str | None, expected: bool) -> None:
    assert is_leave_event(title) is expected


@pytest.mark.parametrize(
    ("leave_type", "shift", "expected"),
    [
        ("off", ("9:00", "18:00"), None),
        ("paid_off", ("9:00", "18:00"), None),
        ("am_off", ("9:00", "18:00"), ("13:00", "18:00")),
        ("pm_off", ("9:00", "18:00"), ("9:00", "12:30")),
        # 境目ちょうど: 13:00 までの勤務の午前休・12:30 からの勤務の午後休は働く時間が無い
        ("am_off", ("9:00", "13:00"), None),
        ("pm_off", ("12:30", "18:00"), None),
        # 境目の 1 分外は残る
        ("am_off", ("9:00", "13:01"), ("13:00", "13:01")),
        ("pm_off", ("12:29", "18:00"), ("12:29", "12:30")),
        # 境目より後から / 前までの勤務は半休でも勤務時間そのまま
        ("am_off", ("14:00", "18:00"), ("14:00", "18:00")),
        ("pm_off", ("9:00", "12:00"), ("9:00", "12:00")),
        # 休みでない種類
        ("custom_time", ("9:00", "18:00"), ("9:00", "18:00")),
        (None, ("9:00", "18:00"), ("9:00", "18:00")),
    ],
)
def test_leave_window(
    leave_type: str | None, shift: tuple[str, str], expected: tuple[str, str] | None
) -> None:
    got = leave_window(leave_type, t(shift[0]), t(shift[1]))
    assert got == (None if expected is None else (t(expected[0]), t(expected[1])))
