"""diagnose.py (アプリの物差しでの案の診断) のテスト。わざと悪い案を作り、見つけられるかを確かめる。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_diagnose.py -q
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose import diagnose, one_more_move  # noqa: E402
from solver import Job, Staff, Visit, travel_min  # noqa: E402

DAY = date(2026, 10, 12)  # 月曜
X1, X2 = (35.60, 140.10), (35.6005, 140.1060)  # 約 500m 離れた 2 軒 (X 地区)
Y1, Y2 = (35.69, 140.10), (35.6905, 140.1060)  # X から約 10km (Y 地区)


def visit(name, loc, start, office="稲"):
    return Visit(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        patient=name,
        office=office,
        day=DAY,
        fixed_start=start,
        dur=60,
        win=(start, start),
        time_type="固定",
        loc=loc,
        female_only=False,
        ng=set(),
        cur_staff=None,
        cur_course=None,
    )


def job(v, t=None):
    t = v.fixed_start if t is None else t
    return Job([v], [0], t, v.dur, v.win[0], v.win[1], v.win[0], v.win[1])


def staff(sid):
    return Staff(sid, sid, sid, "female", {"稲"}, False, shift={0: (540, 1080)}, code=sid)


def world():
    """S1: X1 9:00 → Y1 14:00、S2: Y2 9:00 → X2 14:00 (わざと地区をまたがせた悪い案)。
    14:00 の 2 件を入れ替えると 2 人とも同じ地区で回れる。"""
    s1, s2 = staff("S1"), staff("S2")
    a1, b1 = visit("X1さん", X1, 540), visit("Y1さん", Y1, 840)
    b2, a2 = visit("Y2さん", Y2, 540), visit("X2さん", X2, 840)
    for v, s in ((a1, s1), (b1, s1), (b2, s2), (a2, s2)):
        v.cur_staff = s.id
    routes = {s1.id: [(job(a1), 540), (job(b1), 840)], s2.id: [(job(b2), 540), (job(a2), 840)]}
    out = {DAY: ([s1, s2], routes, [])}
    d = {
        "staff": [{"id": "S1", "name": "S1"}, {"id": "S2", "name": "S2"}],
        "shifts": [
            {"staff_id": s, "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
            for s in ("S1", "S2")
        ],
        "weekly_overrides": [],
        "events": [],
    }
    return d, [a1, b1, b2, a2], out, (a1, b1, b2, a2)


def test_finds_missed_swap():
    d, _visits, out, _ = world()
    moves = one_more_move(d, out, {}, 6, 0, {}, 10, {"S1": "S1", "S2": "S2"})
    best = moves[0]
    far = travel_min(X1, Y1)
    assert far >= 25  # 10km / 20km/h = 30 分ほど
    assert best["kind"] == "見落とし"
    # 地区をまたぐ 2 回 (移動＋ゆとり) が、近所の 2 回に変わる
    near = travel_min(X1, X2) + 8 + travel_min(Y2, Y1) + 8
    assert best["saving_min"] == travel_min(X1, Y1) + 8 + travel_min(Y2, X2) + 8 - near


def test_rules_still_apply_ng_blocks_the_move():
    d, _visits, out, (a1, b1, b2, a2) = world()
    # 良くなる手 (14:00 どうし・9:00 どうしの入れ替え) がすべて NG にぶつかるようにする
    a2.ng.add("S1")
    b1.ng.add("S2")
    a1.ng.add("S2")
    b2.ng.add("S1")
    assert one_more_move(d, out, {}, 6, 0, {}, 10, {"S1": "S1", "S2": "S2"}) == []


def test_swap_between_managers_is_still_missed():
    d, _visits, out, _ = world()
    for s in out[DAY][0]:
        s.manager = True  # 2 人ともマネージャー: 入れ替えは件数を変えないので、まだ「見落とし」
    moves = one_more_move(d, out, {}, 6, 0, {}, 10, {"S1": "S1", "S2": "S2"})
    assert moves[0]["kind"] == "見落とし"


Y3 = (35.717, 140.10)  # Y1 から約 3km


def _manager_world(with_regular: bool):
    """S1 (正規): X1 9:00 → Y1 14:00。M (マネージャー): Y2 9:00。任意で S2 (正規): Y3 9:00。
    Y1 を M へ移すと移動は減るが、マネージャーの担当が増える。"""
    s1, m = staff("S1"), staff("M")
    m.manager = True
    x1, y1, y2 = visit("X1さん", X1, 540), visit("Y1さん", Y1, 840), visit("Y2さん", Y2, 540)
    x1.ng |= {"M", "S2"}
    y2.ng.add("S1")
    vehicles = [s1, m]
    routes = {"S1": [(job(x1), 540), (job(y1), 840)], "M": [(job(y2), 540)]}
    ids = ["S1", "M"]
    if with_regular:
        s2 = staff("S2")
        y3 = visit("Y3さん", Y3, 540)
        y3.ng |= {"S1", "M"}
        y2.ng.add("S2")
        vehicles.append(s2)
        routes["S2"] = [(job(y3), 540)]
        ids.append("S2")
    d = {
        "staff": [{"id": i, "name": i} for i in ids],
        "shifts": [
            {"staff_id": i, "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
            for i in ids
        ],
        "weekly_overrides": [],
        "events": [],
    }
    return d, {DAY: (vehicles, routes, [])}, {i: i for i in ids}


def test_declined_when_manager_would_take_more():
    d, out, names = _manager_world(with_regular=False)
    moves = one_more_move(d, out, {}, 6, 0, {}, 10, names)
    assert moves, "Y1 を M へ移す手が見つかるはず"
    assert all(m["kind"] == "わざと選ばなかった" for m in moves)
    y1 = [m for m in moves if m["move"].startswith("Y1さん")]
    assert y1 and "マネージャーの担当が増える" in y1[0]["why_not"]


def test_missed_is_not_hidden_by_bigger_declined_move():
    """同じ訪問で「M へ (−28 分・わざと選ばなかった)」と「S2 へ (−21 分・見落とし)」があるとき、見落としを残す。"""
    d, out, names = _manager_world(with_regular=True)
    moves = one_more_move(d, out, {}, 6, 0, {}, 10, names)
    y1 = [m for m in moves if m["move"].startswith("Y1さん")]
    assert len(y1) == 1
    assert y1[0]["kind"] == "見落とし" and y1[0]["move"].endswith(" S2")


def test_good_plan_has_nothing_left():
    d, _visits, out, (a1, b1, b2, a2) = world()
    s1, s2 = out[DAY][0]
    out[DAY] = ([s1, s2], {"S1": [(job(a1), 540), (job(a2), 840)], "S2": [(job(b2), 540), (job(b1), 840)]}, [])
    assert one_more_move(d, out, {}, 6, 0, {}, 10, {"S1": "S1", "S2": "S2"}) == []


def test_diagnose_totals_and_match():
    d, visits, out, _ = world()
    r = diagnose(d, visits, out, {}, 6, 0, {}, {"S1": "S1", "S2": "S2"}, log=lambda _m: None)
    assert r["yardstick_match"]
    assert r["cur"]["travel_minutes"] == r["new"]["travel_minutes"] == travel_min(X1, Y1) + travel_min(Y2, X2)
    assert r["new"]["buffer_minutes"] == 16
    assert r["missed"] >= 1


def test_search_skipped_when_plan_breaks_rules():
    d, visits, out, (a1, b1, b2, a2) = world()
    a1.ng.add("S1")  # 案そのものが NG にぶつかる
    r = diagnose(d, visits, out, {}, 6, 0, {}, {"S1": "S1", "S2": "S2"}, log=lambda _m: None)
    assert r["moves_checked"] is False and r["moves"] == [] and r["missed"] == 0


def test_buffer_only_saving_gets_a_reason():
    """移動は同じで、ゆとりだけ減る手 = 道具の点数では得にならない →「わざと選ばなかった」に理由が付く。

    S1: P 9:00 → Q 11:00 (P から 200m・移動 1 分) → P の同じ建物 13:00。S2: Q2 15:00 (Q から 650m・2 分)。
    Q を S2 へ移すと S1 は 移動 2＋ゆとり 16 減り、S2 は 移動 2＋ゆとり 8 増える = 移動は ±0・ゆとりだけ 8 減る。"""
    s1, s2 = staff("S1"), staff("S2")
    p, p_same, q, q2 = (35.60, 140.10), (35.6001, 140.1001), (35.6018, 140.10), (35.60765, 140.10)
    assert travel_min(p, q) == 1 and travel_min(q, p_same) == 1 and travel_min(q, q2) == 2
    a, b, c, dd = visit("A", p, 540), visit("B", q, 660), visit("C", p_same, 780), visit("D", q2, 900)
    routes = {"S1": [(job(a), 540), (job(b), 660), (job(c), 780)], "S2": [(job(dd), 900)]}
    d = {
        "staff": [{"id": "S1", "name": "S1"}, {"id": "S2", "name": "S2"}],
        "shifts": [
            {"staff_id": i, "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
            for i in ("S1", "S2")
        ],
        "weekly_overrides": [],
        "events": [],
    }
    moves = one_more_move(d, {DAY: ([s1, s2], routes, [])}, {}, 6, 0, {}, 5, {"S1": "S1", "S2": "S2"})
    mb = [m for m in moves if m["move"].startswith("B ")]
    assert len(mb) == 1 and mb[0]["saving_min"] == 8
    assert mb[0]["kind"] == "わざと選ばなかった" and mb[0]["tool_score_delta"] == 0
    assert "ゆとり" in mb[0]["why_not"]
