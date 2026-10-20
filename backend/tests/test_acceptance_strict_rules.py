"""受け入れ枠 (数週続けて空いているか) — 判定の決まりごとの小さな試験 (設計書 §7-2).

DB を使わない純粋な関数 (``app.services.scheduling.acceptance_strict``) だけを試す。
"""

from __future__ import annotations

import re
import time as _time
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.scheduling.acceptance_strict import (
    MARK_MID,
    MARK_NG,
    MARK_NONE,
    MARK_OK,
    KaipokeLeave,
    StaffDay,
    StrictRules,
    compute_cell,
    compute_strict,
    free_hours,
    grade,
    summarize,
    triangle_min,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG

RULES = StrictRules.from_config(DEFAULT_SCHEDULING_CONFIG)
DAY = date(2026, 10, 20)


def hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def can_start(start: str, *, visits=(), blocks=(), rules: StrictRules = RULES) -> bool:
    """その時刻ちょうどに 35 分の訪問を始められるか (窓をその 1 回ぶんにして見る)。"""
    t = hm(start)
    return free_hours(list(visits), list(blocks), t, t + rules.visit_min, rules) == [t // 60]


def day(**kwargs) -> StaffDay:
    kwargs.setdefault("shift", (hm("09:30"), hm("18:00")))
    return StaffDay(staff_id="s1", day=DAY, **kwargs)


def test_defaults_match_design() -> None:
    assert RULES.visit_min == 35
    assert RULES.cap == 7
    assert RULES.pad == 20  # ゆとり 8 + 移動 12
    assert (RULES.lunch_min, RULES.lunch_from, RULES.lunch_to) == (60, hm("11:30"), hm("13:30"))
    assert RULES.as_dict()["lunch_from"] == "11:30"


def test_touching_edges_do_not_overlap() -> None:
    visit = [(hm("10:00"), hm("10:30"))]
    assert can_start("10:50", visits=visit)  # 10:30 + 20 分ちょうど
    assert not can_start("10:45", visits=visit)
    # 前側: 新しい訪問の終わり (9:40) = 既存の始まり − 20 分 ならよい。
    assert can_start("09:05", visits=visit)
    assert not can_start("09:10", visits=visit)


def test_events_are_blocked_without_padding() -> None:
    meeting = [(hm("10:00"), hm("10:30"))]
    assert can_start("10:30", blocks=meeting)
    assert not can_start("10:25", blocks=meeting)


def test_lunch_window_fully_blocked_means_no_start() -> None:
    lunch_busy = [(hm("11:30"), hm("13:30"))]
    sd = day(events=lunch_busy)
    cell = compute_cell(sd, RULES)
    assert cell["real"]["state"] == "tight"
    assert cell["real"]["hours"] == []


def test_new_visit_cannot_eat_the_last_lunch_hour() -> None:
    # 11:30〜12:30 が塞がっている → 昼休みは 12:30〜13:30 しか残らない。
    blocks = [(hm("11:30"), hm("12:30"))]
    assert not can_start("12:40", blocks=blocks)  # 12:20〜13:35 を塞ぐ
    assert can_start("14:10", blocks=blocks)  # 13:50〜14:45+20 は昼休みと重ならない
    assert not can_start("13:30", blocks=blocks)  # 13:10 から塞ぐ → 昼休みが残らない


def test_cap_counts_only_main_visits() -> None:
    six = day(n=6)
    seven = day(n=7)
    assert compute_cell(six, RULES)["real"]["state"] == "free"
    assert compute_cell(seven, RULES)["real"]["state"] == "full"
    assert compute_cell(seven, RULES)["ignore"]["state"] == "full"


def test_hour_bucket_and_hidden_eight_oclock() -> None:
    # 10:55 開始だけが入る人 → 10 時台。
    visits = [(hm("09:00"), hm("10:35")), (hm("11:50"), hm("12:00"))]
    sd = day(shift=(hm("10:55"), hm("11:30")), visits=visits)
    assert compute_cell(sd, RULES)["real"]["hours"] == [10]
    # 8:30 からの勤務で 8 時台に入れても表には出さない。
    early = day(shift=(hm("08:30"), hm("09:30")))
    assert compute_cell(early, RULES)["real"]["hours"] == []


@pytest.mark.parametrize(
    ("override", "expected_state", "hours_has"),
    [
        ("off", "off", None),
        ("paid_off", "off", None),
        ("am_off", "free", 13),
        ("pm_off", "free", 10),
    ],
)
def test_rakusuke_leave(override: str, expected_state: str, hours_has: int | None) -> None:
    cell = compute_cell(day(rk_type=override), RULES)
    assert cell["real"]["state"] == expected_state
    assert cell["ignore"]["state"] == "free"
    if hours_has is not None:
        assert hours_has in cell["real"]["hours"]
    if override == "am_off":
        assert min(cell["real"]["hours"]) == 13
        assert cell["real"]["why_kind"] == "half"
    if override == "pm_off":
        assert max(cell["real"]["hours"]) <= 11
    if override == "paid_off":
        assert cell["real"]["why"] == "有給（らく助）"
        assert cell["real"]["why_kind"] == "full"


def test_rakusuke_custom_time_is_not_leave() -> None:
    cell = compute_cell(day(rk_type="custom_time", rk_time=(hm("14:00"), hm("16:00"))), RULES)
    assert cell["real"]["state"] == "free"
    assert cell["real"]["hours"] == [14, 15]
    assert cell["real"]["why"] == "時間変更"


def test_kaipoke_leave_window_and_overlap_with_override() -> None:
    pm = compute_cell(day(kp_off=KaipokeLeave("pm_off", "PM休み")), RULES)
    assert pm["real"]["why"] == "PM休み（カイポケ）"
    assert max(pm["real"]["hours"]) <= 11
    # override で 14:00 から、カイポケで午前休 → 重なり (14:00〜)。
    both = compute_cell(
        day(
            rk_type="custom_time",
            rk_time=(hm("14:00"), hm("18:00")),
            kp_off=KaipokeLeave("am_off", "AM休み"),
        ),
        RULES,
    )
    assert min(both["real"]["hours"]) == 14
    off = compute_cell(day(kp_off=KaipokeLeave("off", "休み")), RULES)
    assert off["real"]["state"] == "off"
    assert off["ignore"]["state"] == "free"


def test_unknown_leave_blocks_only_in_real_mode() -> None:
    sd = day(kp_unknown=[(hm("09:30"), hm("18:00"), "休み調整")])
    cell = compute_cell(sd, RULES)
    assert cell["real"]["state"] == "tight"
    assert cell["real"]["unknown_leave"] == "休み調整"
    assert cell["ignore"]["state"] == "free"


def test_not_employed_cell() -> None:
    assert compute_cell(day(not_employed=True), RULES) == {"n": 0, "state": "not_employed"}


@pytest.mark.parametrize(
    ("n_weeks", "ok", "mark"),
    [
        (4, 4, MARK_OK),
        (4, 3, MARK_MID),
        (4, 2, MARK_NG),
        (6, 6, MARK_OK),
        (6, 5, MARK_MID),
        (6, 4, MARK_MID),
        (6, 3, MARK_NG),
        (8, 8, MARK_OK),
        (8, 7, MARK_MID),
        (8, 5, MARK_MID),
        (8, 4, MARK_NG),
        (7, 7, MARK_OK),
        (7, 5, MARK_MID),
        (7, 4, MARK_NG),
    ],
)
def test_grade_boundaries(n_weeks: int, ok: int, mark: str) -> None:
    # 7 週 = 8 週のうち 1 週が祝日などで見られなかったとき。
    total = 8 if n_weeks == 7 else n_weeks
    assert grade(ok, n_weeks, total) == mark


def test_triangle_min_table() -> None:
    assert [triangle_min(n) for n in (3, 4, 5, 6, 7, 8)] == [2, 3, 4, 4, 5, 5]


def test_too_few_weeks_is_dash() -> None:
    assert grade(3, 3, 8) == MARK_NONE
    assert grade(4, 4, 8) == MARK_OK
    assert grade(1, 1, 4) == MARK_NONE


def test_summarize_skips_holiday_and_days_without_counted_staff() -> None:
    mondays = [date(2026, 9, 28) + timedelta(weeks=i) for i in range(4)]
    cells: dict = {"s1": {}}
    days: dict[str, str] = {}
    for i, monday in enumerate(mondays):
        d = monday.isoformat()
        days[d] = "holiday" if i == 1 else "ok"
        sd = StaffDay(staff_id="s1", day=monday, shift=(hm("09:30"), hm("18:00")))
        cells["s1"].update(compute_strict([sd], RULES)["s1"])
    # 4 週目は入職前 → 数えない。
    cells["s1"][mondays[3].isoformat()] = {"n": 0, "state": "not_employed"}
    got = summarize(days, cells, ["s1"], mondays, "real", [0], RULES)
    assert got[(0, 10)]["counted"] == 2
    assert got[(0, 10)]["mark"] == MARK_OK
    assert got[(0, 10)]["per_week"] == [1, 1]


def test_compute_strict_is_fast_for_eight_weeks_fifteen_staff() -> None:
    """8 週・15 人・1 日 7 件の材料で 1 秒以内 (M7)。"""
    staff_days = []
    start = date(2026, 8, 31)
    for w in range(8):
        for wd in range(6):
            d = start + timedelta(weeks=w, days=wd)
            for s in range(15):
                visits = [(hm("09:30") + k * 70, hm("09:30") + k * 70 + 35) for k in range(7)]
                staff_days.append(
                    StaffDay(
                        staff_id=f"s{s}",
                        day=d,
                        shift=(hm("08:30"), hm("18:00")),
                        n=6,
                        visits=visits,
                        events=[(hm("08:30"), hm("08:45"))],
                    )
                )
    began = _time.perf_counter()
    cells = compute_strict(staff_days, RULES)
    elapsed = _time.perf_counter() - began
    assert len(cells) == 15
    assert elapsed < 1.0, elapsed


ACCEPTANCE_SOURCES = [
    "app/services/scheduling/acceptance_strict.py",
    "app/services/scheduling/acceptance_strict_data.py",
    "app/services/scheduling/acceptance_strict_refresh.py",
]


@pytest.mark.parametrize("path", ACCEPTANCE_SOURCES)
def test_no_leave_values_written_outside_core_leave(path: str) -> None:
    """休みの境目・種類の値は app/core/leave.py だけに書く (段階 0 §0-1)。"""
    text = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    assert "13:00" not in text
    assert "12:30" not in text
    assert not re.search(r"==\s*[\"']off[\"']", text)
