"""完全固定（赤ピン）= 曜日・時刻を動かさない（担当は自由）のテスト。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_red_pin.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from solver import is_time_fixed, load, run  # noqa: E402

MON = date(2026, 10, 12)
HOME = (35.60, 140.10)
CONFIG = {"regular": {"C1": "稲"}, "manager": {}, "cap_regular": 6}
WP = {"time_type": "時間帯", "preferred_start": "15:00", "preferred_end": "17:00"}


def world(tmp_path, start="11:00", source="auto", locked=True, has_unlocked=False):
    d = {
        "week_start": str(MON),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {"id": "p0", "name": "p0", "weekly_pattern": WP, "lat": HOME[0], "lng": HOME[1],
             "primary_office_id": "o1", "sex_restriction": None}
        ],
        "staff": [{"id": "S1", "name": "S1", "code": "C1", "sex": "female"}],
        "shifts": [{"staff_id": "S1", "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}],
        "weekly_overrides": [],
        "events": [],
        "visits": [
            {"id": "v1", "patient_id": "p0", "visit_date": str(MON), "start_time": start,
             "end_time": None, "primary_staff_id": None, "course_staff_id": None,
             "course_office": None, "course_code": None, "source": source}
        ],
        "pfv_locked": [{"patient_id": "p0", "weekday": 0, "start_time": "11:00", "slot_index": 0,
                        "has_unlocked": has_unlocked}] if locked else [],
    }
    end_h, end_m = divmod(int(start[:2]) * 60 + int(start[3:]) + 35, 60)
    d["visits"][0]["end_time"] = f"{end_h:02d}:{end_m:02d}"
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return wp, hp


def test_locked_slot_fixes_time_even_outside_preference(tmp_path):
    wp, hp = world(tmp_path)  # 赤 11:00 / 希望 15:00〜17:00 → 赤が勝つ (Q-4)
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert visits[0].time_fixed and visits[0].win == (11 * 60, 11 * 60)


def test_without_red_the_old_behaviour_is_kept(tmp_path):
    wp, hp = world(tmp_path, locked=False)
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert not visits[0].time_fixed and visits[0].win == (11 * 60, 11 * 60)  # 離れた固定は元から固定のまま


def test_moved_this_week_keeps_this_weeks_time(tmp_path):
    wp, hp = world(tmp_path, start="15:30", source="manual_week")  # Q-1
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert visits[0].time_fixed and visits[0].win == (15 * 60 + 30, 15 * 60 + 30)


def test_moved_with_other_unlocked_slot_is_not_tied_to_red(tmp_path):
    wp, hp = world(tmp_path, start="15:30", source="manual_week", has_unlocked=True)
    _d, visits, *_ = load(wp, hp, CONFIG)
    assert not visits[0].time_fixed


def test_different_time_auto_visit_is_not_fixed():
    v = {"patient_id": "p0", "visit_date": str(MON), "start_time": "15:30", "source": "auto"}
    assert not is_time_fixed(v, [{"patient_id": "p0", "weekday": 0, "start_time": "11:00"}])


def test_solver_keeps_red_time_and_staff_is_free(tmp_path):
    wp, hp = world(tmp_path)
    d, visits, _s, out, _h, _l = run(wp, hp, CONFIG, 2, {}, log=lambda m: None)
    (veh, routes, dropped), = [out[MON]]
    assert dropped == []
    (job, t), = routes["S1"]
    assert t == 11 * 60
