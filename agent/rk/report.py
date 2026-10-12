"""案の帳票（1 枚の HTML・外部ファイルなし）。A4 縦 = 一覧／--a3 = A3 横の時間割。

--a3 は plan["timelines"]（日ごとの list）があれば 1 日 1 枚（改ページ）で全日を描き、無ければ plan["timeline"]（1 日分）。

timeline の形: {"date": "2026-10-22", "rows": [{"name": "鈴木", "blocks": [
    {"start": "9:30", "end": "10:20", "label": "伊東様", "changed": true}]}]}
"""

from __future__ import annotations

import base64
import html
import re
from datetime import date
from pathlib import Path

from rk import RkError
from rk.config import ICON_PATH, Config
from rk.opsbase import Registry, load_all

PINK = "#e15a7f"
FONT = "'Noto Sans JP', 'Hiragino Kaku Gothic ProN', 'Yu Gothic', 'Meiryo', sans-serif"
FOOTER = "らく助エージェントの案。反映するまで予定は変わりません"


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _icon_data_uri(icon_path: Path = ICON_PATH) -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(icon_path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _minutes(t: str) -> int:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(t))
    if not m:
        raise RkError(f"時刻の形が違います: {t}（例 9:30）")
    return int(m.group(1)) * 60 + int(m.group(2))


def _target_text(target: dict) -> str:
    parts = []
    if target.get("date"):
        parts.append(str(target["date"]))
    if target.get("iso_week"):
        parts.append(f"{target.get('iso_year', '')} 年 第 {target['iso_week']} 週")
    for k, v in target.items():
        if k not in ("date", "iso_week", "iso_year"):
            parts.append(f"{k}: {v}")
    return "・".join(parts)


CSS = """
:root { --pink: %(pink)s; --ink: #2b2b2b; --mute: #6b6b6b; --line: #e6dfe2; --soft: #fdf1f4; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink); font-family: %(font)s; font-size: 10.5pt; line-height: 1.55; }
.page { padding: 12mm 12mm 16mm; }
header { display: flex; align-items: center; gap: 10px; border-bottom: 3px solid var(--pink); padding-bottom: 6px; margin-bottom: 10px; }
header img { width: 40px; height: 40px; }
header h1 { font-size: 15pt; margin: 0; }
header .meta { margin-left: auto; text-align: right; color: var(--mute); font-size: 9pt; }
h2 { font-size: 11.5pt; margin: 14px 0 6px; padding-left: 8px; border-left: 4px solid var(--pink); }
ul { margin: 0; padding-left: 1.3em; }
.summary { background: var(--soft); border-radius: 6px; padding: 8px 12px; }
.summary li { font-weight: 600; }
table { border-collapse: collapse; width: 100%%; }
th, td { border: 1px solid var(--line); padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: var(--soft); font-weight: 600; white-space: nowrap; }
td.n { text-align: right; color: var(--mute); width: 2.5em; }
.tag { display: inline-block; border: 1px solid var(--pink); color: var(--pink); border-radius: 3px; padding: 0 4px; font-size: 8.5pt; }
.warn li { color: #a8323e; }
footer { margin-top: 14px; border-top: 1px solid var(--line); padding-top: 6px; color: var(--mute); font-size: 9pt; text-align: center; }
.tl { position: relative; }
.tl-axis, .tl-row { display: flex; }
.tl-name { width: 24mm; flex: none; font-weight: 600; padding: 2px 4px; border-bottom: 1px solid var(--line); }
.tl-track { position: relative; flex: 1; height: 13mm; border-bottom: 1px solid var(--line); }
.tl-axis .tl-track { height: 6mm; border-bottom: 2px solid var(--pink); }
.tl-hour { position: absolute; top: 0; bottom: 0; border-left: 1px dashed #ddd; font-size: 8pt; color: var(--mute); padding-left: 2px; }
.blk { position: absolute; top: 1.2mm; bottom: 1.2mm; border-radius: 3px; background: #eceaea; border: 1px solid #c9c4c6; font-size: 7.5pt; line-height: 1.2; padding: 1px 2px; overflow: hidden; }
.blk.chg { background: var(--soft); border: 1.5px solid var(--pink); font-weight: 600; }
.tl-day { break-before: page; page-break-before: always; }
.tl-day:first-of-type { break-before: auto; page-break-before: auto; }
@media print { .page { padding: 0; } }
"""


def render_html(plan: dict, a3: bool = False, registry: Registry | None = None, icon_path: Path = ICON_PATH) -> str:
    reg = registry or load_all()
    days = _timelines(plan) if a3 else []
    if a3 and not days:
        raise RkError("--a3 には案の timelines（日ごとの list）か timeline（{date, rows:[{name, blocks:[...]}]}）が要ります")
    size = "A3 landscape" if a3 else "A4 portrait"
    css = CSS % {"pink": PINK, "font": FONT} + f"\n@page {{ size: {size}; margin: 10mm; }}\n"
    icon = _icon_data_uri(icon_path)
    title = plan.get("title", "")
    parts = [
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{_e(title)}</title><style>{css}</style></head><body><div class='page'>",
        "<header>",
        f"<img src='{icon}' alt='らく助'>" if icon else "",
        f"<h1>{_e(title)}</h1>",
        f"<div class='meta'>{_e(_target_text(plan.get('target') or {}))}<br>案の種類: {_e(plan.get('kind', ''))}</div>",
        "</header>",
    ]
    if plan.get("summary"):
        parts.append("<h2>要点</h2><div class='summary'><ul>")
        parts += [f"<li>{_e(s)}</li>" for s in plan["summary"]]
        parts.append("</ul></div>")
    if len(days) == 1:
        parts.append(_timeline_html(days[0]))
    elif days:
        # 1 日 1 枚（A3 横）。要点の後に日ごとの改ページ、操作の一覧は最後の日の後
        parts += [f"<section class='tl-day'>{_timeline_html(tl)}</section>" for tl in days]
    ops = plan.get("ops") or []
    parts.append(f"<h2>行う操作（{len(ops)} 件）</h2><table><tr><th>#</th><th>内容</th><th>操作</th><th>元に戻す</th></tr>")
    for i, op in enumerate(ops, 1):
        impl = reg.get(op.get("op", ""))
        kind = impl.summary if impl and impl.summary else op.get("op", "")
        note = impl.undo_note(op) if impl else None
        undo = ("API で戻せる" if note is None else f"<span class='tag'>{_e(note)}</span>") if impl else "—"
        parts.append(f"<tr><td class='n'>{i}</td><td>{_e(op.get('label', ''))}</td><td>{_e(kind)}</td><td>{undo}</td></tr>")
    parts.append("</table>")
    parts.append(_unplaced_html(plan))
    if plan.get("checks"):
        parts.append("<h2>確かめた決まり</h2><ul>" + "".join(f"<li>{_e(c)}</li>" for c in plan["checks"]) + "</ul>")
    if plan.get("warnings"):
        parts.append("<h2>気をつけること</h2><ul class='warn'>" + "".join(f"<li>{_e(w)}</li>" for w in plan["warnings"]) + "</ul>")
    parts.append(f"<footer>{_e(FOOTER)}</footer></div></body></html>")
    return "\n".join(p for p in parts if p)


def _unplaced_html(plan: dict) -> str:
    """入らなかった訪問と、rk plan resolve で決めたこと（誰が・いつ）。"""
    items = plan.get("unplaced") or []
    if not items:
        return ""
    res = {r.get("visit_id"): r for r in plan.get("resolutions") or []}
    out = ["<h2>入らなかった訪問と決めたこと</h2><table><tr><th>訪問</th><th>入らない理由</th><th>決めたこと</th></tr>"]
    for u in items:
        r = res.get(u.get("visit_id"))
        if r:
            what = (r.get("choice_ja") or r.get("choice", "")) + (f"（{r['staff']}）" if r.get("staff") else "")                 + (f"（{r['start']} へ）" if r.get("start") else "")
            cell = f"{_e(what)}<br>{_e(r.get('approved', ''))}・{_e(r.get('at', ''))}"
        else:
            cell = "<span class='tag'>まだ決めていない</span>"
        out.append(f"<tr><td>{_e(u.get('label', ''))}</td><td>{_e(u.get('reason', ''))}。{_e(u.get('hint', ''))}</td>"
                   f"<td>{cell}</td></tr>")
    out.append("</table>")
    return "".join(out)


def _timelines(plan: dict) -> list[dict]:
    tls = plan.get("timelines")
    if isinstance(tls, list):
        days = [t for t in tls if isinstance(t, dict) and t.get("rows")]
        if days:
            return sorted(days, key=lambda t: str(t.get("date", "")))
    tl = plan.get("timeline")
    return [tl] if isinstance(tl, dict) else []


def _timeline_html(tl: dict) -> str:
    rows = tl.get("rows") or []
    spans = [(_minutes(b["start"]), _minutes(b["end"])) for r in rows for b in r.get("blocks") or []]
    lo = min([s for s, _ in spans] + [8 * 60]) // 60 * 60
    hi = -(-max([e for _, e in spans] + [18 * 60]) // 60) * 60
    width = max(hi - lo, 60)

    def pct(m: int) -> float:
        return (m - lo) * 100.0 / width

    out = [f"<h2>時間割 {_e(tl.get('date', ''))}（桃色 = 変わるところ）</h2><div class='tl'>"]
    hours = "".join(f"<div class='tl-hour' style='left:{pct(h * 60):.3f}%'>{h}</div>"
                    for h in range(lo // 60, hi // 60 + 1) if h * 60 < hi)
    out.append(f"<div class='tl-axis'><div class='tl-name'></div><div class='tl-track'>{hours}</div></div>")
    for r in rows:
        blocks = []
        for b in r.get("blocks") or []:
            s, e = _minutes(b["start"]), _minutes(b["end"])
            cls = "blk chg" if b.get("changed") else "blk"
            blocks.append(
                f"<div class='{cls}' style='left:{pct(s):.3f}%;width:{max(pct(e) - pct(s), 0.4):.3f}%' "
                f"title='{_e(b['start'])}-{_e(b['end'])} {_e(b.get('label', ''))}'>"
                f"{_e(b['start'])}<br>{_e(b.get('label', ''))}</div>")
        out.append(f"<div class='tl-row'><div class='tl-name'>{_e(r.get('name', ''))}</div>"
                   f"<div class='tl-track'>{''.join(blocks)}</div></div>")
    out.append("</div>")
    return "".join(out)


def report_path(cfg: Config, plan: dict, a3: bool = False, today: date | None = None) -> Path:
    today = today or date.today()
    target = plan.get("target") or {}
    key = target.get("date") or (f"w{target['iso_week']}" if target.get("iso_week") else "")
    slug = re.sub(r"[^a-z0-9-]+", "-", f"{plan.get('kind', 'plan')}-{key}".lower()).strip("-") or "plan"
    if a3:
        slug += "-a3"
    return cfg.reports_dir / today.isoformat() / f"{slug}.html"


def write_report(cfg: Config, plan: dict, a3: bool = False, out: Path | None = None,
                 registry: Registry | None = None) -> Path:
    path = out or report_path(cfg, plan, a3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(plan, a3, registry), encoding="utf-8")
    return path
