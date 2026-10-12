"""コースのならし: 問題の組み立てと数え方（架空のデータ tools/courseplan/fixtures だけ・本番に触らない）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from tools.courseplan import model as M  # noqa: E402

FIX = AGENT / "tools" / "courseplan" / "fixtures"


def raw():
    return json.loads((FIX / "sample_raw.json").read_text(encoding="utf-8"))


def slot(p, key):
    return next(s for s in p["slots"] if s["key"] == key)


def test_one_course_per_person_matched_by_history():
    p = M.build_problem(raw(), "甲", 0)
    assert [c["label"] for c in p["courses"]] == ["甲A", "甲B", "甲M"]  # 臨時は使わない・M はマネージャー
    a, b, m = p["courses"]
    assert (a["staff"]["code"], b["staff"]["code"], m["staff"]["code"]) == ("T01", "T02", "T03")
    assert m["manager"] and m["cap"] == 99 and a["cap"] == 6 and not a["manager"]
    assert a["events"] == [[17 * 60, 17 * 60 + 30]] and a["shift"] == [9 * 60, 18 * 60]
    # 4 人目（T04）にはコースが残っていない → 気を付ける点に出す。新人（T06）はコースを持たない
    assert any("コースが足りません" in n for n in p["notes"])
    assert all(c["staff"]["code"] != "T06" for c in p["courses"])


def test_fewer_people_than_courses_moves_slots_off_unstaffed_course():
    p = M.build_problem(raw(), "甲", 0, {"staff_codes": ["T01", "T03"]})
    assert [c["label"] for c in p["courses"]] == ["甲A", "甲M"]
    assert any("コース B の枠は別のコースへ移す前提" in n for n in p["notes"])
    m = M.evaluate(p, M.current_assignment(p))
    assert m["unstaffed"] == 4  # B の枠 4 つは人のいないコース
    assert M.badness(m)[0] >= 4


def test_scope_is_office_courses_and_unassigned_slots_of_office_patients():
    p = M.build_problem(raw(), "甲", 0)
    keys = {s["key"] for s in p["slots"]}
    assert "F12" not in keys  # 別の拠点のコースの枠
    assert "F5" in keys and slot(p, "F5")["cur_course"] is None and not slot(p, "F5")["cur_open"]


def test_windows_red_pin_preference_and_no_preference():
    p = M.build_problem(raw(), "甲", 0)
    assert slot(p, "F4")["win"] == [630, 630] and slot(p, "F4")["why"] == "赤ピン"  # 赤 = 時刻は動かさない
    assert slot(p, "F3")["win"] == [540, 720]  # 時間帯 9:00〜12:00
    assert slot(p, "F10")["win"] == [960, 960]  # 希望が固定
    assert slot(p, "F11")["win"] == [660, 660] and slot(p, "F11")["why"] == "希望なし"
    free = M.build_problem(raw(), "甲", 0, {"no_pref": "free", "red": "ignore"})
    assert slot(free, "F11")["win"] == [540, M.BUSINESS_END - 35]
    assert slot(free, "F4")["win"] == [600, 660]  # 赤を外すと希望 10:00〜11:00


def test_allowed_courses_follow_ng_and_female_only():
    p = M.build_problem(raw(), "甲", 0)
    assert slot(p, "F4")["allowed"] == ["tA", "tM"]  # 女性限定 → 男性の B は不可
    assert slot(p, "F10")["allowed"] == ["tB", "tM"]  # NG → A は不可


def test_two_person_slots_are_paired():
    p = M.build_problem(raw(), "甲", 0)
    assert slot(p, "F8")["pair"] == slot(p, "F9")["pair"] == "P7"
    assert slot(p, "F1")["pair"] is None


def test_evaluate_current_counts_problems():
    p = M.build_problem(raw(), "甲", 0)
    m = M.evaluate(p, M.current_assignment(p))
    assert m["conflicts"] == 2  # A 9:30 に 2 名・B 10:30→11:00 が間に合わない
    assert m["ng_or_sex"] == 1 and m["no_course"] == 1
    assert m["regular_counts"] == [5, 4] and m["count_spread"] == 1
    assert m["travel_min"] > 0 and m["lunch_short"] == 0


def test_same_building_pair_is_not_a_conflict():
    p = M.build_problem(raw(), "甲", 0)
    a = {s["key"]: {"course": None, "start": s["start"]} for s in p["slots"]}
    a["F6"] = {"course": "tA", "start": 13 * 60 + 30}
    a["F7"] = {"course": "tA", "start": 13 * 60 + 30}
    m = M.evaluate(p, a)
    assert m["conflicts"] == 0 and m["courses"][0]["travel_min"] == 0


def test_changes_and_tidy_reverts_needless_changes():
    p = M.build_problem(raw(), "甲", 0)
    good = {"F1": ("tA", 570), "F4": ("tA", 630), "F6": ("tA", 790), "F7": ("tA", 790), "F8": ("tA", 900),
            "F3": ("tB", 570), "F11": ("tB", 660), "F5": ("tB", 840), "F9": ("tB", 900), "F10": ("tB", 960)}
    assign = {k: {"course": c, "start": t} for k, (c, t) in good.items()}
    before = M.changes(p, assign)
    assert {c["key"] for c in before} == {"F3", "F4", "F5", "F6", "F7"}
    tidied, n = M.tidy(p, assign)
    after = {c["key"] for c in M.changes(p, tidied)}
    assert n == 2 and after == {"F3", "F4", "F5"}  # 13:10 へ動かす必要は無い → 13:30 に戻す
    m = M.evaluate(p, tidied)
    assert M.badness(m) == (0, 0)


def test_operating_weekdays_formats():
    assert M.operating({"operating_weekdays": [0, 1, 2, 3, 4]}, 5) is False
    assert M.operating({"operating_weekdays": ["mon", "sat"]}, 5) is True
    assert M.operating({"operating_weekdays": None}, 5) is True
