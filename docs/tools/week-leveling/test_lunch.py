"""昼休み (PO 2026-10-03: 11:30〜13:30 に 45 分を目指す・入らない日だけ 30 分・予定の時間は昼休みに数えない) のテスト。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_lunch.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import check, lunch_free  # noqa: E402
from solver import LUNCH_WIN, Job, Visit, lunch_starts, run  # noqa: E402

DAY = date(2026, 10, 12)  # 月曜
HOME = (35.60, 140.10)


def hm(h, m=0):
    return h * 60 + m


def job(start, dur, loc=HOME):
    v = Visit("v", "p", "p", "稲", DAY, start, dur, (start, start), "固定", loc, False, set(), None, None)
    return Job([v], [0], start, dur, start, start, start, start)


# --- 検査側 (report.lunch_free) ---


def test_lunch_free_without_events_is_the_longest_gap():
    seq = [(job(hm(11), 45), hm(11)), (job(hm(12, 30), 60), hm(12, 30))]
    assert lunch_free(seq) == 45  # 11:45〜12:30


def test_meeting_time_is_not_lunch():
    # 11:45〜13:15 が空き。12:00〜12:30 の会議は昼休みに数えない → 12:30〜13:15 の 45 分
    seq = [(job(hm(11), 45), hm(11)), (job(hm(13, 15), 45), hm(13, 15))]
    assert lunch_free(seq) == 90
    assert lunch_free(seq, [(hm(12), hm(12, 30))]) == 45


def test_travel_still_needs_time_outside_meeting():
    far = (35.69, 140.10)  # 約 10km = 移動 30 分＋ゆとり 8 分
    seq = [(job(hm(11), 45), hm(11)), (job(hm(13, 15), 45, far), hm(13, 15))]
    # 空き 90 分 − 会議 30 分 = 60 分を、移動 38 分と昼休みで分ける → 22 分
    assert lunch_free(seq, [(hm(12), hm(12, 30))]) == 22


def test_check_meeting_filling_the_window_is_a_warning_not_an_error():
    d = {
        "shifts": [{"staff_id": "S1", "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}],
        "weekly_overrides": [],
        "events": [{"staff_id": "S1", "date": str(DAY), "start": "11:30", "end": "13:30"}],
    }
    from solver import Staff

    s = Staff("S1", "S1", "S1", "female", {"稲"}, False, shift={0: (540, 1080)}, code="S1")
    out = {DAY: ([s], {"S1": [(job(hm(9), 60), hm(9)), (job(hm(15), 60), hm(15))]}, [])}
    errors, warnings = check(d, out, 6, 0)
    assert errors == []
    assert any("予定のため昼休み" in w for w in warnings)


# --- 計算側 (solver.run: 45 分 → 入らない日だけ 30 分) ---


def write_world(tmp_path, visits, events=(), spread=0.00001):
    """職員 1 名 (9:00〜18:00)・利用者は spread 度ずつ北へ (既定は同じ建物で移動 0 分)・時刻は固定。"""
    d = {
        "week_start": str(DAY),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {
                "id": f"p{i}",
                "name": f"p{i}",
                "weekly_pattern": {"time_type": "固定"},
                "lat": HOME[0] + i * spread,
                "lng": HOME[1],
                "primary_office_id": "o1",
                "sex_restriction": None,
            }
            for i in range(len(visits))
        ],
        "staff": [{"id": "S1", "name": "S1", "code": "C1", "sex": "female"}],
        "shifts": [{"staff_id": "S1", "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}],
        "weekly_overrides": [],
        "events": [
            {"staff_id": "S1", "date": str(DAY), "end_date": None, "start": a, "end": b} for a, b in events
        ],
        "visits": [
            {
                "id": f"v{i}",
                "patient_id": f"p{i}",
                "visit_date": str(DAY),
                "start_time": a,
                "end_time": b,
                "primary_staff_id": None,
                "course_office": None,
                "course_code": None,
            }
            for i, (a, b) in enumerate(visits)
        ],
    }
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return wp, hp


CONFIG = {"regular": {"C1": "稲"}, "manager": {}, "cap_regular": 6}


def solve(tmp_path, visits, events=(), spread=0.00001):
    wp, hp = write_world(tmp_path, visits, events, spread)
    d, _v, _s, out, _h, lunch = run(wp, hp, CONFIG, 2, log=lambda m: None)
    _veh, routes, dropped = out[DAY]
    return d, out, lunch[DAY], sum(len(seq) for seq in routes.values()), dropped


def test_45_minutes_when_it_fits(tmp_path):
    _d, _o, lunch, placed, dropped = solve(tmp_path, [("11:00", "11:45"), ("12:30", "13:30")])
    assert (lunch, placed, dropped) == (45, 2, [])


def test_falls_back_to_30_only_when_45_drops_a_visit(tmp_path):
    # 空きは 12:30〜13:00 の 30 分だけ → 45 分では 1 件入らない → この日は 30 分で全部入る
    d, out, lunch, placed, dropped = solve(tmp_path, [("11:30", "12:30"), ("13:00", "14:00")])
    assert (lunch, placed, dropped) == (30, 2, [])
    errors, warnings = check(d, out, 6, 0)
    assert errors == []
    assert any("30 分（45 分未満）" in w for w in warnings)


def test_meeting_and_lunch_do_not_share_time(tmp_path):
    # 空き 11:45〜13:15 (90 分) に会議 12:00〜12:30。計算は会議の前後 15 分も昼休みと別に数えるので
    # (60 分＋昼休み) が 90 分に収まる 30 分に下がる。検査は会議そのものだけを除くので 45 分と数える。
    d, out, lunch, placed, dropped = solve(
        tmp_path, [("11:00", "11:45"), ("13:15", "14:00")], events=[("12:00", "12:30")]
    )
    assert (lunch, placed, dropped) == (30, 2, [])
    assert check(d, out, 6, 0)[0] == []


def test_lunch_starts_avoid_the_meeting():
    starts = lunch_starts(45, [(hm(12), hm(12, 30))])
    # 11:30〜12:00 は 30 分しかないので 45 分は会議の後 (12:30〜12:45 開始) だけ
    assert starts == list(range(hm(12, 30), LUNCH_WIN[1] - 45 + 1))
    assert lunch_starts(30, [(hm(12), hm(12, 30))])[0] == LUNCH_WIN[0]
    assert lunch_starts(30, [(hm(11, 30), hm(13, 30))]) == []


def test_solver_never_puts_lunch_inside_the_meeting(tmp_path):
    # レビューの再現: 11:00〜12:00・15:00〜16:00、会議 12:20〜13:15。
    # 昼の窓で会議の外の空きは 12:00〜12:20 の 20 分だけ → 昼休み 30 分は取れない。
    # 計算が「45 分取れた」と言いながら検査で問題になることがあってはならない。
    d, out, _lunch, placed, dropped = solve(
        tmp_path, [("11:00", "12:00"), ("15:00", "16:00")], events=[("12:20", "13:15")]
    )
    errors = check(d, out, 6, 0)[0]
    assert [e for e in errors if "昼休み" in e] == []
    assert (placed, sum(j.count for j in dropped)) == (1, 1)  # 30 分も取れないので 1 件は入らない


def test_tie_keeps_45(tmp_path):
    # 45 分でも 30 分でも 1 件入らない (別の建物で同じ時刻に 2 件・職員 1 名) → 45 分のまま
    _d, _o, lunch, placed, dropped = solve(
        tmp_path, [("10:00", "11:00"), ("10:00", "11:00")], spread=0.01
    )
    assert (lunch, placed, len(dropped)) == (45, 1, 1)


def test_travel_outside_the_window_still_counts_without_meetings():
    far = (35.69, 140.10)  # 移動 30 分＋ゆとり 8 分
    seq = [(job(hm(10), 60), hm(10)), (job(hm(12, 30), 60, far), hm(12, 30))]
    # 空き 11:00〜12:30 (90 分)。移動 38 分を 11:00〜11:38 に済ませれば、11:38〜12:30 の 52 分が昼休み
    assert lunch_free(seq) == 52


def test_meeting_over_the_whole_window_does_not_stop_the_day(tmp_path):
    _d, _o, _lunch, placed, dropped = solve(
        tmp_path, [("09:30", "10:30"), ("15:00", "16:00")], events=[("11:30", "13:30")]
    )
    assert (placed, dropped) == (2, [])
