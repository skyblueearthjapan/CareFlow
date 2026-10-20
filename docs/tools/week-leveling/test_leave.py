"""休みの種類と境目 (休み設計 2026-10-20 §2-1・§3-2) のテスト。

公休・有給 = 終日の休み／午前休 = 13:00 から勤務／午後休 = 12:30 まで勤務。
主担当が空で manual_staff_override が真の訪問 (明示の担当なし) はコースの担当に読み替えない。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_leave.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import staff_hours  # noqa: E402
from solver import AM_OFF_UNTIL, FULL_DAY_OFF, PM_OFF_FROM, leave_hours, load  # noqa: E402

DAY = date(2026, 10, 12)  # 月曜
HOME = (35.60, 140.10)
CONFIG = {"regular": {"C1": "稲"}, "manager": {}, "cap_regular": 6}


def test_constants():
    assert FULL_DAY_OFF == {"off", "paid_off"}
    assert AM_OFF_UNTIL == 13 * 60
    assert PM_OFF_FROM == 12 * 60 + 30


@pytest.mark.parametrize(
    ("kind", "hours", "want"),
    [
        ("am_off", (540, 1080), (780, 1080)),  # 9:00〜18:00 → 13:00〜18:00
        ("pm_off", (540, 1080), (540, 750)),  # 9:00〜18:00 → 9:00〜12:30
        ("am_off", (840, 1080), (840, 1080)),  # 14:00 からの人は変わらない
        ("pm_off", (540, 720), (540, 720)),  # 12:00 までの人は変わらない
        ("custom_time", (540, 1080), (540, 1080)),
        (None, (540, 1080), (540, 1080)),
    ],
)
def test_leave_hours(kind, hours, want):
    assert leave_hours(kind, hours) == want


def week(overrides, visits=()):
    return {
        "week_start": str(DAY),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {
                "id": "p0",
                "name": "p0",
                "weekly_pattern": {"time_type": "固定"},
                "lat": HOME[0],
                "lng": HOME[1],
                "primary_office_id": "o1",
                "sex_restriction": None,
            }
        ],
        "staff": [{"id": "S1", "name": "S1", "code": "C1", "sex": "female"}],
        "shifts": [{"staff_id": "S1", "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}],
        "weekly_overrides": [
            {"staff_id": "S1", "weekday": 0, "override_type": k, "start_time": None, "end_time": None}
            for k in overrides
        ],
        "events": [],
        "visits": list(visits),
    }


def load_week(tmp_path, d):
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return load(wp, hp, CONFIG)


@pytest.mark.parametrize(
    ("kind", "off", "shift"),
    [
        ("off", True, None),
        ("paid_off", True, None),
        ("am_off", False, (780, 1080)),
        ("pm_off", False, (540, 750)),
    ],
)
def test_load_reads_leave_kinds(tmp_path, kind, off, shift):
    _d, _v, staff, _o, _h = load_week(tmp_path, week([kind]))
    s = staff["S1"]
    assert (0 in s.off) is off
    if shift:
        assert s.shift[0] == shift


@pytest.mark.parametrize(
    ("kind", "want"),
    [("off", None), ("paid_off", None), ("am_off", (780, 1080)), ("pm_off", (540, 750)), (None, (540, 1080))],
)
def test_report_staff_hours(kind, want):
    d = week([kind] if kind else [])
    assert staff_hours(d, "S1", DAY) == want


def _visit(vid, primary, manual):
    return {
        "id": vid,
        "patient_id": "p0",
        "visit_date": str(DAY),
        "start_time": "10:00",
        "end_time": "10:35",
        "primary_staff_id": primary,
        "manual_staff_override": manual,
        "course_staff_id": "S1",
        "course_office": "稲",
        "course_code": "A",
    }


def test_explicit_unassigned_is_not_read_as_course_staff(tmp_path):
    visits = [_visit("v1", None, True), _visit("v2", None, False), _visit("v3", "S1", True)]
    _d, vs, _s, _o, _h = load_week(tmp_path, week([], visits))
    cur = {v.id: v.cur_staff for v in vs}
    assert cur == {"v1": None, "v2": "S1", "v3": "S1"}
