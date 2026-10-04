"""案から外す訪問 (--drop)・特別訪問週間の○を足す (--add-special)・今の担当にコースの担当を使う のテスト。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_special.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import check, flags_for  # noqa: E402
from solver import join_fixed, load, run  # noqa: E402

MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
HOME = (35.60, 140.10)
CONFIG = {"regular": {"C1": "稲"}, "manager": {}, "cap_regular": 6}


def write_world(tmp_path, marks=(), course_staff=None):
    """職員 1 名 (月・火 9:00〜18:00)。利用者 p0 (時間帯 15:00〜17:00) は月 15:30 の訪問が 2 件 (11:00 は古い残り)。"""
    d = {
        "week_start": str(MON),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {
                "id": "p0",
                "name": "p0",
                "weekly_pattern": {"time_type": "時間帯", "preferred_start": "15:00", "preferred_end": "17:00"},
                "lat": HOME[0],
                "lng": HOME[1],
                "primary_office_id": "o1",
                "sex_restriction": None,
            }
        ],
        "staff": [{"id": "S1", "name": "S1", "code": "C1", "sex": "female"}],
        "shifts": [
            {"staff_id": "S1", "weekday": wd, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
            for wd in (0, 1)
        ],
        "weekly_overrides": [],
        "events": [],
        "visits": [
            {
                "id": "aaaa1111-old",
                "patient_id": "p0",
                "visit_date": str(MON),
                "start_time": "11:00",
                "end_time": "11:35",
                "primary_staff_id": None,
                "course_staff_id": None,
                "course_office": None,
                "course_code": None,
            },
            {
                "id": "bbbb2222-new",
                "patient_id": "p0",
                "visit_date": str(MON),
                "start_time": "15:30",
                "end_time": "16:05",
                "primary_staff_id": None,
                "course_staff_id": course_staff,
                "course_office": None,
                "course_code": None,
            },
        ],
        "special_marks": [
            {"id": f"m{wd}", "patient_id": "p0", "weekday": wd, "duration_min": 35} for wd in marks
        ],
    }
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return wp, hp


def test_drop_removes_the_visit_by_id_prefix(tmp_path):
    wp, hp = write_world(tmp_path)
    d, visits, *_ = load(wp, hp, CONFIG, {"drop": ["aaaa1111"]})
    assert [v.id for v in visits] == ["bbbb2222-new"]
    assert d["dropped_visits"] == ["aaaa1111-old"]


def test_drop_unknown_id_is_an_error(tmp_path):
    wp, hp = write_world(tmp_path)
    with pytest.raises(ValueError, match="見つかりません"):
        load(wp, hp, CONFIG, {"drop": ["ffff"]})


def test_course_staff_is_the_current_staff_when_visit_has_none(tmp_path):
    wp, hp = write_world(tmp_path, course_staff="S1")
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert {v.id: v.cur_staff for v in visits} == {"aaaa1111-old": None, "bbbb2222-new": "S1"}


def test_marks_are_added_only_on_days_without_a_visit(tmp_path):
    wp, hp = write_world(tmp_path, marks=(0, 1))  # 月は訪問がある → 足さない / 火は足す
    d, visits, *_ = load(wp, hp, CONFIG, {"add_special": True})
    added = [v for v in visits if v.new]
    assert [(v.day, v.win, v.dur) for v in added] == [(TUE, (15 * 60, 17 * 60), 35)]
    assert d["special_added"] == ["m1"] and d["special_skipped"] == ["m0"]


def test_marks_are_not_added_without_the_option(tmp_path):
    wp, hp = write_world(tmp_path, marks=(1,))
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert not any(v.new for v in visits)


def test_added_visit_is_placed_inside_the_window_and_not_flagged_as_moved(tmp_path):
    wp, hp = write_world(tmp_path, marks=(1,))
    d, visits, _s, out, hist, _l = run(
        wp, hp, CONFIG, 2, {"drop": ["aaaa1111"], "add_special": True}, log=lambda m: None
    )
    _veh, routes, dropped = out[TUE]
    assert dropped == []
    (job, t), = routes["S1"]
    assert job.visits[0].new and 15 * 60 <= t <= 17 * 60
    errors, _w = check(d, out, 6, 0)
    assert errors == []
    flags = flags_for(out, hist)
    assert not any(k == "move" for k, _ in flags.get(job.visits[0].id, []))


def test_fixed_time_far_from_the_window_is_kept_as_is():
    # 固定 11:00・希望 17:00〜17:35 → 間の 14:00 などは選ばせない (固定のまま)
    assert join_fixed((17 * 60, 17 * 60 + 35), 11 * 60) == (11 * 60, 11 * 60)


def test_fixed_time_near_the_window_is_joined():
    # 固定 16:15・希望 16:20〜17:35 → 16:15〜17:35 でつなぐ
    assert join_fixed((16 * 60 + 20, 17 * 60 + 35), 16 * 60 + 15) == (16 * 60 + 15, 17 * 60 + 35)
    assert join_fixed((9 * 60, 12 * 60), 10 * 60) == (9 * 60, 12 * 60)
