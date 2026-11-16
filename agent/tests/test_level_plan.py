"""ならしの結果 → rk の案（plan.json）の変換のテスト。架空のデータ（tools/leveling/fixtures）だけを使う。本番に触らない。"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from tools.leveling.extract_rk import extract_week, unwrap  # noqa: E402
from tools.leveling.plan import build_plan, choose_courses  # noqa: E402

FIX = AGENT / "tools" / "leveling" / "fixtures"
CANONICAL = {"return-to-pool", "move-visit-week", "place-from-pool", "patch-course-staff", "assign-staff-week",
             "special-visit-place"}


def load(name):
    return json.loads((FIX / f"sample_{name}.json").read_text(encoding="utf-8"))


def make(result=None, week=None, extra="default", **kw):
    return build_plan(result or load("result"), week or load("week"),
                      load("extra") if extra == "default" else extra,
                      config={"cap_regular": 6}, created_at="2026-10-12T10:00:00", **kw)


def ops_of(plan, name):
    return [o for o in plan["ops"] if o["op"] == name]


def test_ops_use_canonical_names_and_order():
    plan = make()
    assert {o["op"] for o in plan["ops"]} <= CANONICAL
    steps = [o["step"] for o in plan["ops"]]
    assert steps == sorted(steps)
    assert all(isinstance(o["label"], str) and o["label"] for o in plan["ops"])
    assert Counter(o["op"] for o in plan["ops"]) == Counter({
        "assign-staff-week": 1, "return-to-pool": 1, "move-visit-week": 2, "special-visit-place": 1,
        "patch-course-staff": 5})
    # 入らない訪問（v3）があるので、決めるまでは反映できない
    assert plan["ready"] is False and plan["blockers"] == ["入らない訪問が 1 件あります（rk plan resolve で 1 件ずつ決めると反映できます）"]
    assert [u["visit_id"] for u in plan["unplaced"]] == ["v3"] and plan["unplaced"][0]["staff_code"] == "T02"
    from rk.resolve import resolve
    resolve(plan, "pool", approved="今泉 10/12", key="v3")
    assert plan["ready"] is True and plan["blockers"] == []


def test_move_carries_visit_move_week_only_body():
    (mv,) = [o for o in ops_of(make(), "move-visit-week") if o["visit_id"] == "v2"]
    assert mv["iso_year"] == 2026 and mv["iso_week"] == 43
    assert mv["patient_id"] == "P2"
    assert (mv["old_weekday"], mv["old_start_time"]) == (0, "10:00:00")
    assert (mv["new_weekday"], mv["new_start_time"]) == (0, "10:30:00")
    assert mv["new_course_template_id"] == "tXB"  # XA → XB（担当が試験 太郎さんのコース）
    assert mv["from_course"] == "XA" and mv["to_course"] == "XB"
    assert mv["op_group_id"]


def test_special_mark_placed_in_own_office_then_moved():
    plan = make()
    (pl,) = ops_of(plan, "special-visit-place")
    assert pl["mark_id"] == "mk1"
    assert pl["course_template_id"] == "tYM"  # 利用者の拠点 Y の M に置く（place は拠点一致が必須）
    assert pl["start_time"] == "14:00"
    after = [o for o in ops_of(plan, "move-visit-week") if o["patient_id"] == "P5"]
    assert len(after) == 1 and after[0]["visit_id"] is None
    assert after[0]["new_course_template_id"] == "tXM"
    assert after[0]["old_start_time"] == after[0]["new_start_time"] == "14:00:00"
    assert plan["ops"].index(pl) < plan["ops"].index(after[0])


def test_patch_course_staff_and_clear_unused_course():
    plan = make()
    patches = {o["course"]: o for o in ops_of(plan, "patch-course-staff")}
    assert {c: o["staff_id"] for c, o in patches.items()} == {"XA": "S1", "XB": "S2", "XM": "M1", "YA": "S3", "XC": None}
    assert patches["XA"]["course_id"] == "cXA" and patches["XM"]["course_id"] == "cXM"
    assert patches["XA"]["weekday"] == 0 and patches["XA"]["iso_week"] == 43
    assert patches["XA"]["course_code"] == "A" and patches["XA"]["office"] == "X"
    assert plan["course_map"]["2026-10-19"]["M1"] == "XM"  # マネージャーは M


def test_pool_and_manual_override():
    plan = make()
    (pool,) = ops_of(plan, "return-to-pool")
    assert pool["visit_id"] == "v3" and pool["cascade_partner"] is False
    assert pool["op_group_id"] != ops_of(plan, "move-visit-week")[0]["op_group_id"]
    (asg,) = ops_of(plan, "assign-staff-week")
    assert asg["visit_id"] == "v6" and asg["staff_id"] is None
    assert any(w.startswith("入らない:") and "三郎" in w and "相談先" in w for w in plan["warnings"])


def test_unchanged_and_completed_visits_have_no_ops():
    plan = make()
    touched = {o.get("visit_id") for o in plan["ops"]}
    assert "v1" not in touched and "v4" not in touched and "v7" not in touched


def test_explicit_unassigned_gets_direct_assign_after_course_patch():
    """明示の担当なし（主担当が空・手の上書きの印＝半休の時間帯から外した訪問など・休み設計 §3-2）は
    step 0 で印を外さず（外すとコースの担当＝休む方に読み替わる）、step 4 の後に直接担当を付ける。"""
    r, extra = load("result"), load("extra")
    for v in r["visits"]:
        if v["id"] in ("v2", "v4"):
            v["cur_staff"] = None  # 道具は明示の担当なしを「今の担当なし」として読む
    # v2 = 半休の S1 のコース XA に残っていた訪問 → S2 へ（別のコースへ移る）／v4 = コースも時刻も変わらない
    extra["manual_overrides"] += [{"id": "v2", "primary_staff_id": None, "visit_date": "2026-10-19"},
                                  {"id": "v4", "primary_staff_id": None, "visit_date": "2026-10-19"}]
    plan = make(result=r, extra=extra)
    asg = {o["visit_id"]: o for o in ops_of(plan, "assign-staff-week")}
    assert asg["v6"]["step"] == 0 and asg["v6"]["staff_id"] is None  # 担当のある手の上書きは今までどおり外す
    assert asg["v2"]["step"] == 5 and asg["v2"]["staff_id"] == "S2"
    assert asg["v4"]["step"] == 5 and asg["v4"]["staff_id"] == "S3"
    assert any(o["op"] == "move-visit-week" and o["visit_id"] == "v2" for o in plan["ops"])
    assert not any(o["op"] == "move-visit-week" and o["visit_id"] == "v4" for o in plan["ops"])
    steps = [o["step"] for o in plan["ops"]]
    assert steps == sorted(steps)
    last_patch = max(i for i, o in enumerate(plan["ops"]) if o["op"] == "patch-course-staff")
    assert all(i > last_patch for i, o in enumerate(plan["ops"]) if o["op"] == "assign-staff-week" and o["step"] == 5)


def test_checks_are_strings_and_errors_block():
    plan = make()
    assert all(isinstance(c, str) for c in plan["checks"])
    assert all(isinstance(s, str) for s in plan["summary"]) and all(isinstance(w, str) for w in plan["warnings"])
    assert any(c.startswith("△ 昼休み") for c in plan["checks"])
    r = load("result")
    r["errors"] = ["2026-10-19 試験　花子: 7 名（上限超え）"]
    bad = make(result=r)
    assert any(c.startswith("× 1 人の上限") for c in bad["checks"])
    assert bad["ready"] is False and bad["blockers"]
    detail = {d["rule"]: d for d in bad["check_details"]}
    assert detail["1 人の上限"]["errors"] == 1


def test_timeline_shape_for_rk_report():
    plan = make()
    tl = plan["timeline"]
    assert isinstance(tl, dict) and tl["date"] == "2026-10-19"
    assert isinstance(plan["timelines"], list) and len(plan["timelines"]) == 1
    names = [r["name"] for r in tl["rows"]]
    assert "試験 花子" in names and names[-1] == "入らない"
    for r in tl["rows"]:
        for b in r["blocks"]:
            assert {"start", "end", "label", "changed"} <= set(b)
            assert len(b["start"]) == 5 and b["start"] <= b["end"]
    taro = next(r for r in tl["rows"] if r["name"] == "試験 太郎")
    assert [b["changed"] for b in taro["blocks"]] == [True, True]
    hanako = next(r for r in tl["rows"] if r["name"] == "試験 花子")
    assert any(b["kind"] == "event" for b in hanako["blocks"])


def test_no_templates_blocks_plan():
    plan = make(extra=None)
    assert plan["ready"] is False
    assert not ops_of(plan, "patch-course-staff")
    assert any("テンプレート" in b for b in plan["blockers"])


def test_trainee_assigned_is_blocker():
    week = load("week")
    for s in week["staff"]:
        if s["id"] == "S3":
            s["is_trainee"] = True
    plan = make(week=week)
    assert plan["ready"] is False
    assert any(c.startswith("× 新人") for c in plan["checks"])


def test_ambiguous_move_source_is_warned():
    week = load("week")
    dup = copy.deepcopy(next(v for v in week["visits"] if v["id"] == "v2"))
    dup["id"] = "v2b"
    week["visits"].append(dup)  # 同じ利用者・同じ日・同じ開始がもう 1 件（案には入っていない）
    plan = make(week=week)
    assert any("1 件に決まらない" in w for w in plan["warnings"])


def test_regular_staff_stay_in_own_office():
    staff = {"A": {"manager": False, "offices": ["X"]}, "B": {"manager": False, "offices": ["Y"]}}
    keep = Counter({("B", "XA"): 5})  # 今は X のコースに 5 件あっても
    office_days = {"X": set(range(6)), "Y": set(range(5))}
    m, err = choose_courses(["A", "B"], keep, ["XA", "XB", "YA"], {"XA"}, staff,
                            {"XA": "X", "XB": "X", "YA": "Y"}, office_days, 0)
    assert err is None and m["B"] == "YA"
    # Y が休みの土曜は X のコースでもよい
    m, err = choose_courses(["A", "B"], keep, ["XA", "XB"], {"XA"}, staff, {"XA": "X", "XB": "X"}, office_days, 5)
    assert err is None and m["B"] == "XA"
    m, err = choose_courses(["A", "B"], keep, ["XA"], set(), staff, {"XA": "X"}, office_days, 0)
    assert m is None and "足りません" in err


def test_plan_passes_rk_plan_check_shape():
    rk_plan = pytest.importorskip("rk.plan")
    from rk.opsbase import Op, Registry

    reg = Registry()
    for name in CANONICAL:
        reg.add(type(name.replace("-", "_"), (Op,), {"name": name}))
    from rk.resolve import resolve
    plan = make()
    assert any("入らない訪問が" in e for e in rk_plan.check_plan(plan, registry=reg))
    resolve(plan, "pool", approved="今泉 10/12", key="v3", registry=reg)
    assert rk_plan.check_plan(plan, registry=reg) == []


def test_unwrap_rk_and_psql_shapes():
    assert unwrap([{"json_build_object": {"a": 1}}]) == {"a": 1}
    assert unwrap([{"coalesce": []}]) == []
    assert unwrap('BEGIN\n{"a": 2}\nCOMMIT') == {"a": 2}
    assert unwrap([{"a": 1, "b": 2}]) == [{"a": 1, "b": 2}]


def test_extract_week_with_injected_runner(tmp_path):
    seen = []

    def fake(sql):
        seen.append(sql)
        if "manual_overrides" in sql:
            return [{"json_build_object": load("extra")}]
        if "special_marks" in sql:
            return [{"json_build_object": load("week")}]
        return [{"coalesce": [{"patient_id": "P1", "visit_date": "2026-10-12", "start_time": "09:00:00",
                               "primary_staff_id": "S1"}]}]

    from datetime import date

    w, h, e = extract_week(fake, date(2026, 10, 19), tmp_path)
    assert len(seen) == 3 and all("insert" not in s.lower() and "update " not in s.lower() for s in seen)
    assert json.loads(w.read_text(encoding="utf-8"))["week_start"] == "2026-10-19"
    assert len(json.loads(h.read_text(encoding="utf-8"))) == 1
    assert json.loads(e.read_text(encoding="utf-8"))["templates"]


def test_check_line_lists_all_details_up_to_ten():
    from tools.leveling.plan import check_line
    two = check_line({"rule": "全部の訪問が入る", "ok": False, "errors": 2, "warnings": 0,
                      "details": ["入らない: 10/22 A", "入らない: 10/22 B"]})
    assert two == "× 全部の訪問が入る（問題 2 件）: 入らない: 10/22 A／入らない: 10/22 B"
    many = check_line({"rule": "全部の訪問が入る", "ok": False, "errors": 13, "warnings": 0,
                       "details": [f"入らない {i}" for i in range(13)]})
    assert "入らない 9" in many and "入らない 10" not in many and many.endswith("／ほか 3 件")
    assert check_line({"rule": "NG", "ok": True, "errors": 0, "warnings": 0, "details": []}) == "○ NG"


def test_unplaced_reason_counts_male_only_as_hard():
    """性別制限「男性のみ」: 女性の職員は「男性限定」で数え、時刻の理由に混ぜない (2026-10-30)."""
    from tools.leveling.plan import RULES, unplaced_reason

    v = {
        "day": "2026-10-12", "win": [9 * 60, 9 * 60], "dur": 35, "ng": [],
        "female_only": False, "male_only": True, "patient": "p0",
    }
    staff = [{"id": "SF", "off": [], "shift": {"0": [9 * 60, 18 * 60]}, "sex": "female", "manager": False}]
    reason = unplaced_reason(v, staff, Counter(), 6, 0)
    assert "男性限定" in "".join(reason)
    assert ("男性限定", ("男性限定に女性",)) in RULES


# ---- 期間で組む: 日を絞った案の読み直し（basis_hash は target.days の日だけ・agent-period-build-design §3-2） ----
def _week_two_days():
    w = load("week")
    extra_day = copy.deepcopy(w["visits"][0])
    extra_day.update(id="v9", visit_date="2026-10-20", start_time="09:00:00")
    w["visits"].append(extra_day)
    return w


def test_target_days_and_basis_hash_only_for_those_days():
    from tools.leveling.plan import basis_hash
    w = _week_two_days()
    whole = make(week=w)
    assert "days" not in whole["target"]
    assert whole["source"]["basis_hash"] == basis_hash(w)  # 日を絞らない案は今までと同じ値
    plan = make(week=w, days=["2026-10-19"])
    assert plan["target"]["days"] == ["2026-10-19"]
    assert plan["source"]["basis_hash"] == basis_hash(w, ["2026-10-19"]) != basis_hash(w)
    # 範囲の外（10/20）が変わっても同じ・範囲の中（10/19）が変わると違う
    out_changed = copy.deepcopy(w)
    out_changed["visits"][-1]["primary_staff_id"] = "S2"
    assert basis_hash(out_changed, ["2026-10-19"]) == plan["source"]["basis_hash"]
    in_changed = copy.deepcopy(w)
    in_changed["visits"][0]["start_time"] = "09:15:00"
    assert basis_hash(in_changed, ["2026-10-19"]) != plan["source"]["basis_hash"]


def test_target_days_from_result_summary():
    r = load("result")
    r["summary"] = {"days": ["2026-10-19"]}
    assert make(result=r)["target"]["days"] == ["2026-10-19"]
    r["summary"] = {"days": "all"}
    assert "days" not in make(result=r)["target"]


# ---- 途中で止まりにくくする（agent-period-build-design §3-4）: 10/28 の形の再現 ----
def _female_only_unplaced(unassigned=False):
    """v3（XB・入らない訪問）の方が女性限定。案は XB に男性の S2 を付ける（10/28 と同じ形）。"""
    w, r, extra = load("week"), load("result"), load("extra")
    for p_ in w["patients"]:
        if p_["id"] == "P3":
            p_["sex_restriction"] = "female_only"
    if unassigned:  # 担当なしの訪問（主担当もコースの担当も無い）
        for v in w["visits"]:
            if v["id"] == "v3":
                v["primary_staff_id"] = None
                v["course_staff_id"] = None
        for v in r["visits"]:
            if v["id"] == "v3":
                v["cur_staff"] = None
        for c in extra["courses"]:
            if c["id"] == "cXB":
                c["assigned_staff_id"] = None
    return make(result=r, week=w, extra=extra)


def test_course_guard_written_on_ops():
    plan = _female_only_unplaced()
    xb = next(o for o in ops_of(plan, "patch-course-staff") if o["course"] == "XB")
    g = xb["course_guard"]
    assert g["staff"]["id"] == "S2" and g["staff"]["sex"] == "male" and g["staff"]["is_trainee"] is False
    assert {v["visit_id"] for v in g["visits"]} == {"v3", "v6"}
    v3 = next(v for v in g["visits"] if v["visit_id"] == "v3")
    assert v3["sex_restriction"] == "female_only" and v3["own_staff"] is True
    assert all("course_guard" in o for o in plan["ops"] if o["op"] in ("move-visit-week", "special-visit-place"))
    assert not any("course_guard" in o for o in ops_of(plan, "patch-course-staff") if o["staff_id"] is None)


def test_keep_female_only_visit_on_male_course_is_blocked_before_apply():
    from rk.plan import COURSE_GUARD_PREFIX, check_plan
    from rk.resolve import resolve
    plan = _female_only_unplaced(unassigned=True)  # 10/28 の形: 担当なしの女性限定の訪問がコースに残る
    # 入らない訪問はプールへ戻す操作があるので、作った時点では組み合わせの問題はない
    assert not [b for b in plan["blockers"] if b.startswith(COURSE_GUARD_PREFIX)]
    resolve(plan, "keep", approved="試験 11/16", key="v3")
    hits = [b for b in plan["blockers"] if b.startswith(COURSE_GUARD_PREFIX)]
    assert len(hits) == 1 and "女性限定" in hits[0] and "XB" in hits[0]
    assert plan["ready"] is False
    errs = check_plan(plan)
    assert any("女性限定" in e for e in errs)
    # 決め方をプールへ変えると外れる
    resolve(plan, "pool", approved="試験 11/16", key="v3")
    assert plan["ready"] is True and not [e for e in check_plan(plan) if "女性限定" in e]


def test_hand_merged_month_plan_is_checked_on_ops():
    """10/28 は週の案をモデルが手で 1 つ（kind month-leveling）にまとめた。操作の材料だけで見つける。"""
    from rk.plan import check_plan
    a = _female_only_unplaced()
    b = make()
    ops = [o for o in a["ops"] if not (o["op"] == "return-to-pool" and o["visit_id"] == "v3")] + b["ops"]
    merged = {"kind": "month-leveling", "title": "11 月の案", "target": {}, "summary": ["まとめ"], "ops": ops,
              "checks": [], "warnings": []}
    errs = check_plan(merged)
    assert len([e for e in errs if "女性限定" in e]) == 1
    # 女性限定の訪問を先に別のコースへ移せば通る（XA の担当は女性の S1）
    mv = {"op": "move-visit-week", "label": "v3 を XA へ", "visit_id": "v3", "patient_id": "P3", "date": "2026-10-19",
          "iso_year": 2026, "iso_week": 43, "old_weekday": 0, "old_start_time": "13:00:00", "new_weekday": 0,
          "new_start_time": "13:00:00", "new_course_template_id": "tXA", "from_course": "XB", "to_course": "XA"}
    xb_i = next(i for i, o in enumerate(merged["ops"]) if o["op"] == "patch-course-staff" and o["course"] == "XB")
    merged["ops"].insert(xb_i, mv)
    assert not [e for e in check_plan(merged) if "女性限定" in e]


def test_ng_and_trainee_on_course_patch():
    from rk.plan import course_staff_conflicts
    plan = make()
    xb = next(o for o in ops_of(plan, "patch-course-staff") if o["course"] == "XB")
    xb = copy.deepcopy(xb)
    xb["course_guard"]["visits"][0]["ng_staff_ids"] = ["S2"]
    assert any("NG の職員" in c for c in course_staff_conflicts({"ops": [xb]}))
    xb["course_guard"]["staff"]["is_trainee"] = True
    assert any("新人" in c for c in course_staff_conflicts({"ops": [xb]}))
    xb["acknowledge_constraint_warnings"] = True  # 人が了承して通すと書いたものは止めない（新人は別: 本体が必ず断る）
    xb["course_guard"]["staff"]["is_trainee"] = False
    assert course_staff_conflicts({"ops": [xb]}) == []
    del xb["course_guard"]  # 材料の無い操作（古い案）は見ない
    assert course_staff_conflicts({"ops": [xb]}) == []


def test_keep_unassigned_visit_would_inherit_course_staff_is_reported():
    """10/28 の 2 つ目: 担当なしで残すと決めた訪問が、コースの担当付けでその人の訪問に読み替わる。
    担当なしのまま保つ操作は画面と同じ API に無い（visit-assign-staff-week の null は手の上書きを外す）ので、
    操作は足さずに知らせる。"""
    from rk.plan import KEPT_UNASSIGNED_PREFIX
    from rk.resolve import resolve
    plan = _female_only_unplaced(unassigned=True)
    resolve(plan, "keep", approved="試験 11/16", key="v3")
    assert not [o for o in plan["ops"] if o.get("resolution_of") == "v3"]  # 担当が無いので留める操作は作れない
    notes = [w for w in plan["warnings"] if w.startswith(KEPT_UNASSIGNED_PREFIX)]
    assert len(notes) == 1 and "XB" in notes[0]
    # 女性限定なので、そもそも反映の前に止まる（10/28 は 273 件目で 422）
    assert plan["ready"] is False
    resolve(plan, "pool", approved="試験 11/16", key="v3")
    assert not [w for w in plan["warnings"] if w.startswith(KEPT_UNASSIGNED_PREFIX)]


# ---- レビューの指摘（11-16）: 検査が黙って漏らさない ----
def _xb_patch(plan):
    return next(o for o in ops_of(plan, "patch-course-staff") if o["course"] == "XB")


def test_course_conflict_reported_even_if_same_text_in_blockers_when_ready_true():
    """blockers と同じ文を落とすのは "ready": false（readiness_errors が出す）のときだけ。"""
    from rk.plan import check_plan
    from rk.resolve import resolve
    plan = _female_only_unplaced(unassigned=True)
    resolve(plan, "keep", approved="試験 11/16", key="v3")
    hit = next(b for b in plan["blockers"] if "女性限定" in b)
    assert len([e for e in check_plan(plan) if "女性限定" in e]) == 1  # ready:false → 1 回だけ（重ねない）
    plan["ready"] = True  # 手で ready を true にした案
    assert hit in plan["blockers"]
    assert any("女性限定" in e for e in check_plan(plan))


def test_course_guard_staff_must_match_op_staff():
    from rk.plan import course_staff_conflicts
    xb = copy.deepcopy(_xb_patch(make()))
    assert course_staff_conflicts({"ops": [xb]}) == []
    xb["staff_id"] = "S1"  # 手で担当を書き換えた（材料は S2 のまま）
    (c,) = course_staff_conflicts({"ops": [xb]})
    assert "材料が古い" in c and "rk level で案を作り直して" in c
    assert "直す" not in c and "修正" not in c
    xb["acknowledge_constraint_warnings"] = True  # 了承の印があっても材料が古いことは知らせる
    assert course_staff_conflicts({"ops": [xb]})


def test_trainee_and_full_day_off_ignore_acknowledge():
    from rk.plan import course_staff_conflicts
    xb = copy.deepcopy(_xb_patch(make()))
    xb["acknowledge_constraint_warnings"] = True
    xb["course_guard"]["staff"]["is_trainee"] = True
    assert any("新人" in c for c in course_staff_conflicts({"ops": [xb]}))
    xb["course_guard"]["staff"]["is_trainee"] = False
    xb["course_guard"]["staff"]["full_day_off"] = True
    assert any("終日の休み" in c for c in course_staff_conflicts({"ops": [xb]}))


def test_full_day_off_written_from_weekly_overrides():
    """本体は PATCH /courses でその日終日休み（公休・有給）の人を 422 で断る。週の休みから材料に書く。"""
    w = load("week")
    xb = _xb_patch(make(week=w))
    wd = xb["weekday"]
    assert xb["course_guard"]["staff"]["full_day_off"] is False
    w2 = copy.deepcopy(w)
    w2["weekly_overrides"] = [{"staff_id": "S2", "weekday": wd, "override_type": "am_off"}]  # 半休は断られない
    assert _xb_patch(make(week=w2))["course_guard"]["staff"]["full_day_off"] is False
    w2["weekly_overrides"] = [{"staff_id": "S2", "weekday": wd, "override_type": "paid_off"}]
    plan = make(week=w2)
    assert _xb_patch(plan)["course_guard"]["staff"]["full_day_off"] is True
    assert any("終日の休み" in b for b in plan["blockers"])


def test_target_days_ops_outside_days_are_errors():
    from rk.plan import check_plan
    from rk.resolve import resolve
    plan = make(days=["2026-10-19"])
    resolve(plan, "pool", approved="試験 11/16", key="v3")
    assert not [e for e in check_plan(plan) if "範囲の外の日" in e]  # rk level --day の案はそのまま通る
    bad = copy.deepcopy(plan)
    bad["ops"].append({"op": "cancel-visit-week", "label": "10/20 の取消", "visit_id": "v9", "cancel": True,
                       "patient_id": "P1", "date": "2026-10-20", "start_time": "09:00"})
    errs = [e for e in check_plan(bad) if "範囲の外の日" in e]
    assert len(errs) == 1 and "2026-10-19 だけを組む案" in errs[0] and "2026-10-20" in errs[0]
    # 日付の無い操作でも、曜日と週から日が分かれば見る（移動先の曜日・コースの曜日）
    mv = copy.deepcopy(ops_of(plan, "move-visit-week")[0])
    mv.pop("date", None)
    mv["new_weekday"] = 2
    bad2 = copy.deepcopy(plan)
    bad2["ops"].append(mv)
    assert any("2026-10-21" in e for e in check_plan(bad2) if "範囲の外の日" in e)
    pc = copy.deepcopy(ops_of(plan, "patch-course-staff")[0])
    pc.pop("date", None)
    pc.pop("course_guard", None)
    pc["weekday"] = 5
    bad3 = copy.deepcopy(plan)
    bad3["ops"].append(pc)
    assert any("2026-10-24" in e for e in check_plan(bad3) if "範囲の外の日" in e)
    # 範囲の付いた clear-week も、範囲の日がすべて target.days に入っていること
    cw = {"op": "clear-week", "label": "空にする", "iso_year": 2026, "iso_week": 43,
          "date_from": "2026-10-19", "date_to": "2026-10-20"}
    bad4 = copy.deepcopy(plan)
    bad4["ops"].append(cw)
    assert any("2026-10-20" in e for e in check_plan(bad4) if "範囲の外の日" in e)
    # target.days の無い案（週全体）は見ない
    whole = copy.deepcopy(bad)
    del whole["target"]["days"]
    assert not [e for e in check_plan(whole) if "範囲の外の日" in e]
