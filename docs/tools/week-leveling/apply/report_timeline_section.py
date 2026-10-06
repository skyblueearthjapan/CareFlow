"""週42 ならし案の「日ごとの予定」を時間軸の表にして week42-report.html の §8 に差し込む。"""
import json, math, re, sys, html
from collections import defaultdict
from datetime import date, timedelta
import openpyxl

D = sys.argv[1]
week = json.load(open(f"{D}/week.json", encoding="utf-8"))
wb = openpyxl.load_workbook(f"{D}/leveling.xlsx", read_only=True)
rows = list(wb["変更一覧"].iter_rows(values_only=True))[1:]

pat = {p["id"]: p for p in week["patients"]}
pat_by_name = defaultdict(list)
for p in week["patients"]:
    pat_by_name[p["name"]].append(p)
staff = {s["id"]: s for s in week["staff"]}
staff_by_name = {s["name"]: s for s in week["staff"]}
monday = date.fromisoformat(week["week_start"])

def hm(t): h, m = map(int, t[:5].split(":")); return h * 60 + m
def fmt(m): return f"{m // 60:02d}:{m % 60:02d}"

# 週の訪問を (日, 利用者名, 今の時刻) で引けるように
vis_key = defaultdict(list)
for v in week["visits"]:
    vis_key[(v["visit_date"], pat[v["patient_id"]]["name"], v["start_time"][:5])].append(v)

# 二重 (同じ利用者が同じ日に 2 件以上・同じグループは除く)
per_pd = defaultdict(set)
for v in week["visits"]:
    per_pd[(v["patient_id"], v["visit_date"])].add(v["visit_group_id"] or v["id"])
dup = {k for k, s in per_pd.items() if len(s) > 1}

WD = "月火水木金土日"
plan = defaultdict(list)  # (iso_date, staff_name) -> items
for r in rows:
    day, pname, cur_t, cur_s, new_t, new_s, shift = r[:7]
    m, dd = re.match(r"(\d+)/(\d+)", day).groups()
    iso = f"2026-{int(m):02d}-{int(dd):02d}"
    cands = vis_key.get((iso, pname, cur_t))
    v = cands.pop(0) if cands else None
    dur = (hm(v["end_time"]) - hm(v["start_time"])) if v else 35
    p = pat[v["patient_id"]] if v else pat_by_name[pname][0]
    plan[(iso, new_s)].append(dict(
        start=hm(new_t), end=hm(new_t) + dur, name=pname, cur_t=cur_t, cur_s=cur_s,
        moved=(cur_t != new_t), changed=(cur_s != new_s), src=(v or {}).get("source"),
        office=(v or {}).get("course_office"), dup=(v and (v["patient_id"], iso) in dup),
        lat=p.get("lat"), lng=p.get("lng"), pid=p["id"],
        group=(v or {}).get("visit_group_id"),
    ))

# 変更一覧には変わらない訪問が出ないので、残りは今の担当・時刻のまま足す
for (iso, pname, t), left in vis_key.items():
    for v in left:
        sname = staff[v["primary_staff_id"]]["name"] if v["primary_staff_id"] in staff else "担当なし"
        p = pat[v["patient_id"]]
        plan[(iso, sname)].append(dict(
            start=hm(v["start_time"]), end=hm(v["end_time"]), name=pname, cur_t=t, cur_s=sname,
            moved=False, changed=False, src=v["source"], office=v["course_office"],
            dup=(v["patient_id"], iso) in dup, lat=p.get("lat"), lng=p.get("lng"), pid=p["id"],
            group=v["visit_group_id"]))

events = defaultdict(list)
for e in week["events"] or []:
    s = staff.get(e["staff_id"])
    if not s or e["date"] != e["end_date"]:
        continue
    events[(e["date"], s["name"])].append((hm(e["start"]), hm(e["end"])))

# 休み (週間シフト is_on=false or その週の off)
off = set()
for sh in week["shifts"] or []:
    if sh["is_on"] is False and sh["staff_id"] in staff:
        off.add((staff[sh["staff_id"]]["name"], sh["weekday"]))
for o in week["weekly_overrides"] or []:
    if o["override_type"] == "off" and o["staff_id"] in staff:
        off.add((staff[o["staff_id"]]["name"], o["weekday"]))

def travel(a, b):
    if a["pid"] == b["pid"] or (a["lat"] == b["lat"] and a["lng"] == b["lng"]):
        return 0
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    km = 2 * R * math.asin(math.sqrt(h))
    return round(km / 20 * 60)

order = [s["name"] for s in sorted(week["staff"], key=lambda s: s["code"])]
T0, T1, PX = 9 * 60, 18 * 60 + 30, 1.6  # 1 分 = 1.6px
esc = html.escape

out = ['<div class="legend"><span class="lg v">訪問</span><span class="lg mv">時刻を動かした</span>'
       '<span class="lg ch">担当が変わる</span><span class="lg dp">二重（要判断）</span>'
       '<span class="lg ev">朝会・予定</span><span class="lg ln">昼休みの窓 11:30〜13:30</span></div>']
for i in range(6):
    dt = monday + timedelta(days=i)
    iso = dt.isoformat()
    names = [n for n in order if plan.get((iso, n)) or (events.get((iso, n)) and (n, i) not in off)]
    names = [n for n in names if plan.get((iso, n))] + [n for n in names if not plan.get((iso, n))]
    names = sorted(names, key=order.index)
    offs = [n.split("　")[0] for n in order if (n, i) in off]
    total = sum(len(plan.get((iso, n), [])) for n in names)
    holiday = "・スポーツの日" if iso == "2026-10-12" else ""
    out.append(f'<div class="day"><h3>{dt.month}/{dt.day}（{WD[i]}{holiday}）<span class="dsub">訪問 {total} 件'
               + (f'・休み: {"・".join(offs)}' if offs else "") + '</span></h3>')
    out.append('<div class="grid-wrap"><div class="grid" style="grid-template-columns:44px repeat(%d,minmax(118px,1fr))">' % len(names))
    # ヘッダ
    out.append('<div class="hd"></div>')
    for n in names:
        its = plan.get((iso, n), [])
        role = "（M）" if staff_by_name[n]["role"] != "staff" else ""
        out.append(f'<div class="hd">{esc(n.split(chr(0x3000))[0])}{role}<br><small>{len(its)} 件</small></div>')
    height = (T1 - T0) * PX
    # 時刻の目盛り
    ticks = "".join(f'<div class="tick" style="top:{(h*60-T0)*PX}px">{h}:00</div>' for h in range(9, 19))
    out.append(f'<div class="col axis" style="height:{height}px">{ticks}</div>')
    for n in names:
        its = sorted(plan.get((iso, n), []), key=lambda x: x["start"])
        cell = [f'<div class="lunch" style="top:{(690-T0)*PX}px;height:{120*PX}px"></div>']
        cell += [f'<div class="hl" style="top:{(h*60-T0)*PX}px"></div>' for h in range(10, 19)]
        for s, e in events.get((iso, n), []):
            lab = "朝会" if (s, e) == (540, 555) else "予定"
            cell.append(f'<div class="ev" style="top:{(max(s,T0)-T0)*PX}px;height:{max(e-s,12)*PX}px">{fmt(s)} {lab}</div>')
        prev = None
        for it in its:
            cls = "v" + (" dp" if it["dup"] else "") + (" mv" if it["moved"] else "") + (" ch" if it["changed"] and not it["moved"] else "")
            note = []
            if it["moved"]:
                note.append(f'元 {it["cur_t"]}')
            if it["cur_s"] != n and it["cur_s"] != "担当なし":
                note.append(f'今 {it["cur_s"].split(chr(0x3000))[0]}')
            if it["office"] == "津":
                note.append("都賀")
            if it["dup"]:
                note.append("二重")
            if prev:
                tv = travel(prev, it)
                gap = it["start"] - prev["end"]
                cell.append(f'<div class="mvline" style="top:{(prev["end"]-T0)*PX}px;height:{max(gap,0)*PX}px"><span>{"移動 "+str(tv)+"分" if tv else "同じ建物"}</span></div>')
            title = f'{fmt(it["start"])}〜{fmt(it["end"])} {it["name"]} / 今: {it["cur_t"]} {it["cur_s"]}'
            cell.append(f'<div class="{cls}" title="{esc(title)}" style="top:{(it["start"]-T0)*PX}px;height:{(it["end"]-it["start"])*PX-2}px">'
                        f'<b>{fmt(it["start"])}</b> {esc(it["name"].replace(chr(0x3000)," "))}'
                        + (f'<br><small>{esc("・".join(note))}</small>' if note else "") + '</div>')
            prev = it
        out.append(f'<div class="col" style="height:{height}px">{"".join(cell)}</div>')
    out.append('</div></div></div>')

CSS = """
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 4px;font-size:12px}
.lg{padding:1px 8px;border-radius:4px;border:1px solid var(--line)}
.lg.v{background:var(--v-bg)} .lg.mv{background:var(--v-bg);border-left:4px solid var(--warn)}
.lg.ch{background:var(--v-bg);border-left:4px solid var(--info)} .lg.dp{background:var(--ng-bg);border:2px solid var(--ng)}
.lg.ev{background:var(--ev-bg)} .lg.ln{background:var(--ln-bg)}
.day{margin:18px 0 26px}
.day h3{font-size:17px;margin:0 0 6px;border-bottom:2px solid var(--line);padding-bottom:4px}
.dsub{font-size:13px;color:var(--muted);font-weight:400;margin-left:10px}
.grid-wrap{overflow-x:auto}
.grid{display:grid;gap:0 6px;min-width:max-content}
.hd{position:sticky;top:0;background:var(--card);font-weight:700;font-size:13px;text-align:center;padding:4px 0;border-bottom:1px solid var(--line);z-index:3}
.hd small{font-weight:400;color:var(--muted)}
.col{position:relative;border-left:1px solid var(--line)}
.axis{border-left:none}
.tick{position:absolute;right:4px;font-size:11px;color:var(--muted);transform:translateY(-7px)}
.hl{position:absolute;left:0;right:0;border-top:1px dashed var(--line)}
.lunch{position:absolute;left:0;right:0;background:var(--ln-bg)}
.v{position:absolute;left:3px;right:3px;background:var(--v-bg);border:1px solid var(--v-ln);border-radius:5px;
   font-size:12px;line-height:1.3;padding:2px 5px;overflow:hidden;z-index:2}
.v.mv{border-left:4px solid var(--warn)} .v.ch{border-left:4px solid var(--info)}
.v.dp{background:var(--ng-bg);border:2px solid var(--ng)}
.v small{color:var(--muted);font-size:11px}
.ev{position:absolute;left:3px;right:3px;background:var(--ev-bg);border-radius:4px;font-size:10px;color:var(--muted);padding:0 4px;overflow:hidden;z-index:1}
.mvline{position:absolute;left:50%;border-left:1px dotted var(--muted);z-index:1}
.mvline span{position:absolute;left:4px;top:50%;transform:translateY(-50%);font-size:10px;color:var(--muted);white-space:nowrap;background:var(--card);padding:0 2px}
"""
VARS_L = "--v-bg:#fdf1f4;--v-ln:#f2b8c8;--ev-bg:#e9ecef;--ln-bg:#fbf6e6;"
VARS_D = "--v-bg:#3a2530;--v-ln:#7a4558;--ev-bg:#2c3038;--ln-bg:#2b2818;"

rp = f"{D}/week42-report.html"
src = open(rp, encoding="utf-8").read()
src = src.replace("--info-bg:#eef3fb; --info:#24508f;", "--info-bg:#eef3fb; --info:#24508f;" + VARS_L, 1)
src = src.replace("--info-bg:#1a2536; --info:#8db4ec;\n  }\n}", "--info-bg:#1a2536; --info:#8db4ec;" + VARS_D + "\n  }\n}", 1)
src = src.replace("--info-bg:#1a2536; --info:#8db4ec;\n}", "--info-bg:#1a2536; --info:#8db4ec;" + VARS_D + "\n}", 1)
src = src.replace("@media print", CSS + "\n@media print", 1)
src = src.replace("main{max-width:980px;", "main{max-width:1320px;", 1)
new8 = ('<section>\n  <h2>8. 日ごとの予定（ならし案）</h2>\n'
        '  <p>横に職員、縦に時刻（9:00〜18:30）。カードにマウスを乗せると今の時刻と担当が出ます。'
        '「元」は今の時刻、「今」は訪問に直接付いている今の担当（作り直した週はほとんどの訪問がコースの担当だけなので出ません）。移動は直線 20km/h の目安（ゆとり 8 分は含まない）。'
        '細かい順路は <a href="leveling-a4.pdf">leveling-a4.pdf</a>、全件の一覧は <a href="leveling.xlsx">leveling.xlsx</a> の「変更一覧」。</p>\n'
        + "\n".join(out) + "\n</section>")
src = re.sub(r"<!-- 8\. 案の全体 -->.*?</section>", "<!-- 8. 案の全体 -->\n" + new8.replace("\\", "\\\\"), src, count=1, flags=re.S)
open(rp, "w", encoding="utf-8").write(src)
print("ok", sum(len(v) for v in plan.values()), "items")
