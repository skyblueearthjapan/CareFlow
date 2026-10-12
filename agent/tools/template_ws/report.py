"""コースの作業場の帳票（1 枚の HTML）。1 枚目 = A3 横の時間割（行 = コース・列 = 月〜土・桃色 = 比べる相手と違う枠）、
2 枚目 = A4 縦の要点（曜日ごとの数・変わる枠・気をつけること・もしもの条件・作業の記録）。利用者名入り → docs/reports/agent/ の下だけ。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rk.report import FONT, PINK, _e, _icon_data_uri
from tools.courseplan import model as M
from tools.template_ws import draft as D
from tools.template_ws import ops as O
from tools.template_ws import problems as P

CSS = """
:root { --pink: %(pink)s; --ink: #2b2b2b; --mute: #6b6b6b; --line: #e6dfe2; --soft: #fdf1f4; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink); font-family: %(font)s; font-size: 9pt; line-height: 1.4; }
@page a3 { size: A3 landscape; margin: 7mm; }
@page a4 { size: A4 portrait; margin: 10mm; }
.pg-a3 { page: a3; padding: 6mm; }
.pg-a4 { page: a4; padding: 8mm; break-before: page; page-break-before: always; font-size: 9.5pt; }
header { display: flex; align-items: center; gap: 10px; border-bottom: 3px solid var(--pink); padding-bottom: 4px; margin-bottom: 6px; }
header img { width: 34px; height: 34px; }
header h1 { font-size: 14pt; margin: 0; }
header .meta { margin-left: auto; text-align: right; color: var(--mute); font-size: 8.5pt; }
h2 { font-size: 11pt; margin: 10px 0 4px; padding-left: 8px; border-left: 4px solid var(--pink); }
table { border-collapse: collapse; width: 100%%; }
th, td { border: 1px solid var(--line); padding: 3px 4px; vertical-align: top; text-align: left; }
th { background: var(--soft); font-weight: 600; white-space: nowrap; }
table.grid { table-layout: fixed; }
table.grid th.c { width: 16mm; }
.cell-staff { color: var(--mute); font-size: 8pt; border-bottom: 1px dotted var(--line); margin-bottom: 1px; }
.b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 8pt; }
.b.chg { background: var(--soft); color: #a3264b; font-weight: 600; }
.b .was { color: var(--mute); font-weight: 400; }
.b .sid { color: #9a9a9a; font-size: 7pt; }
td.chg { border: 1.5px solid var(--pink); }
td.off { background: #f6f6f6; }
.foot { color: var(--mute); font-size: 7.5pt; border-top: 1px dotted var(--line); margin-top: 1px; }
.strip { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 6px; }
.strip div { border: 1px solid var(--line); border-radius: 4px; padding: 2px 6px; font-size: 8.5pt; }
.strip b { color: var(--pink); }
ul { margin: 0; padding-left: 1.3em; }
.warn li { color: #a8323e; }
td.n { text-align: right; }
td.better { color: #1f7a4d; font-weight: 600; }
td.worse { color: #a8323e; font-weight: 600; }
footer { margin-top: 8px; color: var(--mute); font-size: 8pt; text-align: center; }
@media print { .pg-a3, .pg-a4 { padding: 0; } }
"""

ROW_ORDER_TAIL = ("コースなし", "人のいないコース", "置けなかった新しい方")


def _label_row(p: dict, cid) -> str:
    for c in p["courses"]:
        if c["id"] == cid:
            return c["label"][len(p["office"]):] or c["label"]
    return "コースなし" if cid is None else "人のいないコース"


def _row_key(r: str) -> tuple:
    if r in ROW_ORDER_TAIL:
        return (2, ROW_ORDER_TAIL.index(r), r)
    return (1 if r == M.MANAGER_LABEL or r.startswith(M.MANAGER_LABEL) else 0, 0, r)


def build_grid(cfg, draft: dict, vs: str = D.MASTER) -> dict:
    """{rows: [label], days: [{weekday, cells: {label: {staff, blocks, changed, count, travel}}}], metrics: [...]}"""
    oname, side = O._side(cfg, draft, vs)
    days, rows = [], set()
    for p in (P.build_day(draft, wd) for wd in P.operating_days(draft)):
        mine = P.draft_assign(draft, p)
        q, ref = side(p)
        ev = {c["id"]: c for c in M.evaluate(p, mine)["courses"]}
        cells = {}
        for c in p["courses"]:
            lab = _label_row(p, c["id"])
            who = M.short_name((c["staff"] or {}).get("name")) or ("仮の職員" if c.get("virtual") else "")
            e = ev.get(c["id"]) or {}
            cells[lab] = {"staff": who + ("（M）" if c["manager"] else ""), "blocks": [], "changed": False,
                          "count": e.get("count", 0), "travel": e.get("travel_min", 0)}
        ref_labels = {k: _label_row(q, a["course"]) for k, a in ref.items()}
        for s in sorted(p["slots"], key=lambda s: ((mine.get(s["key"]) or {}).get("start", s["start"]), s["slot_index"])):
            a = mine.get(s["key"])
            lab = _label_row(p, a["course"]) if a else "置けなかった新しい方"
            b = ref.get(s["key"])
            changed = (a or None) != (b or None)
            was = ""
            if changed and b:
                rl = ref_labels.get(s["key"], "")
                was = (rl if rl != lab else "") + ("" if b["start"] == (a or {}).get("start") else f" {M.fmt(b['start'])}")
            elif changed and not b:
                was = "新"
            st = (a or {}).get("start", s["start"])
            cell = cells.setdefault(lab, {"staff": "", "blocks": [], "changed": False, "count": 0, "travel": 0})
            cell["blocks"].append({"t": f"{M.fmt(st)}-{M.fmt(st + s['dur'])}", "name": M.short_name(s["patient"]) + "様",
                                   "red": s["locked"], "chg": changed, "was": was.strip(), "sid": s.get("sid", "")})
            if changed:
                cell["changed"] = True
                if b and ref_labels.get(s["key"]) in cells and ref_labels.get(s["key"]) != lab:
                    cells[ref_labels[s["key"]]]["changed"] = True
            if lab in ROW_ORDER_TAIL:
                cell["count"] += 1
        rows |= set(cells)
        days.append({"weekday": p["weekday"], "weekday_ja": p["weekday_ja"], "cells": cells})
    return {"vs": oname, "rows": sorted(rows, key=_row_key), "days": days}


def _grid_html(g: dict) -> str:
    out = ["<table class='grid'><tr><th class='c'>コース</th>"]
    out += [f"<th>{_e(d['weekday_ja'])}曜</th>" for d in g["days"]]
    out.append("</tr>")
    for r in g["rows"]:
        out.append(f"<tr><th class='c'>{_e(r)}</th>")
        for d in g["days"]:
            c = d["cells"].get(r)
            if c is None:
                out.append("<td class='off'></td>")
                continue
            cls = "chg" if c["changed"] else ""
            parts = [f"<div class='cell-staff'>{_e(c['staff'] or '—')}</div>"] if c["staff"] else []
            for b in c["blocks"]:
                was = f" <span class='was'>←{_e(b['was'])}</span>" if b["was"] else ""
                parts.append(f"<div class='b{' chg' if b['chg'] else ''}'>{_e(b['t'])} {_e(b['name'])}{'🔴' if b['red'] else ''}"
                             f"{was} <span class='sid'>{_e(b['sid'])}</span></div>")
            foot = f"{c['count']} 名" + (f"・移動 {c['travel']} 分" if c.get("travel") else "")
            parts.append(f"<div class='foot'>{_e(foot)}</div>")
            out.append(f"<td class='{cls}'>{''.join(parts)}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


METRIC_ROWS = [
    ("間に合わない組", "conflicts"), ("上限超え", "over_cap"), ("勤務時刻の外", "outside_shift"),
    ("固定イベントと重なる", "event_hits"), ("昼休み 30 分未満", "lunch_short"), ("NG・性別", "ng_or_sex"),
    ("コースなし", "no_course"), ("人のいないコース", "unstaffed"), ("希望の外", "outside_window"),
    ("移動の合計（分）", "travel_min"), ("正規の件数の差", "count_spread"), ("マネージャーの件数", "manager_visits"),
]


def _metrics_html(cfg, draft: dict, vs: str) -> tuple[str, dict]:
    cmp = O.compare(cfg, draft["name"], vs)
    out = [f"<table><tr><th>数えたもの（{_e(cmp['b'])} → 下書き）</th>"]
    out += [f"<th>{_e(d['weekday'])}</th>" for d in cmp["days"]]
    out.append("</tr>")
    for title, k in METRIC_ROWS:
        out.append(f"<tr><td>{_e(title)}</td>")
        for d in cmp["days"]:
            a, b = d["a"][k], d["b"][k]
            cls = "better" if a < b else ("worse" if a > b else "")
            out.append(f"<td class='n {cls}'>{b} → {a}</td>")
        out.append("</tr>")
    out.append("<tr><td>正規の件数</td>" + "".join(
        f"<td class='n'>{'/'.join(map(str, d['b']['regular_counts']))} → {'/'.join(map(str, d['a']['regular_counts']))}</td>"
        for d in cmp["days"]) + "</tr>")
    out.append("<tr><td>違う枠</td>" + "".join(f"<td class='n'>{d['differ']}</td>" for d in cmp["days"]) + "</tr></table>")
    return "".join(out), cmp


def render(cfg, draft: dict, vs: str = D.MASTER, icon_path=None) -> str:
    g = build_grid(cfg, draft, vs)
    ms = O.refresh_metrics(draft)
    icon = _icon_data_uri(icon_path) if icon_path else _icon_data_uri()
    css = CSS % {"pink": PINK, "font": FONT}
    title = f"コースの作業場 {draft['name']}（{draft['office']}）"
    meta = f"材料: {draft['snapshot'].get('extracted_at') or ''} に読んだマスター<br>比べる相手: {g['vs']}"
    img = f"<img src='{icon}' alt='らく助'>" if icon else ""
    head = f"<header>{img}<h1>{_e(title)}</h1><div class='meta'>{meta}</div></header>"
    tot = lambda k, side: sum(m[side][k] for m in ms)  # noqa: E731
    strip = [("変える枠", sum(m["changes"] - m["new_placed"] for m in ms)), ("新しい方を置いた枠", sum(m["new_placed"] for m in ms)),
             ("置けない新しい枠", sum(m["new_unplaced"] for m in ms)),
             ("間に合わない", f"{tot('conflicts', 'master')}→{tot('conflicts', 'draft')}"),
             ("上限超え", f"{tot('over_cap', 'master')}→{tot('over_cap', 'draft')}"),
             ("コースなし", f"{tot('no_course', 'master')}→{tot('no_course', 'draft')}"),
             ("赤ピンの枠のコース替え", sum(m["red_course_changes"] for m in ms))]
    parts = ["<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width, initial-scale=1'>",
             f"<title>{_e(title)}</title><style>{css}</style></head><body>",
             f"<section class='pg-a3'>{head}",
             "<div class='strip'>" + "".join(f"<div>{_e(k)} <b>{_e(v)}</b></div>" for k, v in strip) + "</div>",
             _grid_html(g),
             f"<footer>桃色 = {_e(g['vs'])}と違う枠（←は{_e(g['vs'])}でのコース・時刻／新 = 新しい枠）・🔴 = 赤ピン（曜日・時刻は動かさない）・"
             "右下の番号で rk template move ができます。型の下書きです。登録するまで予定もマスターも変わりません</footer></section>"]
    mt, cmp = _metrics_html(cfg, draft, vs)
    parts.append(f"<section class='pg-a4'>{head}<h2>曜日ごとの数（{_e(cmp['b'])} → 下書き・緑 = 良くなる）</h2>{mt}")
    if cmp["changed"]:
        parts.append(f"<h2>違う枠（{len(cmp['changed'])}）</h2><table><tr><th>番号</th><th>曜日</th><th>方</th><th>{_e(cmp['b'])}</th><th>下書き</th></tr>")
        for r in cmp["changed"]:
            parts.append(f"<tr><td>{_e(r['sid'])}</td><td>{_e(r['weekday'])}</td><td>{_e(r['patient'])}様{'🔴' if r['red'] else ''}"
                         f"{'（新）' if r['new'] else ''}</td><td>{_e(r['b'])}</td><td>{_e(r['a'])}</td></tr>")
        parts.append("</table>")
    warn = [n for m in ms for n in m["notes"]]
    warn += [f"{m['weekday_ja']}曜 下書きでも残る: {x}" for m in ms for x in m["draft"]["issues"]]
    warn += [f"まだ枠の無い方 {sp['patient'].get('code')}: {sp['reason']}" for sp in P.candidate_specs(draft) if sp["reason"]]
    warn += [f"置けなかった新しい枠 {draft['sids'].get(k, k)}: {v}" for k, v in sorted((draft.get("unplaced") or {}).items())]
    if warn:
        parts.append("<h2>気をつけること</h2><ul class='warn'>" + "".join(f"<li>{_e(w)}</li>" for w in warn) + "</ul>")
    sc = draft["scenario"]
    cond = [("赤ピン", "守る（曜日・時刻を動かさない）" if sc.get("red", "keep") == "keep" else "全部外した試算"),
            ("希望の無い方", "今の時刻のまま" if sc.get("no_pref", "fixed") == "fixed" else "終日の範囲"),
            ("正規の上限", f"1 日 {sc.get('cap', 6)} 名")]
    if sc.get("staff_count"):
        cond.append(("もしも: 職員の数", "・".join(f"{M.WD[int(k)]} {v} 人" for k, v in sorted(sc["staff_count"].items()))))
    if sc.get("drop_staff"):
        cond.append(("もしも: 外した職員", "・".join(sc["drop_staff"])))
    if sc.get("add_patients"):
        cond.append(("もしも: 足した方", "・".join(sc["add_patients"])))
    if sc.get("prefs"):
        cond.append(("もしも: 希望の時間帯", "・".join(f"{c} {M.WD[int(w)]} {M.fmt(r[0])}〜{M.fmt(r[1])}"
                                                   for c, d in sc["prefs"].items() for w, r in d.items())))
    parts.append("<h2>条件</h2><table>" + "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in cond) + "</table>")
    hist = draft.get("history") or []
    if hist:
        parts.append("<h2>作業の記録</h2><ul>" + "".join(f"<li>{_e(h['at'])} {_e(h['detail'])}</li>" for h in hist[-12:]) + "</ul>")
    parts.append("<footer>らく助エージェントの型の下書き。登録（rk template commit → 了承 → バックアップ → 反映）するまで、マスターも予定も変わりません</footer>")
    parts.append("</section></body></html>")
    return "\n".join(parts)


def write(cfg, name: str, vs: str = D.MASTER, out: str | None = None) -> Path:
    draft = D.load(cfg, name)
    path = Path(out) if out else Path(cfg.reports_dir) / date.today().isoformat() / (
        f"template-{name}" + ("" if vs == D.MASTER else f"-vs-{vs}") + ".html")
    from tools.courseplan.run import CoursePlanError, check_out_dir

    try:
        check_out_dir(path.parent)
    except CoursePlanError as e:
        raise D.TemplateError(str(e)) from None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(cfg, draft, vs), encoding="utf-8")
    return path
