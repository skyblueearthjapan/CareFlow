"""NG 曜日 (訪問しない曜日) と性別制限「男性のみ」のテスト (PO 2026-10-30)。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_ng_weekday_sex.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import check  # noqa: E402
from solver import Job, allowed, load, ng_weekdays_of, run  # noqa: E402

MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
HOME = (35.60, 140.10)
CONFIG = {"regular": {"C1": "稲", "C2": "稲"}, "manager": {}, "cap_regular": 6}


def write_world(tmp_path, *, sex_restriction=None, ng_weekdays=None, marks=()):
    wp_json = {"time_type": "時間帯", "preferred_start": "15:00", "preferred_end": "17:00"}
    if ng_weekdays is not None:
        wp_json["ng_weekdays"] = ng_weekdays
    d = {
        "week_start": str(MON),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {
                "id": "p0",
                "name": "p0",
                "weekly_pattern": wp_json,
                "lat": HOME[0],
                "lng": HOME[1],
                "primary_office_id": "o1",
                "sex_restriction": sex_restriction,
            }
        ],
        "staff": [
            {"id": "SF", "name": "SF", "code": "C1", "sex": "female"},
            {"id": "SM", "name": "SM", "code": "C2", "sex": "male"},
        ],
        "shifts": [
            {"staff_id": sid, "weekday": wd, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
            for sid in ("SF", "SM")
            for wd in (0, 1)
        ],
        "weekly_overrides": [],
        "events": [],
        "visits": [
            {
                "id": "v1",
                "patient_id": "p0",
                "visit_date": str(MON),
                "start_time": "15:30",
                "end_time": "16:05",
                "primary_staff_id": "SF",
                "course_staff_id": None,
                "course_office": None,
                "course_code": None,
            }
        ],
        "special_marks": [
            {"id": f"m{wd}", "patient_id": "p0", "weekday": wd, "duration_min": 35} for wd in marks
        ],
    }
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return wp, hp


def test_ng_weekdays_of_reads_codes_and_ints():
    assert ng_weekdays_of({"ng_weekdays": ["Mon", 3, "x", True, 9]}) == frozenset({0, 3})
    assert ng_weekdays_of({}) == frozenset()
    assert ng_weekdays_of(None) == frozenset()


def test_male_only_visit_goes_to_the_male_staff(tmp_path):
    wp, hp = write_world(tmp_path, sex_restriction="male_only")
    d, visits, staff, out, _hist, _l = run(wp, hp, CONFIG, 2, log=lambda m: None)
    assert visits[0].male_only and not visits[0].female_only
    _veh, routes, dropped = out[MON]
    assert dropped == []
    assert [sid for sid, seq in routes.items() if seq] == ["SM"]
    errors, _w = check(d, out, 6, 0)
    assert errors == []


def test_male_only_is_hard_in_allowed(tmp_path):
    wp, hp = write_world(tmp_path, sex_restriction="male_only")
    _d, visits, staff, *_ = load(wp, hp, CONFIG)
    v = visits[0]
    job = Job(visits=[v], offsets=[0], start=v.fixed_start, dur=35, lo=0, hi=0, pref_lo=0, pref_hi=0)
    assert allowed(staff["SM"], job)
    assert not allowed(staff["SF"], job)


def test_female_only_still_hard(tmp_path):
    wp, hp = write_world(tmp_path, sex_restriction="female_only")
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert visits[0].female_only and not visits[0].male_only


def test_special_mark_on_ng_weekday_is_not_added(tmp_path):
    # 火曜が NG 曜日 → 火曜の○は足さない (skipped). 月曜は訪問があるので元々足さない.
    wp, hp = write_world(tmp_path, ng_weekdays=["Tue"], marks=(0, 1))
    d, visits, *_ = load(wp, hp, CONFIG, {"add_special": True})
    assert not any(v.new for v in visits)
    assert d["special_added"] == [] and sorted(d["special_skipped"]) == ["m0", "m1"]
    assert visits[0].ng_weekdays == frozenset({1})


def test_special_mark_on_other_day_is_still_added(tmp_path):
    wp, hp = write_world(tmp_path, ng_weekdays=["Wed"], marks=(1,))
    d, visits, *_ = load(wp, hp, CONFIG, {"add_special": True})
    assert [v.day for v in visits if v.new] == [TUE]
    assert d["special_added"] == ["m1"]


def test_existing_visit_keeps_its_day(tmp_path):
    # 曜日は変えない: 既存の訪問が NG 曜日に載っていても、ならしは別の日へ動かさない (点検で知らせる側).
    wp, hp = write_world(tmp_path, ng_weekdays=["Mon"])
    _d, visits, _s, out, _h, _l = run(wp, hp, CONFIG, 2, log=lambda m: None)
    _veh, routes, _dropped = out[MON]
    placed = [j.visits[0].id for seq in routes.values() for j, _t in seq]
    assert placed == ["v1"]
