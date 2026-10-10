"""ならし案 (level.py の出力) を本番へ反映する操作の一覧 ops.json を作る。本番には繋がない。
使い方: python build_ops.py <level の出力フォルダ> <出力フォルダ>
週42 (2026-10-07) の手順を週を選べる形にしたもの。今回の週は担当が全部空 (一斉スタッフ未割当済み) の前提。
順番:
 1 入らない訪問を保留プールへ (DELETE /visits・1 つの op_group)
 3 今週だけ移動 (コース・時刻)
 3 特別訪問週間の○を置く (拠点が違えば同じ拠点の M に置いてから移す)
 4 コースに担当 (月〜土。宇田川さんは都賀 A・同点ならマネージャーは M)"""
import itertools, json, re, sys
from collections import Counter, defaultdict
from datetime import date, timedelta
import openpyxl

SRC, OUT = sys.argv[1], sys.argv[2]
week = json.load(open(f"{SRC}/week.json", encoding="utf-8"))
monday = date.fromisoformat(week["week_start"])
iso = monday.isocalendar()
pat = {p["id"]: p for p in week["patients"]}
staff = {s["name"]: s for s in week["staff"]}
office_label = {o["id"]: o["short_label"] for o in week["offices"]}
TPL = {"津A": "d1bba8ad-408d-42db-84a8-6ffb54b83bdc", "稲A": "a4f2276e-dd8c-410d-9319-9eb7cbe8e084",
       "稲B": "c687fb66-d351-473e-b408-b95993e11546", "稲C": "e0b5d1aa-e75a-4aca-b6d8-99dd5985e8df",
       "稲D": "b4d33c20-9297-4c13-bbf1-7de110a689da", "稲M": "8ead9177-01eb-4358-8b50-5d716c614384"}
def hm(t): h, m = map(int, str(t)[:5].split(":")); return h * 60 + m
def fmt(m): return f"{m // 60:02d}:{m % 60:02d}"

key = defaultdict(list)
for v in week["visits"]:
    if v["status"] == "planned":
        key[(v["visit_date"], pat[v["patient_id"]]["name"], v["start_time"][:5])].append(v)
marks = defaultdict(list)
for m in week.get("special_marks") or []:
    marks[(m["patient_id"], (monday + timedelta(days=m["weekday"])).isoformat())].append(m)

plan = []
for r in list(openpyxl.load_workbook(f"{SRC}/leveling.xlsx", read_only=True)["変更一覧"].iter_rows(values_only=True))[1:]:
    day, pname, cur_t, cur_s, new_t, new_s = r[:6]
    if not day:
        continue
    mo, dd = re.match(r"(\d+)/(\d+)", day).groups()
    d = f"{monday.year}-{int(mo):02d}-{int(dd):02d}"
    if str(cur_t).startswith("追加"):
        p = next(p for p in week["patients"] if p["name"] == pname)
        m = marks[(p["id"], d)].pop(0)
        plan.append(dict(kind="add", mark_id=m["id"], pid=p["id"], day=d, name=pname, start=hm(new_t),
                         dur=int(m["duration_min"]), staff=new_s, cur_course=None, cur_t=None))
        continue
    v = key[(d, pname, cur_t)].pop(0)
    cc = (v["course_office"] or "") + (v["course_code"] or "")
    if new_t == "入らない":
        plan.append(dict(kind="pool", id=v["id"], pid=v["patient_id"], day=d, name=pname, cur_t=cur_t, cur_course=cc))
    else:
        plan.append(dict(kind="visit", id=v["id"], pid=v["patient_id"], day=d, name=pname, cur_t=cur_t, cur_course=cc,
                         start=hm(new_t), staff=new_s))
left = [v for vs in key.values() for v in vs]
assert not left, f"案に無い予定の訪問が {len(left)} 件 (担当が空の前提が崩れている)"

# 日ごとに 職員→コース (今のコースに残る訪問が最多・宇田川さんは都賀 A・同点ならマネージャーは M)
cur_courses = defaultdict(set)
for v in week["visits"]:
    if v["status"] == "planned" and v["course_code"]:
        cur_courses[v["visit_date"]].add((v["course_office"] or "") + v["course_code"])
cmap = {}
for d in sorted({x["day"] for x in plan if x["kind"] != "pool"}):
    xs = [x for x in plan if x["day"] == d and x["kind"] != "pool"]
    people = sorted({x["staff"] for x in xs})
    keep = Counter((x["staff"], x["cur_course"]) for x in xs)
    courses = sorted(set(TPL) | (cur_courses[d] & set(TPL)))
    best = None
    for perm in itertools.permutations(courses, len(people)):
        # 宇田川さんは月〜金だけ都賀 A (都賀は土曜を開けていない・週42 も土曜は稲毛)
        if date.fromisoformat(d).weekday() < 5 and any(p.startswith("宇田川") and c != "津A" for p, c in zip(people, perm)):
            continue
        kept = sum(keep[(p, c)] for p, c in zip(people, perm))
        pref = sum((staff[p]["role"] == "manager") == c.endswith("M") for p, c in zip(people, perm))
        newc = sum(c not in cur_courses[d] for c in perm)
        score = (kept, -newc, pref)
        if best is None or score > best[0]:
            best = (score, dict(zip(people, perm)))
    cmap[d] = best[1]
    print(d, {p.replace("　", ""): c for p, c in best[1].items()},
          "移す", sum(1 for x in xs if x["kind"] == "visit" and best[1][x["staff"]] != x["cur_course"]))

ops, expect = [], []
for x in plan:
    if x["kind"] == "pool":
        ops.append({"step": 1, "kind": "to_pool", "visit_id": x["id"], "label": f"{x['day']} {x['name']} {x['cur_t']} → プール"})
for x in sorted(plan, key=lambda x: (x["day"], x.get("start") or 0)):
    if x["kind"] == "pool":
        continue
    wd = date.fromisoformat(x["day"]).weekday()
    tgt = cmap[x["day"]][x["staff"]]
    expect.append({"visit_id": x.get("id"), "patient_id": x["pid"], "date": x["day"], "start": fmt(x["start"]),
                   "staff_id": staff[x["staff"]]["id"], "course": tgt})
    if x["kind"] == "add":
        p_off = office_label[pat[x["pid"]]["primary_office_id"]]
        place = tgt if tgt.startswith(p_off) else p_off + "M"
        ops.append({"step": 3, "kind": "place", "mark_id": x["mark_id"],
                    "body": {"course_template_id": TPL[place], "start_time": fmt(x["start"])},
                    "label": f"{x['day']} {x['name']} {place} {fmt(x['start'])}（特別訪問週間）"})
        if place != tgt:
            ops.append({"step": 3, "kind": "move", "body": {"iso_year": iso.year, "iso_week": iso.week, "patient_id": x["pid"],
                        "old_weekday": wd, "old_start_time": fmt(x["start"]) + ":00", "new_weekday": wd,
                        "new_start_time": fmt(x["start"]) + ":00", "new_course_template_id": TPL[tgt]},
                        "label": f"{x['day']} {x['name']} {place} → {tgt}（置いた後の移動）"})
        continue
    if tgt == x["cur_course"] and fmt(x["start"]) == x["cur_t"]:
        continue
    ops.append({"step": 3, "kind": "move", "body": {"iso_year": iso.year, "iso_week": iso.week, "patient_id": x["pid"],
                "old_weekday": wd, "old_start_time": x["cur_t"] + ":00", "new_weekday": wd,
                "new_start_time": fmt(x["start"]) + ":00",
                "new_course_template_id": TPL[tgt] if tgt != x["cur_course"] else None},
                "label": f"{x['day']} {x['name']} {x['cur_course']} {x['cur_t']} → {tgt} {fmt(x['start'])}"})
for d, m in cmap.items():
    wd = date.fromisoformat(d).weekday()
    for s, c in m.items():
        ops.append({"step": 4, "kind": "course_staff", "course": c, "weekday": wd, "staff_id": staff[s]["id"], "label": f"{d} {c} ← {s}"})
assert [o["step"] for o in ops] == sorted(o["step"] for o in ops)
json.dump({"iso_year": iso.year, "iso_week": iso.week, "monday": monday.isoformat(), "ops": ops, "expect": expect, "cmap": cmap},
          open(f"{OUT}/ops.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(f"{OUT}/expect.txt", "w", encoding="utf-8").write("".join(
    f"{e['visit_id'] or ''}|{e['patient_id']}|{e['date']}|{e['start']}|{e['staff_id']}|{e['course']}\n" for e in expect))
print(Counter((o["step"], o["kind"]) for o in ops), "expect", len(expect))
