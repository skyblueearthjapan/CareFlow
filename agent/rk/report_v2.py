"""案の帳票 v2（PO 2026-10-12 の「案の出し方」標準）。1 枚の HTML・外部ファイル・スクリプトなし。

順序: 1 結論（3 行まで）→ 2 気になる点・人が足りない所（選択肢つき）→ 3 変わるところ（理由つき）
      → 4 今回だけの条件 → 5 案 N との違い（作り直しのとき）→ 6 確かめの問い → くわしい確かめ。
--a3 は日ごとの時間割（変わるブロックを色＋印で強調・変わらないものは薄く・「←元の担当」）を 1 日 1 枚。

追加の案の項目（すべて任意。無ければ summary / unplaced / warnings から補う）:
  headline: [str, ...]（3 行まで） / ops[i].reason: str / issues: [{severity: info|warn, text, options: [str]}]
  conditions: [str | {text, approved}] / compare_to: 前の案の id / diff: [str] か {summary:[str]}
timeline の blocks: {start, end, label, changed, from?, kind?}（from = 元の担当や時刻）。rows[].before_blocks を付けると「変更前」も並べる。
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from rk.config import ICON_PATH, Config
from rk.opsbase import Registry, load_all
from rk.report import _icon_data_uri, _minutes, _target_text, _timelines, report_path

PINK = "#e15a7f"
FONT = "'Zen Maru Gothic', 'Noto Sans JP', 'Hiragino Maru Gothic ProN', 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', 'Yu Gothic', 'Meiryo', sans-serif"
CIRCLED = "①②③④⑤⑥⑦⑧⑨"
FOOTER = "らく助エージェントの案です。「反映する」とお返事をいただくまで、予定は変わりません"
NOT_UNDOABLE = "画面からは元に戻せません"  # 理由やバックアップの話は管理者に出さない
ASK = ["この案で反映する", "ここを直してほしい（どこをどう変えたいか教えてください）", "やめる（何も変えません）"]
DEFAULT_OPTIONS = ["時刻をずらして入れる", "応援を 1 名お願いする", "いったん保留にする（プールに置く）"]

MAX_HEADLINE = 3


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _c(i: int) -> str:
    return CIRCLED[i] if i < len(CIRCLED) else f"({i + 1})"


# ---- 案の項目の取り出し（無いものは補う） ----------------------------------------------------

def headline_of(plan: dict) -> list[str]:
    h = [str(x) for x in plan.get("headline") or [] if str(x).strip()]
    return (h or [str(x) for x in plan.get("summary") or []])[:MAX_HEADLINE]


def issues_of(plan: dict) -> list[dict]:
    out = []
    for it in plan.get("issues") or []:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            out.append({"severity": "warn" if it.get("severity") == "warn" else "info",
                        "text": str(it["text"]), "options": [str(o) for o in it.get("options") or []]})
    if plan.get("issues") is None:  # 書かれていない案は、入らない訪問（まだ決めていないもの）から補う
        res = {r.get("visit_id") for r in plan.get("resolutions") or []}
        for u in plan.get("unplaced") or []:
            if u.get("visit_id") in res:
                continue
            why = "。".join(x for x in (str(u.get("reason", "")), str(u.get("hint", ""))) if x)
            out.append({"severity": "warn", "text": f"{u.get('label', '')} が入りません。{why}", "options": list(DEFAULT_OPTIONS)})
    return out


def conditions_of(plan: dict) -> list[tuple[str, str]]:
    out = []
    for c in plan.get("conditions") or []:
        if isinstance(c, dict):
            out.append((str(c.get("text", "")), str(c.get("approved", ""))))
        elif str(c).strip():
            out.append((str(c), ""))
    return out


def diff_of(plan: dict) -> list[str]:
    d = plan.get("diff")
    if isinstance(d, dict):
        d = d.get("summary")
    return [str(x) for x in d or []]


def proposal_warnings(plan: dict) -> list[str]:
    """`rk plan check` が出す「分かりやすさ」の注意（誤りではない）。"""
    w = []
    if not [x for x in plan.get("headline") or [] if str(x).strip()]:
        w.append("headline（結論 3 行まで）がありません。画面と帳票の先頭が空になります")
    ops = [o for o in plan.get("ops") or [] if isinstance(o, dict)]
    miss = [i + 1 for i, o in enumerate(ops) if not str(o.get("reason", "")).strip()]
    if miss:
        w.append(f"reason（変える理由）が無い操作があります: {len(miss)} / {len(ops)} 件（例: {', '.join(map(str, miss[:5]))} 番）")
    if plan.get("unplaced") and plan.get("issues") is None:
        w.append("入らない訪問があるのに issues（気になる点と選択肢）がありません。画面には既定の選択肢で出ます")
    if plan.get("compare_to") and not diff_of(plan):
        w.append("compare_to があるのに diff（案との違い）がありません")
    return w


def proposal_errors(plan: dict) -> list[str]:
    """書かれているなら形が正しいこと（任意項目なので、無いのは誤りではない）。"""
    e = []
    h = plan.get("headline")
    if h is not None and (not isinstance(h, list) or not all(isinstance(x, str) for x in h) or len(h) > MAX_HEADLINE):
        e.append(f"headline は文字列のリスト（{MAX_HEADLINE} 件まで）です")
    iss = plan.get("issues")
    if iss is not None:
        if not isinstance(iss, list):
            e.append("issues はリストです")
        else:
            for i, it in enumerate(iss):
                if not isinstance(it, dict) or not isinstance(it.get("text"), str) or it.get("severity") not in ("info", "warn"):
                    e.append(f"issues[{i}] は {{severity: info|warn, text, options[]}} です")
                elif not isinstance(it.get("options", []), list):
                    e.append(f"issues[{i}].options はリストです")
    c = plan.get("conditions")
    if c is not None and (not isinstance(c, list) or not all(isinstance(x, (str, dict)) for x in c)):
        e.append("conditions は文字列（か {text, approved}）のリストです")
    if plan.get("compare_to") is not None and not isinstance(plan["compare_to"], str):
        e.append("compare_to は前の案の id（文字列）です")
    for i, op in enumerate(plan.get("ops") or []):
        if isinstance(op, dict) and "reason" in op and not isinstance(op["reason"], str):
            e.append(f"ops[{i}].reason は文字列です")
    return e


# ---- スタイル -----------------------------------------------------------------------------

CSS = """
:root { --pink: %(pink)s; --pink-d: #b83a5e; --ink: #1d1d1f; --mute: #4f4f55; --line: #cfc6ca; --soft: #fdf1f4;
        --chg: #0b5cad; --chg-bg: #dcecfb; --warn: #8a3b00; --warn-bg: #fff1dc; --warn-line: #d9822b; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink); font-family: %(font)s; font-size: 12pt; line-height: 1.65; }
.page { max-width: 210mm; margin: 0 auto; padding: 12mm 12mm 14mm; }
header { display: flex; align-items: center; gap: 12px; border-bottom: 4px solid var(--pink); padding-bottom: 8px; margin-bottom: 12px; }
header img { width: 48px; height: 48px; }
header h1 { font-size: 18pt; line-height: 1.3; margin: 0; }
header .meta { margin-left: auto; text-align: right; color: var(--mute); font-size: 10.5pt; }
h2 { font-size: 14pt; margin: 18px 0 8px; padding: 2px 0 2px 10px; border-left: 6px solid var(--pink); }
h2 .n { color: var(--pink-d); margin-right: 4px; }
.headline { background: var(--soft); border: 2px solid var(--pink); border-radius: 10px; padding: 10px 16px; }
.headline ul { margin: 0; padding-left: 1.2em; }
.headline li { font-size: 14pt; font-weight: 700; margin: 2px 0; }
.issue { border: 2px solid var(--line); border-radius: 10px; padding: 8px 14px; margin: 8px 0; break-inside: avoid; }
.issue.warn { background: var(--warn-bg); border-color: var(--warn-line); }
.issue .t { font-weight: 700; }
.issue.warn .t::before { content: "▲ 気になります　"; color: var(--warn); }
.issue.info .t::before { content: "● お知らせ　"; color: var(--chg); }
.opts { margin: 4px 0 0; padding: 0; list-style: none; }
.opts li { margin: 2px 0; padding-left: 1.6em; text-indent: -1.6em; }
.opts b { color: var(--pink-d); }
.none { color: var(--mute); }
table { border-collapse: collapse; width: 100%%; }
th, td { border: 1px solid var(--line); padding: 5px 8px; vertical-align: top; text-align: left; }
th { background: var(--soft); white-space: nowrap; }
td.n { text-align: right; color: var(--mute); width: 2.2em; }
td.why { color: #2a2a2e; }
tr { break-inside: avoid; }
.tag { display: inline-block; border: 1.5px solid var(--warn-line); color: var(--warn); background: var(--warn-bg); border-radius: 4px; padding: 0 5px; font-size: 10pt; }
.cond { margin: 0; padding-left: 1.3em; }
.ask { border: 3px solid var(--pink); border-radius: 12px; padding: 10px 16px; background: #fff; break-inside: avoid; }
.ask ol { list-style: none; margin: 4px 0 0; padding: 0; }
.ask li { font-size: 13pt; font-weight: 700; margin: 3px 0; }
.ask li span { color: var(--pink-d); margin-right: 6px; }
.small { font-size: 10.5pt; color: var(--mute); }
.details li { font-size: 11pt; }
footer { margin-top: 18px; border-top: 1px solid var(--line); padding-top: 8px; color: var(--mute); font-size: 10.5pt; text-align: center; }
/* 時間割 */
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11pt; margin: 4px 0 8px; }
.sw { display: inline-block; width: 22px; height: 12px; vertical-align: middle; margin-right: 4px; border-radius: 3px; }
.sw.c { background: var(--chg-bg); border: 2.5px solid var(--chg); }
.sw.u { background: #f1f1f2; border: 1px solid #bdbdc2; }
.tl { overflow-x: auto; padding-bottom: 6mm; }
.tl-in { min-width: 760px; }
.tl-axis, .tl-row { display: flex; }
.tl-name { width: 30mm; flex: none; font-weight: 700; font-size: 11pt; padding: 3px 6px; border-bottom: 1px solid var(--line); line-height: 1.3; }
.tl-name small { display: block; font-weight: 400; color: var(--mute); font-size: 9pt; }
.tl-track { position: relative; flex: 1; border-bottom: 1px solid var(--line); }
.tl-axis .tl-track { height: 7mm; border-bottom: 2px solid var(--pink); }
.tl-hour { position: absolute; top: 0; bottom: 0; border-left: 1px dashed #bbb; font-size: 10pt; color: var(--mute); padding-left: 3px; }
.tl-row .tl-hour { font-size: 0; }
.blk { position: absolute; border-radius: 4px; font-size: 10pt; line-height: 1.2; padding: 1px 3px; overflow: hidden; background: #f1f1f2; border: 1px solid #bdbdc2; color: #55555a; opacity: .7; }
.blk.chg { opacity: 1; background: var(--chg-bg); border: 2.5px solid var(--chg); color: #08294d; font-weight: 700; }
.blk.chg::before { content: "◆"; color: var(--chg); margin-right: 2px; }
.blk.chg { overflow: visible; z-index: 2; }
.blk small { display: block; white-space: nowrap; font-weight: 700; font-size: 9.5pt; color: var(--chg); background: #fff; border: 1px solid var(--chg); border-radius: 3px; padding: 0 3px; margin-top: 2px; width: max-content; }
.blk.ev { background: #fff; border-style: dashed; }
.pair-h { font-size: 11pt; font-weight: 700; margin: 8px 0 2px; color: var(--mute); }
.tl-day { margin-bottom: 8mm; }
@media print { .tl { overflow: visible; } .page { max-width: none; padding: 0; } .tl-day { break-after: page; page-break-after: always; } .tl-day:last-of-type { break-after: auto; page-break-after: auto; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
"""


def _lanes(blocks: list[dict]) -> list[int]:
    """重なる枠を段に分ける（同じ時間に 2 人分など）。"""
    ends: list[int] = []
    out = []
    for b in sorted(range(len(blocks)), key=lambda i: _minutes(blocks[i]["start"])):
        s, e = _minutes(blocks[b]["start"]), _minutes(blocks[b]["end"])
        for lane, le in enumerate(ends):
            if s >= le:
                ends[lane] = e
                break
        else:
            lane = len(ends)
            ends.append(e)
        out.append((b, lane))
    res = [0] * len(blocks)
    for b, lane in out:
        res[b] = lane
    return res


def _track(blocks: list[dict], pct, faded_all: bool = False) -> tuple[str, int]:
    lanes = _lanes(blocks) if blocks else []
    n = (max(lanes) + 1) if lanes else 1
    h = 17.0  # mm / 段（「←元の担当」の行ぶんも入れる）
    out = []
    for b, lane in zip(blocks, lanes):
        s, e = _minutes(b["start"]), _minutes(b["end"])
        chg = bool(b.get("changed")) and not faded_all
        cls = "blk" + (" chg" if chg else "") + (" ev" if b.get("kind") == "event" else "")
        frm = f"<small>←元の担当 {_e(b['from'])}</small>" if chg and b.get("from") else ""
        out.append(f"<div class='{cls}' style='left:{pct(s):.3f}%;width:{max(pct(e) - pct(s), 0.5):.3f}%;"
                   f"top:{lane * h + 1:.1f}mm;height:{h - 2:.1f}mm' title='{_e(b['start'])}-{_e(b['end'])} {_e(b.get('label', ''))}'>"
                   f"{_e(b['start'])} {_e(b.get('label', ''))}{frm}</div>")
    return "".join(out), n * int(h)


def _timeline_pair(tl: dict) -> str:
    rows = tl.get("rows") or []
    allb = [b for r in rows for k in ("blocks", "before_blocks") for b in r.get(k) or []]
    spans = [(_minutes(b["start"]), _minutes(b["end"])) for b in allb]
    lo = min([s for s, _ in spans] + [9 * 60]) // 60 * 60
    hi = -(-max([e for _, e in spans] + [18 * 60]) // 60) * 60
    width = max(hi - lo, 60)

    def pct(m: int) -> float:
        return (m - lo) * 100.0 / width

    hours = "".join(f"<div class='tl-hour' style='left:{pct(h * 60):.3f}%'>{h}時</div>"
                    for h in range(lo // 60, hi // 60) if h * 60 < hi)
    axis = f"<div class='tl-axis'><div class='tl-name'></div><div class='tl-track'>{hours}</div></div>"
    grid = "".join(f"<div class='tl-hour' style='left:{pct(h * 60):.3f}%'>{h}</div>" for h in range(lo // 60, hi // 60))

    def block(key: str, faded_all: bool) -> str:
        out = [axis]
        for r in rows:
            html_blocks, hmm = _track(r.get(key) or [], pct, faded_all)
            sub = r.get("course") or ""
            out.append(f"<div class='tl-row'><div class='tl-name'>{_e(r.get('name', ''))}"
                       f"{f'<small>{_e(sub)}</small>' if sub else ''}</div>"
                       f"<div class='tl-track' style='height:{hmm}mm'>{grid}{html_blocks}</div></div>")
        return "".join(out)

    date_s = _e(tl.get("date", ""))
    out = [f"<section class='tl-day'><h2>時間割 {date_s}</h2>",
           "<div class='legend'><span><i class='sw c'></i>◆ 変わるところ（青い太枠）</span>"
           "<span><i class='sw u'></i>変わらない（薄い灰色）</span><span>「←元の担当」= 変わる前に入っていた人</span></div>"]
    if any(r.get("before_blocks") for r in rows):
        out.append(f"<div class='pair-h'>変更前</div><div class='tl'><div class='tl-in'>{block('before_blocks', True)}</div></div>")
        out.append("<div class='pair-h'>変更後（この案）</div>")
    out.append(f"<div class='tl'><div class='tl-in'>{block('blocks', False)}</div></div></section>")
    return "".join(out)


# ---- 本体 ---------------------------------------------------------------------------------

def _opts_html(options: list[str]) -> str:
    if not options:
        return ""
    return "<ul class='opts'>" + "".join(f"<li><b>{_c(i)}</b> {_e(o)}</li>" for i, o in enumerate(options)) + "</ul>"


def _prev(plan: dict) -> str:
    c = str(plan.get("compare_to") or "前の案")
    return c if c.startswith("案") or c == "前の案" else f"案 {c}"


def render_html(plan: dict, a3: bool = False, registry: Registry | None = None, icon_path: Path = ICON_PATH) -> str:
    reg = registry or load_all()
    days = _timelines(plan) if a3 else []
    size = "A3 landscape" if a3 else "A4 portrait"
    css = CSS % {"pink": PINK, "font": FONT} + f"\n@page {{ size: {size}; margin: 10mm; }}\n"
    if a3:
        css += ".page { max-width: none; }\n.blk { font-size: 10.5pt; } .tl-name { font-size: 12pt; }\n"
    icon = _icon_data_uri(icon_path)
    title = plan.get("title", "")
    P = ["<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width, initial-scale=1'>",
         f"<title>{_e(title)}</title><style>{css}</style></head><body><div class='page'>",
         "<header>", f"<img src='{icon}' alt='らく助'>" if icon else "", f"<h1>{_e(title)}</h1>",
         f"<div class='meta'>{_e(_target_text(plan.get('target') or {}))}"
         + (f"<br>案 {_e(plan.get('version') or plan.get('id') or '')}" if plan.get("version") or plan.get("id") else "")
         + "</div></header>"]

    hl = headline_of(plan)
    P.append("<h2><span class='n'>1</span>結論</h2><div class='headline'><ul>"
             + ("".join(f"<li>{_e(x)}</li>" for x in hl) or "<li class='none'>（結論が書かれていません）</li>") + "</ul></div>")

    if a3:
        if not days:
            P.append("<p class='none'>この案には日ごとの時間割がありません（A4 の一覧をご覧ください）</p>")
        P += [_timeline_pair(tl) for tl in days]
        P.append(f"<footer>{_e(FOOTER)}</footer></div></body></html>")
        return "\n".join(p for p in P if p)

    iss = issues_of(plan)
    P.append("<h2><span class='n'>2</span>気になる点・人が足りない所</h2>")
    if iss:
        for it in iss:
            P.append(f"<div class='issue {it['severity']}'><div class='t'>{_e(it['text'])}</div>{_opts_html(it['options'])}</div>")
    else:
        P.append("<p class='none'>気になる点はありません</p>")

    ops = plan.get("ops") or []
    P.append(f"<h2><span class='n'>3</span>変わるところと理由（{len(ops)} 件）</h2>"
             "<table><tr><th>#</th><th>変えること</th><th>理由</th><th>元に戻す</th></tr>")
    for i, op in enumerate(ops, 1):
        impl = reg.get(op.get("op", ""))
        note = impl.undo_note(op) if impl else None
        undo = "戻せます" if (impl and note is None) else (f"<span class='tag'>{NOT_UNDOABLE}</span>" if impl else "—")
        why = str(op.get("reason", "")).strip()
        P.append(f"<tr><td class='n'>{i}</td><td>{_e(op.get('label', ''))}</td>"
                 f"<td class='why'>{_e(why) if why else '<span class=none>（理由は書かれていません）</span>'}</td><td>{undo}</td></tr>")
    P.append("</table>")
    P.append("<p class='small'>日ごとの時間割は A3 の帳票（変わるところに青い印）でご覧いただけます。</p>")

    conds = conditions_of(plan)
    P.append("<h2><span class='n'>4</span>今回だけの条件</h2>")
    if conds:
        P.append("<ul class='cond'>" + "".join(
            f"<li>{_e(t)}" + (f"<span class='small'>（{_e(a)}）</span>" if a else "") + "</li>" for t, a in conds) + "</ul>")
    else:
        P.append("<p class='none'>いつもの決まりのままです（今回だけ緩めたことはありません）</p>")

    n = 5
    d = diff_of(plan)
    if plan.get("compare_to") or d:
        P.append(f"<h2><span class='n'>{n}</span>{_e(_prev(plan))} との違い</h2>")
        P.append("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in d) + "</ul>" if d else "<p class='none'>違いは書かれていません</p>")
        n += 1

    P.append(f"<h2><span class='n'>{n}</span>確かめの問い</h2><div class='ask'><div>この案をどうしましょうか。</div><ol>"
             + "".join(f"<li><span>{_c(i)}</span>{_e(a)}</li>" for i, a in enumerate(ASK)) + "</ol></div>")

    if plan.get("checks") or plan.get("warnings"):
        P.append("<h2>くわしい確かめ</h2><ul class='details'>"
                 + "".join(f"<li>{_e(c)}</li>" for c in (plan.get("checks") or []) + (plan.get("warnings") or [])) + "</ul>")
    P.append(f"<footer>{_e(FOOTER)}</footer></div></body></html>")
    return "\n".join(p for p in P if p)


def render_applied(plan: dict, result: dict, icon_path: Path = ICON_PATH) -> str:
    """反映後の短い報告。result: {applied, failed?, run_id?, verified?, undo?, kaipoke?}。"""
    css = CSS % {"pink": PINK, "font": FONT} + "\n@page { size: A4 portrait; margin: 10mm; }\n"
    icon = _icon_data_uri(icon_path)
    ok = int(result.get("failed", 0) or 0) == 0
    lines = [f"{result.get('applied', 0)} 件を反映しました" + ("" if ok else f"（{result.get('failed')} 件は反映できていません）"),
             "読み直して、案のとおりになっていることを確かめました" if result.get("verified") else "読み直しの確かめは、これからです",
             result.get("kaipoke") or "カイポケへはまだ送っていません"]
    undo = result.get("undo") or "元に戻したいときはお知らせください。確かめてから戻します"
    return ("<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            f"<title>反映しました</title><style>{css}</style></head><body><div class='page'><header>"
            + (f"<img src='{icon}' alt='らく助'>" if icon else "") + f"<h1>反映しました：{_e(plan.get('title', ''))}</h1></header>"
            "<div class='headline'><ul>" + "".join(f"<li>{_e(x)}</li>" for x in lines if x) + "</ul></div>"
            + (f"<p>{_e(undo)}</p>" if undo else "") + f"<footer>{_e(FOOTER)}</footer></div></body></html>")


def write_report(cfg: Config, plan: dict, a3: bool = False, out: Path | None = None,
                 registry: Registry | None = None, today: date | None = None) -> Path:
    path = out or report_path(cfg, plan, a3, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(plan, a3, registry), encoding="utf-8")
    return path
