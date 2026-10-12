"""「今回だけの条件」(level_knobs / solver の opts) のテスト。架空の職員 3 名・月火の訪問だけを使う。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_knobs.py -q
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import level_knobs  # noqa: E402
from solver import find_patient, run  # noqa: E402

MON, TUE = date(2026, 10, 12), date(2026, 10, 13)
HOME = (35.60, 140.10)
CONFIG = {"regular": {"A1": "稲", "A2": "稲", "A3": "稲"}, "manager": {}, "cap_regular": 6}


def world(tmp_path, visits):
    """visits = [(日, 開始, 終了, 今の担当コード|None)]。利用者は 1 訪問 1 名・約 2km ずつ離す。"""
    d = {
        "week_start": str(MON),
        "offices": [{"id": "o1", "short_label": "稲", "lat": HOME[0], "lng": HOME[1]}],
        "ng": [],
        "patients": [
            {"id": f"p{i}", "name": f"利用者{i} 太郎", "weekly_pattern": {"time_type": "終日"},
             "lat": HOME[0] + i * 0.02, "lng": HOME[1], "primary_office_id": "o1", "sex_restriction": None}
            for i in range(len(visits))
        ],
        "staff": [{"id": f"S{n}", "name": f"職員{n}", "code": f"A{n}", "sex": "female"} for n in (1, 2, 3)],
        "shifts": [{"staff_id": f"S{n}", "weekday": wd, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
                   for n in (1, 2, 3) for wd in (0, 1)],
        "weekly_overrides": [],
        "events": [],
        "visits": [
            {"id": f"v{i}", "patient_id": f"p{i}", "visit_date": str(day), "start_time": a, "end_time": b,
             "primary_staff_id": cur, "course_staff_id": None, "course_office": None, "course_code": None}
            for i, (day, a, b, cur) in enumerate(visits)
        ],
    }
    wp, hp = tmp_path / "week.json", tmp_path / "history.json"
    wp.write_text(json.dumps(d), encoding="utf-8")
    hp.write_text("[]", encoding="utf-8")
    return wp, hp


def sub(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return p


def solve(tmp_path, visits, days=None, **opts):
    wp, hp = world(tmp_path, visits)
    d, _v, staff, out, _h, _l = run(wp, hp, CONFIG, 2, opts, days, log=lambda m: None)
    by_visit = {}
    for day, (_veh, routes, _dr) in out.items():
        for sid, seq in routes.items():
            for j, t in seq:
                for v, off in zip(j.visits, j.offsets):
                    by_visit[v.id] = (staff[sid].code, day, t + off, v.dur)
    return d, out, by_visit


FOUR = [(MON, "10:00", "10:30", "S1"), (MON, "11:00", "11:30", "S2"), (MON, "14:00", "14:30", "S3"),
        (MON, "15:00", "15:30", "S1")]


def test_days_only_solves_those_days(tmp_path):
    _d, out, by = solve(tmp_path, FOUR + [(TUE, "10:00", "10:30", "S1")], days={TUE})
    assert set(out) == {TUE} and set(by) == {"v4"}


def test_prefer_staff_pulls_visits(tmp_path):
    _d, _o, by = solve(tmp_path, FOUR, prefer={"A3": 200})
    assert {c for c, *_ in by.values()} == {"A3"}


def test_avoid_staff_only_if_necessary(tmp_path):
    _d, _o, by = solve(tmp_path, FOUR, avoid={"A1": 600})
    assert "A1" not in {c for c, *_ in by.values()}
    # どうしても要る (他の 2 人が休み) ときは使う
    _d, _o, by = solve(sub(tmp_path, "x"), FOUR, avoid={"A1": 600},
                       off=[("A2", None), ("A3", None)])
    assert {c for c, *_ in by.values()} == {"A1"}


def test_off_still_never_used(tmp_path):
    _d, _o, by = solve(tmp_path, FOUR, off=[("A1", None)], prefer={"A1": 500})
    assert "A1" not in {c for c, *_ in by.values()}


def test_keep_current_minimises_churn(tmp_path):
    _d, _o, by = solve(tmp_path, FOUR, keep_current=300)
    assert [by[f"v{i}"][0] for i in range(4)] == ["A1", "A2", "A3", "A1"]


def test_cancel_removes_visit_and_is_recorded(tmp_path):
    d, _o, by = solve(tmp_path, FOUR, cancel=["v1"])
    assert "v1" not in by and d["cancelled_visits"] == ["v1"] and d["dropped_visits"] == ["v1"]
    with pytest.raises(ValueError, match="見つかりません"):
        solve(sub(tmp_path, "y"), FOUR, cancel=["zzzz"])


def test_only_staff_window_blocks_others(tmp_path):
    visits = [(MON, "10:00", "10:30", "S2"), (MON, "10:30", "11:00", "S3"), (MON, "14:00", "14:30", "S2")]
    _d, _o, by = solve(tmp_path, visits, only_staff=[{"codes": ["A1"], "window": [10 * 60, 12 * 60]}])
    for code, _day, t, dur in by.values():
        if t < 12 * 60 and t + dur > 10 * 60:
            assert code == "A1"
    assert len(by) == 3


def test_not_same_day_splits_two_patients(tmp_path):
    _d, _o, together = solve(tmp_path, FOUR, prefer={"A3": 200})
    assert together["v0"][0] == together["v1"][0]  # 条件が無ければ同じ人に寄る
    _d, _o, by = solve(sub(tmp_path, "n"), FOUR, prefer={"A3": 200}, not_same_day=[["p0", "p1"]])
    assert by["v0"][0] != by["v1"][0] and len(by) == 4


def test_pair_and_forbid_staff(tmp_path):
    _d, _o, by = solve(tmp_path, FOUR, prefer={"A3": 200}, pair_staff=[["利用者1", "A2"]], forbid_pair=[["p0", "A3"]])
    assert by["v1"][0] == "A2" and by["v0"][0] != "A3"


def test_unknown_codes_and_patients_are_errors(tmp_path):
    with pytest.raises(ValueError, match="職員コード ZZ"):
        solve(tmp_path, FOUR, prefer={"ZZ": 100})
    with pytest.raises(ValueError, match="利用者が見つかりません"):
        solve(sub(tmp_path, "m"), FOUR, pair_staff=[["nobody", "A1"]])


def test_weights_override_and_extra_constraints(tmp_path):
    calls = []

    def only_v0_with_a2(model, box, data):
        calls.append(data.day)
        idx = box["index_of_visit"].get("v0")
        if idx is not None:
            for code, i in box["vehicle_of_code"].items():
                if code != "A2":
                    model.VehicleVar(idx).RemoveValue(i)

    _d, _o, by = solve(tmp_path, FOUR, extra_constraints=[only_v0_with_a2], weights={"rotation": (0, 0, 0)})
    assert by["v0"][0] == "A2" and calls
    with pytest.raises(ValueError, match="重みの名前"):
        solve(sub(tmp_path, "w"), FOUR, weights={"nope": 1})


def test_find_patient_ambiguous():
    d = {"patients": [{"id": "ab1", "name": "山田 花"}, {"id": "ab2", "name": "山田 月"}]}
    assert find_patient(d, "山田花") == "ab1"
    with pytest.raises(ValueError, match="2 名"):
        find_patient(d, "山田")


# --- 引数の解釈・日本語の説明 ---


def parse(argv):
    ap = argparse.ArgumentParser()
    level_knobs.add_args(ap)
    return ap.parse_args(argv)


def test_parse_args_to_opts():
    a = parse(["--prefer-staff", "A3:150", "--avoid-staff", "A1", "--keep-current", "--cancel", "AbC",
               "--only-staff", "A1,A2", "--window", "09:00-12:00", "--not-same-day", "x,y",
               "--pair-staff", "x:A1", "--forbid-pair", "y:A2", "--fast"])
    o = level_knobs.from_args(a)
    assert o["prefer"] == {"A3": 150} and o["avoid"] == {"A1": 600} and o["keep_current"] == 300
    assert o["cancel"] == ["abc"] and o["only_staff"] == [{"codes": ["A1", "A2"], "window": [540, 720]}]
    assert o["not_same_day"] == [["x", "y"]] and o["pair_staff"] == [["x", "A1"]] and o["fast"] is True
    assert parse(["--keep-current", "50"]).keep_current == 50
    assert level_knobs.from_args(parse([])) == {}
    json.dumps(o)  # JSON にできる


def test_parse_errors_and_weekdays():
    assert level_knobs.parse_weekdays("thu,fri") == [3, 4] and level_knobs.parse_weekdays("木、金") == [3, 4]
    with pytest.raises(ValueError):
        level_knobs.parse_weekdays("xyz")
    with pytest.raises(ValueError, match="同じ数"):
        level_knobs.from_args(parse(["--only-staff", "A1"]))
    with pytest.raises(ValueError, match="HH:MM"):
        level_knobs.from_args(parse(["--only-staff", "A1", "--window", "9-12"]))
    with pytest.raises(ValueError, match="A,B"):
        level_knobs.from_args(parse(["--not-same-day", "x"]))


def test_describe_lists_every_knob_and_flags_relaxing_ones():
    opts = {"allow_over": 1, "prefer": {"A3": 100}, "avoid": {"A1": 600}, "keep_current": 300, "fast": True,
            "only_staff": [{"codes": ["A1"], "window": [540, 720]}], "cancel": ["abc"],
            "not_same_day": [["x", "y"]], "pair_staff": [["x", "A1"]], "forbid_pair": [["y", "A2"]],
            "off": [("A2", date(2026, 10, 13))], "weights": {"rotation": 0}}
    rows = level_knobs.describe(opts, {"A3": "鈴木"}, days=[MON], custom=["午後は A2 を外す"])
    text = "\n".join(r["text"] for r in rows)
    for needle in ("鈴木さん", "上限を 1 名まで", "今の担当", "09:00〜12:00", "急遽キャンセル", "同じ日に両方持たない",
                   "必ず", "にしない", "10/13", "計算した日", "自作の条件", "重みを変えた", "計算時間"):
        assert needle in text, needle
    relax = {r["text"] for r in rows if r["relax"]}
    assert any("上限" in t for t in relax) and any("自作" in t for t in relax) and any("計算時間" in t for t in relax)
    assert not any("鈴木さんへ多めに" in t for t in relax)
