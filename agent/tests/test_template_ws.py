"""コースの作業場（rk template）。架空のデータだけ・本番に触らない。計算は偽のプロセスで流す（OR-Tools があれば本物も 1 本）。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
FIX = AGENT / "tools" / "courseplan" / "fixtures"

from rk import cli  # noqa: E402
from rk.config import Config  # noqa: E402
from rk.opsbase import load_all  # noqa: E402
from tools.courseplan.plan import RED_CLEANUP_BLOCKER  # noqa: E402
from tools.template_ws import draft as D  # noqa: E402
from tools.template_ws import ops  # noqa: E402
from tools.template_ws import problems as P  # noqa: E402
from tools.template_ws import report  # noqa: E402
from tools.template_ws.draft import TemplateError  # noqa: E402

# 今ある枠は今のまま（人のいないコース・コースなしは最初のコースへ）、新しい枠と手で動かした枠は許されたコースの最初・範囲の始まりへ
FAKE = r"""
import json, sys
a = sys.argv
inp, out = a[a.index('--in') + 1], a[a.index('--out') + 1]
ps = json.load(open(inp, encoding='utf-8'))
sols = []
for p in ps:
    ids = [c['id'] for c in p['courses']]
    asg, dropped = {}, []
    for s in p['slots']:
        if s.get('new') or s.get('held'):
            if not s['allowed']:
                dropped.append(s['key']); continue
            asg[s['key']] = {'course': s['allowed'][0], 'start': s['win'][0]}
        else:
            asg[s['key']] = {'course': s['cur_course'] if s['cur_course'] in ids else ids[0], 'start': s['start']}
    sols.append({'office': p['office'], 'weekday': p['weekday'], 'assign': asg, 'dropped': dropped, 'lunch': 45, 'tries': []})
json.dump(sols, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
"""
FAKE_CMD = [sys.executable, "-c", FAKE]

CANDS = [
    {"id": "PN1", "code": "P950", "name": "新規 花子", "status": "pending", "sex_restriction": "female_only",
     "lat": 35.61, "lng": 140.11, "primary_office_id": "o1", "requires_multiple_staff": False,
     "weekly_pattern": {"frequency_per_week": 1, "preferred_weekdays": ["Mon", "Wed"], "time_type": "午前",
                        "service_minutes": 40}},
    {"id": "PN2", "code": "P951", "name": "新規 次郎", "status": "active", "sex_restriction": None,
     "lat": 35.62, "lng": 140.10, "primary_office_id": "o1", "requires_multiple_staff": True,
     "weekly_pattern": {"preferred_weekdays": ["Tue"], "time_type": "時間帯", "preferred_start": "13:00",
                        "preferred_end": "14:00"}},
    {"id": "PN3", "code": "P952", "name": "新規 三子", "status": "active", "sex_restriction": None,
     "lat": 35.62, "lng": 140.10, "primary_office_id": "o1", "requires_multiple_staff": False,
     "weekly_pattern": {"time_type": "終日"}},  # 回数も曜日も無い → 置けない
    {"id": "PN9", "code": "P959", "name": "他拠点 九子", "status": "active", "sex_restriction": None,
     "lat": 35.7, "lng": 140.3, "primary_office_id": "o2", "requires_multiple_staff": False,
     "weekly_pattern": {"frequency_per_week": 1, "preferred_weekdays": ["Mon"]}},
]


@pytest.fixture
def env(tmp_path):
    raw = json.loads((FIX / "sample_raw.json").read_text(encoding="utf-8"))
    raw["candidates"] = json.loads(json.dumps(CANDS))
    rp = tmp_path / "raw.json"
    rp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")
    return cfg, raw, rp


def mk(env, name="d1"):
    cfg, raw, rp = env
    return ops.new(cfg, name, office="甲", from_raw=str(rp), log=lambda m: None)


def solve(cfg, name="d1", **kw):
    return ops.solve(cfg, name, seconds=1, solver_cmd=FAKE_CMD, log=lambda m: None, **kw)


def same_master(raw):
    return lambda sql: [{"x": raw["pfv"]}]


# ---------------------------------------------------------------------------


def test_cli_registered():
    p = cli.build_parser()
    a = p.parse_args(["template", "whatif", "d1", "--staff-count", "mon=2", "--add-patient", "P950:mon@09:00-10:00",
                      "--pref", "P902:mon=09:00-10:00", "--drop-staff", "T02", "--as", "d2"])
    assert a.func.__name__ == "cmd_whatif" and a.as_name == "d2" and a.add_patient == ["P950:mon@09:00-10:00"]
    assert p.parse_args(["template", "commit", "d1"]).red_cleanup_done is False
    assert p.parse_args(["template", "compare", "d1"]).other == "master"
    assert p.parse_args(["template", "solve", "d1", "--weekday", "mon,tue"]).weekday == ["mon,tue"]


def test_new_copies_master_into_git_ignored_runs(env):
    cfg, raw, _ = env
    d = mk(env)
    path = D.path_of(cfg, "d1")
    assert path.exists() and path.parent == Path(cfg.runs_dir) / "template-drafts"
    assert d["office"] == "甲" and d["origin"] == "master" and d["assign"] == {}
    assert "P10" not in d["snapshot"]["scope_patients"]  # 乙の方は入らない
    assert {"P1", "PN1", "PN2", "PN3"} <= set(d["snapshot"]["scope_patients"]) and "PN9" not in d["snapshot"]["scope_patients"]
    assert d["sids"]["F1"].startswith("mon") and d["sids"]["F2"] == "wed01"
    with pytest.raises(TemplateError):
        mk(env)  # 同じ名前は --force が要る
    with pytest.raises(TemplateError):
        D.check_name("../x")


def test_candidates_days_and_windows(env):
    d = mk(env)
    specs = {s["patient"]["code"]: s for s in P.candidate_specs(d)}
    assert set(specs) == {"P950", "P951", "P952"}
    # 週 1 回・希望は月水 → 混み具合の軽い水曜（月曜は枠 10・水曜は 1）
    assert specs["P950"]["days"] == [2] and specs["P950"]["dur"] == 40
    assert specs["P950"]["per_day"][2][0] == (540, 715)  # 午前 = 9:00〜11:55 に開始
    assert specs["P951"]["days"] == [1] and specs["P951"]["pair"] is True
    assert specs["P952"]["days"] == [] and "回数" in specs["P952"]["reason"]
    tue = P.build_day(d, 1)
    news = [s for s in tue["slots"] if s.get("new")]
    assert [s["slot_index"] for s in news] == [0, 1] and news[0]["win"] == [780, 840] and news[0]["pair"] == "PN2"
    wed = P.build_day(d, 2)
    (n,) = [s for s in wed["slots"] if s.get("new")]
    male = {c["id"] for c in wed["courses"] if (c["staff"] or {}).get("sex") == "male"}
    assert n["female_only"] and not (male & set(n["allowed"]))  # 女性限定は男性のコースに当てない


def test_solve_week_places_new_slots_and_keeps_master_untouched(env):
    cfg, raw, _ = env
    mk(env)
    res = solve(cfg)
    d = res["draft"]
    ms = {m["weekday"]: m for m in res["metrics"]}
    assert ms[2]["new_placed"] == 1 and ms[1]["new_placed"] == 2
    assert ms[0]["draft"]["no_course"] == 0 and ms[0]["master"]["no_course"] == 1
    assert d["assign"]["new:PN1:2:0"]["start"] == 540
    assert raw["pfv"] == json.loads(json.dumps(raw["pfv"]))  # 材料は書き換えない
    assert any(h["action"] == "solve" for h in d["history"])


def test_whatif_staff_count_and_drop_staff(env):
    cfg, _, _ = env
    mk(env)
    ops.whatif(cfg, "d1", staff_count="mon=2", as_name="d2")
    d2 = D.load(cfg, "d2")
    assert D.load(cfg, "d1")["scenario"]["staff_count"] == {}  # 元の下書きは残る
    p = P.build_day(d2, 0)
    assert [c["label"] for c in p["courses"]] == ["甲A", "甲M"]  # 正規のコースを後ろから閉じる
    ops.whatif(cfg, "d1", staff_count="mon=4", as_name="d3")
    p = P.build_day(D.load(cfg, "d3"), 0)
    assert any(c.get("virtual") for c in p["courses"]) and len(p["courses"]) == 4
    ops.whatif(cfg, "d1", drop_staff=["T02"])
    p = P.build_day(D.load(cfg, "d1"), 0)
    assert all((c["staff"] or {}).get("code") != "T02" for c in p["courses"])
    with pytest.raises(TemplateError):
        ops.whatif(cfg, "d1", drop_staff=["T99"])
    with pytest.raises(TemplateError):
        ops.whatif(cfg, "d1", add_patient=["P901"])  # もう枠のある方は足せない
    with pytest.raises(TemplateError):
        ops.whatif(cfg, "d1", staff_count="mon=x")


def test_candidate_entries_and_ng_weekdays(env):
    cfg, raw, rp = env
    raw["candidates"].append({
        "id": "PN4", "code": "P953", "name": "新規 四子", "status": "pending", "sex_restriction": None,
        "lat": 35.6, "lng": 140.1, "primary_office_id": "o1", "requires_multiple_staff": False,
        "weekly_pattern": {"frequency_per_week": 2, "ng_weekdays": ["Mon", "Tue"],
                           "entries": [{"weekday": "Wed", "time_type": "時間帯", "preferred_start": "14:00",
                                        "preferred_end": "15:00", "service_minutes": 60, "staff_count": 2},
                                       {"weekday": "Thu", "time_type": "午前"}]}})
    rp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    d = mk(env)
    sp = next(s for s in P.candidate_specs(d) if s["patient"]["code"] == "P953")
    assert sp["days"] == [2, 3]
    assert sp["per_day"][2] == ((840, 900), "希望 時間帯（曜日ごと）", 60, True)
    assert sp["per_day"][3][0] == (540, 715)
    news = [s for s in P.build_day(d, 2)["slots"] if s.get("patient_code") == "P953"]
    assert [s["slot_index"] for s in news] == [0, 1] and news[0]["dur"] == 60


def test_whatif_pref_and_add_patient(env):
    cfg, _, _ = env
    mk(env)
    ops.whatif(cfg, "d1", pref=["P902:mon=09:00-10:00"], add_patient=["P952:thu@10:00-11:00"])
    d = D.load(cfg, "d1")
    p = P.build_day(d, 0)
    s = next(x for x in p["slots"] if x.get("patient_code") == "P902")
    assert s["win"] == [540, 600] and "もしも" in s["why"]
    thu = P.build_day(d, 3)
    (n,) = [x for x in thu["slots"] if x.get("new")]
    assert n["patient_code"] == "P952" and n["win"] == [600, 660]
    ops.whatif(cfg, "d1", clear=True)
    assert D.load(cfg, "d1")["scenario"]["prefs"] == {}


def test_move_checks_rules_and_is_held_on_resolve(env):
    cfg, _, _ = env
    d = mk(env)
    sid_red = d["sids"]["F4"]  # 赤ピン（10:30・女性限定）
    with pytest.raises(TemplateError, match="赤ピン"):
        ops.move(cfg, "d1", slot=sid_red, course="A", start="10:45")
    res = ops.move(cfg, "d1", slot=sid_red, course="A")  # コースだけなら替えられる
    assert res["to"] == "甲A 10:30"
    with pytest.raises(TemplateError, match="当てられません"):
        ops.move(cfg, "d1", slot=d["sids"]["F10"], course="A")  # P8 は T01 が NG
    with pytest.raises(TemplateError, match="範囲"):
        ops.move(cfg, "d1", slot=d["sids"]["F3"], course="A", start="13:00")  # 希望 9:00〜12:00 の外
    with pytest.raises(TemplateError):
        ops.move(cfg, "d1", slot="mon99", course="A")
    with pytest.raises(TemplateError, match="コース"):
        ops.move(cfg, "d1", slot=d["sids"]["F1"], course="Z")
    solve(cfg)
    assert D.load(cfg, "d1")["assign"]["F4"] == {"course": "tA", "start": 630}  # 解き直しても動かさない


def test_compare_and_report(env):
    cfg, _, _ = env
    mk(env)
    solve(cfg)
    ops.new(cfg, "d2", origin="d1")
    c = ops.compare(cfg, "d1", "master")
    assert c["b"] == "マスター" and any(r["new"] for r in c["changed"]) and c["lines"]
    assert ops.compare(cfg, "d1", "d2")["changed"] == []
    path = report.write(cfg, "d1")
    html = path.read_text(encoding="utf-8")
    assert path.is_relative_to(Path(cfg.reports_dir))
    assert "@page a3" in html and "A3 landscape" in html and "A4 portrait" in html
    assert "class='b chg'" in html and "月曜" in html and "土曜" in html
    assert "新規 花子"[:2] in html
    with pytest.raises(TemplateError):
        report.write(cfg, "d1", out=str(AGENT / "x.html"))  # リポジトリの中なら docs/reports の下だけ


def test_commit_builds_full_item_pfv_updates_gated(env):
    cfg, raw, _ = env
    mk(env)
    solve(cfg)
    res = ops.commit(cfg, "d1", run_sql=same_master(raw), log=lambda m: None)
    plan = res["plan"]
    assert Path(res["plan_path"]).exists() and plan["kind"] == "course-template"
    assert plan["ready"] is False and plan["blockers"] == [RED_CLEANUP_BLOCKER]
    by = {o["patient_id"]: o for o in plan["ops"]}
    assert {"P4", "PN1", "PN2"} <= set(by)
    (it,) = by["PN1"]["items"]
    assert it["weekday"] == 2 and it["start_time"] == "09:00" and it["duration_min"] == 40 and it["movability"] == "unknown"
    assert [i["slot_index"] for i in by["PN2"]["items"]] == [0, 1]
    reg = load_all()
    for o in plan["ops"]:
        o = {**o, "patient_id": "00000000-0000-4000-8000-000000000001"}
        assert reg.get("pfv-update").validate(o) == []
    res = ops.commit(cfg, "d1", run_sql=same_master(raw), red_cleanup_done=True, log=lambda m: None)
    plan = res["plan"]
    assert plan["ready"] is True and plan["source"]["master_hash"]
    assert 1 <= len(plan["headline"]) <= 3 and all(o["reason"] for o in plan["ops"])
    from rk.plan import check_plan
    ids = {o["patient_id"] for o in plan["ops"]}
    fake = {pid: f"00000000-0000-4000-8000-{i:012d}" for i, pid in enumerate(sorted(ids))}
    plan = {**plan, "ops": [{**o, "patient_id": fake[o["patient_id"]]} for o in plan["ops"]]}
    assert check_plan(plan) == []


def test_commit_blocks_when_master_changed_or_whatif(env):
    cfg, raw, _ = env
    mk(env)
    solve(cfg)
    changed = json.loads(json.dumps(raw["pfv"]))
    changed[0]["start_time"] = "09:45"  # P1 の枠が写した後に変わった
    res = ops.commit(cfg, "d1", run_sql=lambda sql: [{"x": changed}], red_cleanup_done=True, log=lambda m: None)
    assert res["plan"]["ready"] is False and any("マスターが変わった" in b for b in res["plan"]["blockers"])
    res = ops.commit(cfg, "d1", no_master_check=True, red_cleanup_done=True, log=lambda m: None)
    assert res["plan"]["ready"] is False
    ops.whatif(cfg, "d1", staff_count="mon=4", pref=["P902:mon=09:00-10:00"])
    solve(cfg)
    res = ops.commit(cfg, "d1", run_sql=same_master(raw), red_cleanup_done=True, log=lambda m: None)
    bl = " ".join(res["plan"]["blockers"])
    assert "希望の時間帯のもしも" in bl


def test_pfv_rows_sql_rejects_odd_ids():
    assert "patient_fixed_visits" in D.pfv_rows_sql(["P1", "abc-123"])
    with pytest.raises(TemplateError):
        D.pfv_rows_sql(["x'; drop"])


HAS_SOLVER = bool(importlib.util.find_spec("ortools")) or shutil.which("uv") is not None


@pytest.mark.skipif(not HAS_SOLVER, reason="OR-Tools も uv も無い環境")
def test_real_solver_places_new_patients(env):
    cfg, _, _ = env
    mk(env)
    res = ops.solve(cfg, "d1", seconds=2, log=lambda m: None)
    ms = {m["weekday"]: m for m in res["metrics"]}
    assert ms[0]["draft"]["conflicts"] == 0 and ms[0]["draft"]["no_course"] == 0
    assert ms[1]["new_placed"] == 2 and ms[2]["new_placed"] == 1
    a = res["draft"]["assign"]
    assert a["new:PN2:1:0"]["start"] == a["new:PN2:1:1"]["start"] and a["new:PN2:1:0"]["course"] != a["new:PN2:1:1"]["course"]
    assert 540 <= a["new:PN1:2:0"]["start"] <= 715
    assert a["F4"]["start"] == 630  # 赤ピンの時刻は動かない


# ---------------------------------------------------------------------------
# 画面の下書き: 全拠点で作る・最適化の報告（画面の［くわしく］と同じ中身）
# ---------------------------------------------------------------------------


def test_cli_draft_new_all_offices_and_explain_registered():
    p = cli.build_parser()
    a = p.parse_args(["template", "draft-new", "全体の型", "--office", "all"])
    assert a.func.__name__ == "cmd_draft_new" and a.office == "all"
    a = p.parse_args(["template", "explain", "全体の型", "--seq", "3"])
    assert a.func.__name__ == "cmd_explain" and a.seq == 3


def test_explain_summary_speaks_plain_japanese():
    from rk.commands.template import explain_summary

    day = {"conflicts": 2, "over_cap": 1, "no_course": 5, "unstaffed": 0, "travel_min": 100,
           "regular_counts": [5, 7, 3], "manager_visits": 1}
    rep = {
        "draft": "火曜を軽く", "seq": 4, "actor_label": "あなた",
        "text": ["変わった枠は 2 件です。", "移動の合計は 100 分 → 140 分（+40 分）に増えました。"],
        "cautions": ["管理者（M のコース）の訪問が増えました"],
        "categories": [{"kind": "course_added", "label": "コースが無かった枠にコースを付けた", "count": 1, "items": [
            {"weekday_ja": "火", "patient": "青木 様", "from_course": "（コースなし）", "from_start": "10:00",
             "to_course": "稲B", "to_start": "10:00"}]}],
        "days": [{"name": "火", "changes": 2, "master": day,
                  "after": {**day, "conflicts": 0, "no_course": 0, "travel_min": 140, "regular_counts": [6, 6, 6]},
                  "travel": {"routed": 35, "reorder": 5}}],
    }
    out = explain_summary(rep)
    assert out["要約"][1].startswith("移動の合計")
    assert out["種類ごと"][0]["枠"] == ["火曜 青木 様 （コースなし） 10:00 → 稲B 10:00"]
    d = out["曜日ごと（いまの型→下書き）"][0]
    assert d["一般職員の件数"] == "5・7・3 → 6・6・6" and d["移動の合計（分）"] == "100→140"
    assert d["移動の増減の内訳"] == {"道順に新しく入った訪問": 35, "並べ替え": 5}
    import json

    text = json.dumps(out, ensure_ascii=False)
    assert not any(w in text for w in ("直す", "修正", "補正"))
