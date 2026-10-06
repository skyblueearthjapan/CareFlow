"""案 v4 を本番へ反映する操作の一覧 (ops.json) を作る。本番には繋がない。
順番: 1 今週だけ取消 → 2 今週だけ移動 (コース・時刻) → 3 特別訪問の配置 (拠点が違えば稲毛に置いてから移動)
      → 4 コースに担当 (最後に週42 のコースを読み直して ID を解決) → 5 付け間違いの○を取消"""
import json, sys
from datetime import date

D = sys.argv[1]
plan = json.load(open(f"{D}/plan.json", encoding="utf-8"))
week = json.load(open(f"{D}/week.json", encoding="utf-8"))
cmap = json.load(open(f"{D}/course_map.json", encoding="utf-8"))
staff_id = {s["name"]: s["id"] for s in week["staff"]}
pat = {p["id"]: p for p in week["patients"]}
office_label = {o["id"]: o["short_label"] for o in week["offices"]}
vis = {v["id"]: v for v in week["visits"]}
TPL = {"津A": "d1bba8ad-408d-42db-84a8-6ffb54b83bdc", "稲A": "a4f2276e-dd8c-410d-9319-9eb7cbe8e084",
       "稲B": "c687fb66-d351-473e-b408-b95993e11546", "稲C": "e0b5d1aa-e75a-4aca-b6d8-99dd5985e8df",
       "稲D": "b4d33c20-9297-4c13-bbf1-7de110a689da", "稲M": "8ead9177-01eb-4358-8b50-5d716c614384"}
CANCEL = ["291dd2fb", "4d6ac712", "aed620bd", "e511c7c9", "58f06049", "b0f3dfcf", "eadad8ee"]
# 付け間違いの○ (週42 の特定の利用者・PO 決定で取消)
STRAY_MARKS = {0: "69134a41-5842-4a16-8c44-e78dfffc6eda", 2: "408ce1df-4d29-40a3-8d68-8b655322468a",
               4: "fab506a9-cdcd-4ead-a3e4-3851410f2fb9"}

def fmt(m): return f"{m // 60:02d}:{m % 60:02d}"

ops = []
for pre in CANCEL:
    vid = next(i for i in vis if i.startswith(pre))
    ops.append({"step": 1, "kind": "cancel", "visit_id": vid,
                "label": f"{vis[vid]['visit_date']} {pat[vis[vid]['patient_id']]['name']} {vis[vid]['start_time'][:5]}"})

expect = []  # 反映後に確かめる (patient, date, start, staff, course)
for x in plan:
    tgt = cmap[x["day"]][x["staff"]]
    wd = date.fromisoformat(x["day"]).weekday()
    expect.append({"patient_id": x["pid"], "date": x["day"], "start": fmt(x["start"]),
                   "staff_id": staff_id[x["staff"]], "course": tgt, "new": x["new"]})
    if x["new"]:
        continue
    v = vis[x["id"]]
    cur = (v["course_office"] or "") + (v["course_code"] or "")
    if cur == tgt and x["cur_t"] == fmt(x["start"]):
        continue
    ops.append({"step": 2, "kind": "move", "patient_id": x["pid"], "date": x["day"],
                "body": {"iso_year": 2026, "iso_week": 42, "patient_id": x["pid"], "old_weekday": wd,
                         "old_start_time": x["cur_t"] + ":00", "new_weekday": wd,
                         "new_start_time": fmt(x["start"]) + ":00",
                         "new_course_template_id": TPL[tgt] if cur != tgt else None},
                "label": f"{x['day']} {x['name']} {cur} {x['cur_t']} → {tgt} {fmt(x['start'])}"})

for x in plan:
    if not x["new"]:
        continue
    tgt = cmap[x["day"]][x["staff"]]
    wd = date.fromisoformat(x["day"]).weekday()
    p_off = office_label[pat[x["pid"]]["primary_office_id"]]
    place_tpl = tgt if tgt.startswith(p_off) else p_off + "M"  # 拠点が違えば同じ拠点の M に置いてから移す
    ops.append({"step": 3, "kind": "place", "mark_id": x["mark_id"],
                "body": {"course_template_id": TPL[place_tpl], "start_time": fmt(x["start"])},
                "label": f"{x['day']} {x['name']} {place_tpl} {fmt(x['start'])}"})
    if place_tpl != tgt:
        ops.append({"step": 3, "kind": "move", "patient_id": x["pid"], "date": x["day"],
                    "body": {"iso_year": 2026, "iso_week": 42, "patient_id": x["pid"], "old_weekday": wd,
                             "old_start_time": fmt(x["start"]) + ":00", "new_weekday": wd,
                             "new_start_time": fmt(x["start"]) + ":00", "new_course_template_id": TPL[tgt]},
                    "label": f"{x['day']} {x['name']} {place_tpl} → {tgt} {fmt(x['start'])}（配置の後の移動）"})

# 4: コースに担当 (コース ID は実行時に週42 を読み直して (office+code, weekday) で引く)
for day, m in cmap.items():
    wd = date.fromisoformat(day).weekday()
    for s, c in m.items():
        if any(e["date"] == day and e["staff_id"] == staff_id[s] for e in expect):
            ops.append({"step": 4, "kind": "course_staff", "course": c, "weekday": wd,
                        "staff_id": staff_id[s], "label": f"{day} {c} ← {s}"})

for wd, mid in STRAY_MARKS.items():
    ops.append({"step": 5, "kind": "delete_mark", "mark_id": mid, "label": f"週42 曜日{wd} の付け間違いの○"})

json.dump({"ops": ops, "expect": expect}, open(f"{D}/ops.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from collections import Counter
print(Counter((o["step"], o["kind"]) for o in ops), "expect", len(expect))
# 同じ利用者・同じ日に移動先の時刻がぶつからないか (取消した行も含めて)
