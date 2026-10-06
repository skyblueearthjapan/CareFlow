"""案 v3 を 1 件ずつの表 (plan.json) に組み立て、不自然な所を洗い出す。本番には繋がない。"""
import json, math, re, sys
from collections import defaultdict
from datetime import date, timedelta
import openpyxl

D = sys.argv[1]
DROP = ("291dd2fb", "4d6ac712", "aed620bd", "e511c7c9", "58f06049", "b0f3dfcf", "eadad8ee")
week = json.load(open(f"{D}/week.json", encoding="utf-8"))
hist = json.load(open(f"{D}/history.json", encoding="utf-8"))
pat = {p["id"]: p for p in week["patients"]}
staff = {s["id"]: s for s in week["staff"]}
sid_by_name = {s["name"]: s["id"] for s in week["staff"]}
monday = date.fromisoformat(week["week_start"])
pfv = defaultdict(list)
for ln in open(f"{D}/pfv.txt", encoding="utf-8"):
    parts = ln.strip().split("|")
    if len(parts) == 5:
        pfv[parts[0]].append(dict(wd=int(parts[1]), t=parts[2], dur=int(parts[3]), mv=parts[4]))

def hm(t): h, m = map(int, t[:5].split(":")); return h * 60 + m
def fmt(m): return f"{m // 60:02d}:{m % 60:02d}"

visits = [v for v in week["visits"] if not v["id"].startswith(DROP)]
key = defaultdict(list)
for v in visits:
    key[(v["visit_date"], pat[v["patient_id"]]["name"], v["start_time"][:5])].append(v)
marks_by = defaultdict(list)
for m in week.get("special_marks") or []:
    marks_by[(m["patient_id"], (monday + timedelta(days=m["weekday"])).isoformat())].append(m)

rows = list(openpyxl.load_workbook(f"{D}/leveling.xlsx", read_only=True)["変更一覧"].iter_rows(values_only=True))[1:]
items = []
for r in rows:
    day, pname, cur_t, cur_s, new_t, new_s = r[:6]
    mo, dd = re.match(r"(\d+)/(\d+)", day).groups()
    iso = f"2026-{int(mo):02d}-{int(dd):02d}"
    if str(cur_t).startswith("追加"):
        p = next(p for p in week["patients"] if p["name"] == pname)
        m = marks_by[(p["id"], iso)][0]
        items.append(dict(id=f"add-{m['id']}", mark_id=m["id"], pid=p["id"], day=iso, name=pname, start=hm(new_t),
                          dur=int(m["duration_min"]), staff=new_s, cur_t=None, cur_s=None, new=True))
        continue
    v = key[(iso, pname, cur_t)].pop(0)
    items.append(dict(id=v["id"], pid=v["patient_id"], day=iso, name=pname, start=hm(new_t),
                      dur=hm(v["end_time"]) - hm(v["start_time"]), staff=new_s, cur_t=cur_t, cur_s=cur_s, new=False,
                      source=v["source"], office=v["course_office"], course=v["course_code"]))
for (iso, pname, t), left in key.items():  # 変わらない訪問
    for v in left:
        cs = v["primary_staff_id"] or v.get("course_staff_id")
        items.append(dict(id=v["id"], pid=v["patient_id"], day=iso, name=pname, start=hm(t),
                          dur=hm(v["end_time"]) - hm(v["start_time"]), staff=staff[cs]["name"] if cs else "担当なし",
                          cur_t=t, cur_s=staff[cs]["name"] if cs else "担当なし", new=False, source=v["source"],
                          office=v["course_office"], course=v["course_code"]))
json.dump(items, open(f"{D}/plan.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("件数", len(items))

# ---- 洗い出し ----
def wp(pid): return pat[pid]["weekly_pattern"] or {}
print("\n[1] 時刻を 60 分以上動かした訪問")
for it in sorted(items, key=lambda x: (x["day"], x["start"])):
    if it["new"]: continue
    mv = it["start"] - hm(it["cur_t"])
    if abs(mv) >= 60:
        w = wp(it["pid"]); wd = date.fromisoformat(it["day"]).weekday()
        mvb = [f["mv"] for f in pfv[it["pid"]] if f["wd"] == wd]
        print(f'  {it["day"][5:]} {it["name"]} {it["cur_t"]}→{fmt(it["start"])} ({mv:+d}分) 希望={w.get("time_type")} {w.get("preferred_start") or ""}-{w.get("preferred_end") or ""} 枠={mvb} → {it["staff"]}')

print("\n[2] 同じ利用者が同じ日に 2 回以上")
c = defaultdict(list)
for it in items: c[(it["pid"], it["day"])].append(it)
for (pid, d), lst in c.items():
    if len(lst) > 1: print("  ", d, pat[pid]["name"], [fmt(x["start"]) for x in lst])

print("\n[3] 週の回数: 希望の回数 と 案の件数 が違う人")
n = defaultdict(int)
for it in items: n[it["pid"]] += 1
for pid, k in n.items():
    f = wp(pid).get("frequency_per_week")
    try: f = int(f)
    except Exception: continue
    if f != k: print(f'  {pat[pid]["name"]} 希望 {f} / 案 {k}')

print("\n[4] 職員×日: 件数・最初と最後・一番長い空き（昼の窓の外で 90 分以上）")
by = defaultdict(list)
for it in items: by[(it["day"], it["staff"])].append(it)
for (d, s), lst in sorted(by.items()):
    lst.sort(key=lambda x: x["start"])
    gaps = [(lst[i]["start"] - (lst[i-1]["start"] + lst[i-1]["dur"]), fmt(lst[i-1]["start"] + lst[i-1]["dur"]), fmt(lst[i]["start"])) for i in range(1, len(lst))]
    big = [g for g in gaps if g[0] >= 120]
    flag = "  ←長い空き " + ", ".join(f"{a}〜{b}({g}分)" for g, a, b in big) if big else ""
    print(f'  {d[5:]} {s.split(chr(0x3000))[0]:6} {len(lst)} 件 {fmt(lst[0]["start"])}〜{fmt(lst[-1]["start"]+lst[-1]["dur"])}{flag}')

print("\n[5] 担当の継続: 直近 4 週で一度も担当していない職員が付く訪問 (初めての組み合わせ)")
seen = defaultdict(set)
for h in hist:
    if h["primary_staff_id"] in staff: seen[h["patient_id"]].add(staff[h["primary_staff_id"]]["name"])
first = [it for it in items if it["staff"] not in seen[it["pid"]] and seen[it["pid"]]]
print("  件数", len(first), "/", len(items), " (担当歴が無い利用者は除く)")
nohist = {it["pid"] for it in items if not seen[it["pid"]]}
print("  担当歴の無い利用者", len(nohist))
for it in sorted(first, key=lambda x: (x["day"], x["start"]))[:200]:
    pass
json.dump({"first_pairs": [(it["day"], it["name"], it["staff"]) for it in first]}, open(f"{D}/first_pairs.json", "w", encoding="utf-8"), ensure_ascii=False)
