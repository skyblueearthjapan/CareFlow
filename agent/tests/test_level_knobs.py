"""ならしの「今回だけの条件」（rk level の引数の通り道・案の取消の操作と条件の一覧・Python の部品箱）のテスト。
架空のデータだけ（tools/leveling/fixtures）。本番に触らない。計算の本体のテストは docs/tools/week-leveling/test_knobs.py。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
FIX = AGENT / "tools" / "leveling" / "fixtures"

cli = pytest.importorskip("rk.cli")


def load(name):
    return json.loads((FIX / f"sample_{name}.json").read_text(encoding="utf-8"))


def test_knob_flags_registered_on_week_and_day():
    ap = cli.build_parser()
    a = ap.parse_args(["level", "week", "--week", "2026-10-19", "--days", "thu,fri", "--prefer-staff", "T01:150",
                       "--avoid-staff", "T02", "--keep-current", "--cancel", "ab12", "--fast",
                       "--only-staff", "T01,T02", "--window", "09:00-12:00", "--not-same-day", "A,B",
                       "--pair-staff", "A:T01", "--forbid-pair", "B:T02"])
    assert a.days == "thu,fri" and a.prefer_staff == ["T01:150"] and a.keep_current == 300 and a.fast
    assert a.seconds is None
    a = ap.parse_args(["level", "day", "--date", "2026-10-22", "--off", "T02", "--avoid-staff", "T01"])
    assert a.avoid_staff == ["T01"] and a.keep_current is None


def test_cli_passes_knobs_through_to_level(monkeypatch, capsys):
    import tools.leveling.run as run

    got = {}

    def fake_level(cfg, **kw):
        got.update(kw)
        return {"plan_path": "x.json", "out_dir": "d", "plan": {
            "title": "t", "ready": True, "blockers": [], "summary": [], "checks": [], "warnings": [], "ops": [],
            "conditions": [{"text": "T01さんへ多めに寄せる", "relax": False}]}}

    monkeypatch.setattr(run, "level", fake_level)
    assert cli.main(["level", "week", "--week", "2026-10-19", "--days", "thu,fri", "--prefer-staff", "T01:150",
                     "--keep-current", "--fast"]) == 0
    assert got["weekdays"] == [3, 4] and got["seconds"] is None
    assert got["knob_argv"] == ["--prefer-staff", "T01:150", "--keep-current", "300", "--fast"]
    assert json.loads(capsys.readouterr().out)["conditions"] == ["T01さんへ多めに寄せる"]


def test_cli_rejects_bad_knob_before_calculating(capsys):
    assert cli.main(["level", "week", "--week", "2026-10-19", "--only-staff", "T01"]) == 1
    assert "同じ数" in capsys.readouterr().err
    assert cli.main(["level", "week", "--week", "2026-10-19", "--days", "xyz"]) == 1
    assert "曜日" in capsys.readouterr().err


FAKE_SOLVER = r"""
import json, shutil, sys
args = sys.argv[1:]
out = args[args.index('--out') + 1]
open(out + '/argv.json', 'w').write(json.dumps(args))
shutil.copy(%r, out + '/result.json')
"""


def test_level_runs_solver_with_days_knobs_and_fast_seconds(tmp_path):
    from rk.config import Config
    from tools.leveling.run import level

    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(FIX / "sample_week.json", src / "week.json")
    shutil.copy(FIX / "sample_extra.json", src / "agent_extra.json")
    (src / "history.json").write_text("[]", encoding="utf-8")
    conf = {"cap_regular": 6, "regular": {"T01": "X", "T02": "X", "T03": "Y"}, "manager": {"T09": ["X", "Y"]}}
    (tmp_path / "config.json").write_text(json.dumps(conf), encoding="utf-8")
    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")
    res = level(cfg, week="2026-10-21", from_dir=str(src), config_path=str(tmp_path / "config.json"),
                weekdays=[3, 4], knob_argv=["--prefer-staff", "T01", "--fast"], include_trainee=True,
                solver_cmd=[sys.executable, "-c", FAKE_SOLVER % str(FIX / "sample_result.json")], log=lambda m: None)
    argv = json.loads((Path(res["out_dir"]) / "argv.json").read_text(encoding="utf-8"))
    assert [argv[i + 1] for i, x in enumerate(argv) if x == "--day"] == ["2026-10-22", "2026-10-23"]
    assert argv[argv.index("--seconds") + 1] == "10"  # --fast
    assert argv[argv.index("--prefer-staff") + 1] == "T01" and "--fast" in argv
    assert "-knob" in Path(res["plan_path"]).name


def make_plan(**result_over):
    from tools.leveling.plan import build_plan

    result = {**load("result"), **result_over}
    return build_plan(result, load("week"), load("extra"), config={"cap_regular": 6}, created_at="2026-10-12T10:00:00")


def test_cancel_makes_cancel_visit_week_op_not_pool():
    plan = make_plan(cancelled_visits=["v4"], dropped_visits=["v4"])
    (op,) = [o for o in plan["ops"] if o["op"] == "cancel-visit-week"]
    assert op["visit_id"] == "v4" and op["cancel"] is True and op["step"] == 2 and "急遽キャンセル" in op["reason"]
    assert "取消" in op["label"] and not [o for o in plan["ops"] if o["op"] == "return-to-pool" and o["visit_id"] == "v4"]
    rk_plan = pytest.importorskip("rk.plan")
    assert not [e for e in rk_plan.check_plan(plan) if "cancel" in e]  # 許可リストにある操作・必須の値あり


def test_cancel_of_visit_not_planned_gives_warning_only():
    plan = make_plan(cancelled_visits=["v7"])  # v7 は実施済み
    assert not [o for o in plan["ops"] if o["op"] == "cancel-visit-week"]
    assert any("急遽キャンセル" in w and "completed" in w for w in plan["warnings"])


def test_conditions_listed_and_relaxing_ones_in_warnings():
    conds = [{"text": "T01さんへ多めに寄せる（重み 100）", "relax": False},
             {"text": "正規の職員の 1 日の上限を 1 名まで超えてよい（超えた分は重み付き）", "relax": True}]
    plan = make_plan(conditions=conds)
    assert [c["text"] for c in plan["conditions"]] == [c["text"] for c in conds]
    gentle = [w for w in plan["warnings"] if w.startswith("今回だけの条件（決まりをゆるめる）")]
    assert len(gentle) == 1 and "上限" in gentle[0]
    assert any("今回だけの条件 2 件" in s for s in plan["summary"])
    rk_plan = pytest.importorskip("rk.plan")
    assert not [e for e in rk_plan.check_plan(plan) if "conditions" in e]
    assert make_plan()["conditions"] == []


# --- Python の部品箱 (api.py): 本物の計算を使うので ortools が要る ---


def _world(tmp_path):
    """計算できる小さな週: 職員 3 名（月曜 9〜18 時）・利用者 4 名（約 2km ずつ離れる）。"""
    home = (35.60, 140.10)
    week = {
        "extracted_at": "2026-10-12T09:00:00", "week_start": "2026-10-19",
        "offices": [{"id": "o1", "short_label": "稲", "lat": home[0], "lng": home[1]}],
        "ng": [], "special_marks": [], "weekly_overrides": [], "events": [],
        "patients": [{"id": f"p{i}", "name": f"利用者{i}", "weekly_pattern": {"time_type": "終日"},
                      "lat": home[0] + i * 0.02, "lng": home[1], "primary_office_id": "o1", "sex_restriction": None}
                     for i in range(4)],
        "staff": [{"id": f"S{n}", "name": f"職員{n}", "code": f"A{n}", "sex": "female"} for n in (1, 2, 3)],
        "shifts": [{"staff_id": f"S{n}", "weekday": 0, "is_on": True, "start_time": "09:00", "end_time": "18:00"}
                   for n in (1, 2, 3)],
        "visits": [{"id": f"v{i}", "patient_id": f"p{i}", "visit_date": "2026-10-19", "start_time": f"{10 + i}:00:00",
                    "end_time": f"{10 + i}:30:00", "status": "planned", "primary_staff_id": f"S{1 + i % 3}",
                    "course_staff_id": None, "course_office": None, "course_code": None} for i in range(4)],
    }
    src = tmp_path / "in"
    src.mkdir()
    (src / "week.json").write_text(json.dumps(week), encoding="utf-8")
    (src / "history.json").write_text("[]", encoding="utf-8")
    conf = {"cap_regular": 6, "regular": {"A1": "稲", "A2": "稲", "A3": "稲"}, "manager": {}}
    (tmp_path / "config.json").write_text(json.dumps(conf), encoding="utf-8")
    return src


def test_api_custom_script_end_to_end(tmp_path):
    pytest.importorskip("ortools")
    pytest.importorskip("openpyxl")
    from tools.leveling.api import load_week, solve, to_plan

    src = _world(tmp_path)
    week = load_week("2026-10-19", out_dir=tmp_path / "out", from_dir=src, config_path=tmp_path / "config.json")
    seen = []

    def rule(model, box, data):
        seen.append((str(data.day), sorted(box["index_of_visit"])))

    res = solve(week, extra_constraints=[rule], weights={"rotation": (0, 0, 0)}, no_app_check=True, seconds=2,
                notes=["テスト用: 何も縛らない自作の条件"], log=lambda m: None, knobs={"prefer": {"A1": 100}})
    assert seen and (tmp_path / "out" / "result.json").exists()
    assert any(c["text"].startswith("自作の条件") and c["relax"] for c in res["conditions"])
    assert "extra_constraints" not in res["options"] and list(res["options"]["weights"]["rotation"]) == [0, 0, 0]
    html = (tmp_path / "out" / "leveling-a4.html").read_text(encoding="utf-8")
    assert "今回だけの条件" in html and "自作の条件: テスト用" in html
    out = to_plan(res, week, save=False, log=lambda m: None)
    plan = out["plan"]
    assert any("自作の条件" in w and "決まりをゆるめる" in w for w in plan["warnings"])
    assert any("寄せる" in c["text"] for c in plan["conditions"])


def test_api_requires_notes_for_custom_and_validates_knobs(tmp_path):
    pytest.importorskip("ortools")
    from tools.leveling.api import load_week, solve
    from tools.leveling.run import LevelError

    src = _world(tmp_path)
    week = load_week("2026-10-19", out_dir=tmp_path / "out", from_dir=src, config_path=tmp_path / "config.json")
    with pytest.raises(LevelError, match="notes"):
        solve(week, extra_constraints=[lambda m, b, d: None], log=lambda m: None)
    with pytest.raises(LevelError, match="knobs"):
        solve(week, knobs={"nope": 1}, log=lambda m: None)
    with pytest.raises(LevelError, match="週の外"):
        solve(week, days=["2026-11-30"], log=lambda m: None)


# --- CUSTOM.md の 3 つの例（そのまま動くことを確かめる） ---


def cap_staff(code, n):
    """例 1: この人は 1 日 n 名まで。"""
    def rule(model, box, data):
        i = box["vehicle_of_code"].get(code)
        if i is not None:
            box["count"].CumulVar(model.End(i)).SetMax(n)
    return rule


def same_staff(pid_a, pid_b):
    """例 2: この 2 名の訪問は、同じ職員が持つ。"""
    def rule(model, box, data):
        ids = {v.patient_id: box["index_of_visit"][v.id] for v in data.visits}
        if pid_a in ids and pid_b in ids:
            model.solver().Add(model.VehicleVar(ids[pid_a]) == model.VehicleVar(ids[pid_b]))
    return rule


def finish_by(code, minutes):
    """例 3: この人は minutes（分）までに全部終える（早上がり）。"""
    def rule(model, box, data):
        i = box["vehicle_of_code"].get(code)
        if i is not None:
            box["time"].CumulVar(model.End(i)).SetMax(minutes)
    return rule


def _routes(res):
    (day,) = res["days"].values()
    names = {s["id"]: s["code"] for s in res["staff"]}
    return {names[sid]: [(vid, it["start"], it["dur"]) for it in seq for vid in it["visit_ids"]]
            for sid, seq in day["routes"].items() if seq}


def test_custom_md_examples_work(tmp_path):
    pytest.importorskip("ortools")
    pytest.importorskip("openpyxl")
    from tools.leveling.api import load_week, solve

    week = load_week("2026-10-19", out_dir=tmp_path / "out", from_dir=_world(tmp_path),
                     config_path=tmp_path / "config.json")

    def run_(rule, **kw):
        return _routes(solve(week, extra_constraints=[rule], notes=["例"], no_app_check=True, seconds=2,
                             log=lambda m: None, **kw))

    r = run_(cap_staff("A2", 2), knobs={"prefer": {"A2": 300}})
    assert len(r["A2"]) == 2 and sum(len(x) for x in r.values()) == 4
    r = run_(same_staff("p0", "p1"), knobs={"keep_current": 300})
    owner = {vid: code for code, rows in r.items() for vid, *_ in rows}
    assert owner["v0"] == owner["v1"] and len(owner) == 4
    r = run_(finish_by("A3", 12 * 60), knobs={"prefer": {"A3": 300}})
    assert all(t + dur <= 12 * 60 for _vid, t, dur in r["A3"]) and sum(len(x) for x in r.values()) == 4
