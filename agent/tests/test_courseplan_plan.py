"""コースのならし: 結果 → 案（pfv-update の全件・ready:false）。架空のデータだけ・本番に触らない。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from rk.opsbase import load_all  # noqa: E402
from rk.plan import check_plan  # noqa: E402
from tools.courseplan import model as M  # noqa: E402
from tools.courseplan.plan import RED_CLEANUP_BLOCKER, build_plan  # noqa: E402

FIX = AGENT / "tools" / "courseplan" / "fixtures"
GOOD = {"F1": ("tA", 570), "F4": ("tA", 630), "F6": ("tA", 810), "F7": ("tA", 810), "F8": ("tA", 900),
        "F3": ("tB", 570), "F11": ("tB", 660), "F5": ("tB", 840), "F9": ("tB", 900), "F10": ("tB", 960)}


def make(red="keep", dropped=()):
    raw = json.loads((FIX / "sample_raw.json").read_text(encoding="utf-8"))
    p = M.build_problem(raw, "甲", 0, {"red": red})
    sol = {"office": "甲", "weekday": 0, "assign": {k: {"course": c, "start": t} for k, (c, t) in GOOD.items()},
           "dropped": list(dropped), "lunch": 45}
    return raw, p, build_plan(raw, [p], [sol], created_at="2026-10-12T10:00:00")


def op_for(plan, pid):
    return next(o for o in plan["ops"] if o["patient_id"] == pid)


def test_one_pfv_update_per_changed_patient_with_full_items():
    _, _, plan = make()
    assert plan["kind"] == "course-plan" and plan["ready"] is False  # 赤の整理が未了のうちは反映しない
    assert {o["op"] for o in plan["ops"]} == {"pfv-update"}
    assert sorted(o["patient_id"] for o in plan["ops"]) == ["P2", "P3", "P4"]
    for o in plan["ops"]:
        assert o["mode"] == "normal" and o["change_scope"] == "pattern_only" and o["label"]
    reg = load_all()
    for o in plan["ops"]:  # rk の検査を通る形（架空の ID だけ UUID の形に置き換える）
        o = {**o, "patient_id": "00000000-0000-4000-8000-000000000001"}
        assert reg.get("pfv-update").validate(o) == []


def test_items_keep_other_weekdays_and_red_pin_as_is():
    raw, _, plan = make()
    o = op_for(plan, "P3")
    (it,) = o["items"]
    assert it["course_template_id"] == "tA" and it["start_time"] == "10:30"
    assert it["movability"] == "locked" and it["is_pinned"] is True  # 赤はそのまま写す
    # P1 は変えていない → 案に入らない（水曜の枠もそのまま）
    assert all(o["patient_id"] != "P1" for o in plan["ops"])
    o = op_for(plan, "P4")
    assert o["items"][0]["course_template_id"] == "tB" and o["items"][0]["start_time"] == "14:00"


def test_red_cleanup_is_the_only_blocker_by_default_and_red_course_change_is_ok():
    _, _, plan = make()
    assert plan["blockers"] == [RED_CLEANUP_BLOCKER]  # 赤の枠のコース替え（P3）は止める理由にならない
    assert any("反映できません" in e for e in check_plan(plan))  # ready:false は rk plan check が断る


def test_ready_after_red_cleanup_done():
    raw, p, _ = make()
    sol = {"office": "甲", "weekday": 0, "assign": {k: {"course": c, "start": t} for k, (c, t) in GOOD.items()},
           "dropped": [], "lunch": 45}
    plan = build_plan(raw, [p], [sol], created_at="2026-10-12T10:00:00", red_cleanup_done=True)
    assert plan["blockers"] == [] and plan["ready"] is True


def test_dropped_slots_are_blockers_and_warnings():
    _, _, plan = make(dropped=["F10"])
    assert any("入りきらない枠が 1 件" in b for b in plan["blockers"])
    assert any("入りきらないので今のまま" in w for w in plan["warnings"])


def test_metrics_and_timelines_before_after():
    _, p, plan = make()
    (m,) = plan["metrics"]
    assert m["before"]["conflicts"] == 2 and m["after"]["conflicts"] == 0
    assert m["after"]["ng_or_sex"] == 0 and m["after"]["no_course"] == 0 and m["changes"] == 3
    titles = [t["date"] for t in plan["timelines"]]
    assert titles == ["0 月曜 いまの型", "0 月曜 案"] and sorted(titles) == titles
    after = plan["timelines"][1]
    changed = [b for r in after["rows"] for b in r["blocks"] if b["changed"]]
    assert len(changed) == 3
    assert any(r["name"] == "コースなし" for r in plan["timelines"][0]["rows"])
    assert plan["summary"][0].startswith("変える枠 3（コース 3・時刻 0）／患者 3 名")


def test_report_renders_a3(tmp_path):
    from rk.config import Config
    from rk.report import write_report

    _, _, plan = make()
    cfg = Config(reports_dir=tmp_path)
    path = write_report(cfg, plan, a3=True, out=tmp_path / "a3.html")
    html = path.read_text(encoding="utf-8")
    assert "いまの型" in html and "案" in html and "固定訪問の枠" in html
