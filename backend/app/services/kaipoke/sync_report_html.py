"""連携結果レポートの HTML 化 (純関数・A4 縦印刷前提)。

正典 = ``docs/plans/sync-result-report-design.md`` §1 (章構成) / §4 (印刷規則)。
見た目の手本 = ``docs/reports/2026-09-03-w37-kaipoke-sync-report.html``。

``render_sync_report_html(report)`` は DB にも時刻にも触らない純関数
(``report.generated_at`` だけを使う)。テストはこの関数を直接叩く。
"""

from __future__ import annotations

import html

from app.services.kaipoke.report_css import REPORT_CSS
from app.services.kaipoke.sync_report import (
    ATTENTION_COVER_LIMIT,
    SyncReport,
    day_label,
)


def _e(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _tag(label: str, kind: str) -> str:
    return f'<span class="tag {_e(kind)}">{_e(label)}</span>'


def _fmt_dt(dt) -> str:
    if dt is None:
        return "—"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_time(dt) -> str:
    if dt is None:
        return "—"
    return dt.astimezone().strftime("%H:%M")


def _fmt_duration(sec: int | None) -> str:
    if sec is None:
        return "—"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _month_label(month: str | None) -> str | None:
    """'2026-09' → '2026年9月'。解釈できない値は月なし扱い (None)。"""
    if not month:
        return None
    try:
        return f"{int(month[:4])}年{int(month[5:7])}月"
    except (ValueError, IndexError):
        return None


def _period_text(report: SyncReport) -> str:
    job = report.job
    lb_month = _month_label(job.month)
    if lb_month:
        return f"対象月: {lb_month}"
    if job.week_start and job.week_end:
        _wd1, lb1 = day_label(job.week_start)
        _wd2, lb2 = day_label(job.week_end)
        return f"対象週: {job.week_start[:4]}年 {lb1} 〜 {lb2}"
    return "対象期間: —"


def _period_short(report: SyncReport) -> str:
    job = report.job
    lb_month = _month_label(job.month)
    if lb_month:
        return lb_month
    if job.week_start and job.week_end:
        _w1, lb1 = day_label(job.week_start)
        _w2, lb2 = day_label(job.week_end)
        return f"{lb1}〜{lb2}"
    return "—"


# ---------------------------------------------------------------------------
# 表紙
# ---------------------------------------------------------------------------


def _cover(report: SyncReport) -> str:
    job = report.job
    outbound = job.direction == "outbound"
    title = "らく助 → カイポケ 送信結果報告" if outbound else "カイポケ → らく助 取込結果報告"
    s = report.summary

    meta = [
        _period_text(report),
        f"実行: {_fmt_dt(job.started_at)} 〜 {_fmt_time(job.completed_at)}"
        f"（所要 {_fmt_duration(job.duration_sec)}）",
        f"実行者: {job.executor_name or '—'}",
        f"操作: {job.op_label}",
        f"ジョブ ID: …{job.id[-8:]}",
    ]
    meta_html = "".join(f"<span>{_e(x)}</span>" for x in meta)

    tiles = [
        f"<span>対象 <b>{s.total}</b></span>",
        f'<span>成功 <b class="ok">{s.success}</b></span>',
        f'<span>失敗 <b class="ng">{s.failed}</b></span>',
        f"<span>除外 <b>{s.excluded}</b></span>",
    ]
    if s.unresolved > 0:
        # 結果が返らなかった行。ここが 0 でない限り「全件成功」とは言わせない。
        tiles.append(f'<span>結果不明 <b class="warn">{s.unresolved}</b></span>')
    tiles.append(f'<span>要確認 <b class="warn">{s.attention}</b></span>')
    kpi = f'<div class="kpi">{"".join(tiles)}</div>'

    parts = [
        '<section class="cover">',
        f"<h1>{_e(title)}</h1>",
        f'<div class="meta">{meta_html}</div>',
        f'<div class="lead {_e(report.conclusion_tone)}">{_e(report.conclusion_text)}</div>',
        kpi,
    ]
    if job.result_unknown:
        parts.append(
            '<div class="box warn"><b>注意:</b> このジョブは連携ロボットの結果を'
            "取得できないまま完了扱いになりました（result_unknown）。"
            "件数は参考値です。末尾の突合で実態をご確認ください。</div>"
        )

    # 除外の内訳
    parts.append("<h2>除外の内訳（送らなかった / 取り込まなかった行）</h2>")
    if report.exclusions:
        body = "".join(
            f'<tr><td class="k">{_e(g.label)}</td><td class="n">{g.count}</td>'
            f'<td class="small">{_e(g.reason)}</td></tr>'
            for g in report.exclusions
        )
        parts.append(
            '<table><thead><tr><th>理由</th><th style="width:16mm">件数</th>'
            '<th style="width:46mm">理由コード</th></tr></thead>'
            f"<tbody>{body}</tbody></table>"
        )
    else:
        parts.append("<p>除外された行はありません。</p>")

    # 要対応一覧
    parts.append("<h2>要対応一覧</h2>")
    if report.attention:
        shown = report.attention[:ATTENTION_COVER_LIMIT]
        rest = len(report.attention) - len(shown)
        body = "".join(
            f'<tr><td class="t">{_e(day_label(a.date)[1] if a.date else "—")}</td>'
            f'<td class="t">{_e(a.time or "—")}</td>'
            f"<td>{_e(a.subject)}</td><td>{_e(a.what)}</td>"
            f"<td>{_tag(a.outcome_label, a.outcome_tag)}</td>"
            f"<td>{_e(a.reason_label)}</td></tr>"
            for a in shown
        )
        parts.append(
            '<table><thead><tr><th style="width:20mm">日付</th>'
            '<th style="width:14mm">時刻</th><th>対象</th>'
            '<th style="width:18mm">操作</th><th style="width:18mm">結果</th>'
            "<th>理由</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
        if rest > 0:
            parts.append(f'<p class="small">他 {rest} 件は明細参照。</p>')
    else:
        parts.append("<p>要対応の行はありません。</p>")

    # 送信後 / 取込後の確認
    parts.append(f"<h2>{'送信後の確認' if outbound else '取込後の確認'}</h2>")
    v = report.verification
    if v.available:
        c = v.counts or {}
        cells = "".join(
            f"<span>{_e(k)} <b>{int(n)}</b></span>"
            for k, n in (
                ("一致", c.get("一致", 0)),
                ("相違", c.get("相違", 0)),
                ("らく助のみ", c.get("らく助のみ", 0)),
                ("カイポケのみ", c.get("カイポケのみ", 0)),
            )
        )
        parts.append(f'<div class="kpi">{cells}</div>')
        parts.append(
            f'<p class="small">カイポケ控えの取得時刻: {_e(_fmt_dt(v.fetched_at))}。'
            "連携ロボットの成功報告ではなく、カイポケから取り直したデータで確定しています。</p>"
        )
    else:
        parts.append(f'<div class="box warn">{_e(v.note or "突合できませんでした。")}</div>')

    parts.append(_footer(report))
    parts.append("</section>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 明細
# ---------------------------------------------------------------------------

# 列幅は印刷実測 (A4 縦・8.4pt) で決めた。変更内容だけ可変にして、他は nowrap に
# 収まる固定幅を与える — 変更内容が狭いと全行が 2 行になり明細が倍のページになる。
_ROW_HEAD = (
    '<thead><tr><th style="width:22mm">時刻</th><th style="width:30mm">利用者</th>'
    '<th style="width:14mm">操作</th><th style="min-width:52mm">変更内容</th>'
    '<th style="width:16mm">結果</th><th style="width:36mm">理由</th></tr></thead>'
)


def _change_html(text: str) -> str:
    """「A → B」を矢印の前後でだけ折り返せる形にする (各辺は nowrap)。"""
    if not text:
        return "—"
    sides = text.split(" → ")
    return " → ".join(f'<span class="nw">{_e(s)}</span>' for s in sides)


def _row_tr(r) -> str:
    time_text = f"{r.start}–{r.end}" if r.start and r.end else (r.start or "—")
    return (
        f'<tr><td class="t">{_e(time_text)}</td><td>{_e(r.user_name)}</td>'
        f"<td>{_e(r.action_label)}</td>"
        f'<td class="chg">{_change_html(r.change_text)}</td>'
        f'<td class="res">{_tag(r.outcome_label, r.outcome_tag)}</td>'
        f"<td>{_e(r.reason_label or '')}</td></tr>"
    )


def _day_sections(report: SyncReport) -> str:
    out: list[str] = []
    for d in report.days:
        cls = "day compact" if d.compact else "day"
        ng = sum(1 for r in d.rows if r.outcome in ("failed", "skipped"))
        body = "".join(_row_tr(r) for r in d.rows)
        out.append(
            f'<section class="{cls}">'
            f'<h2>{_e(d.label)}<span class="cnt">{len(d.rows)}件'
            f"{f'・要確認 {ng}' if ng else ''}</span></h2>"
            f"<table>{_ROW_HEAD}<tbody>{body}</tbody></table>"
            "</section>"
        )
    return "".join(out)


def _excluded_section(report: SyncReport) -> str:
    if not report.excluded_rows:
        return ""
    body = "".join(
        f'<tr><td class="t">{_e(day_label(r.date)[1] if r.date else "—")}</td>'
        f'<td class="t">{_e(r.start or "—")}</td><td>{_e(r.user_name)}</td>'
        f"<td>{_e(r.action_label)}</td>"
        f"<td>{_e(r.reason_label or '')}</td></tr>"
        for r in sorted(report.excluded_rows, key=lambda x: (x.date or "", x.start))
    )
    return (
        '<section class="day"><h2>除外した行</h2>'
        '<table><thead><tr><th style="width:20mm">日付</th>'
        '<th style="width:14mm">時刻</th><th>利用者</th>'
        '<th style="width:18mm">操作</th><th style="width:60mm">理由</th></tr></thead>'
        f"<tbody>{body}</tbody></table></section>"
    )


def _replace_sections(report: SyncReport) -> str:
    out: list[str] = []
    if report.replace_days:
        body = "".join(
            # date='' = 日別内訳を復元できない改修前ジョブの合計行。
            f'<tr><td class="t">{_e(day_label(d.date)[1] if d.date else "合計")}</td>'
            f'<td class="n">{d.wiped}</td><td class="n">{d.inserted}</td>'
            f"<td>{_e('日曜のため対象外' if d.sunday_skipped else '')}</td></tr>"
            for d in report.replace_days
        )
        out.append(
            '<section class="day"><h2>置換した日</h2>'
            '<table><thead><tr><th style="width:24mm">日付</th>'
            '<th style="width:20mm">消した件数</th><th style="width:20mm">入れた件数</th>'
            "<th>備考</th></tr></thead>"
            f"<tbody>{body}</tbody></table></section>"
        )
    if report.skips:
        body = "".join(
            f'<tr><td class="t">{_e(day_label(s.date)[1] if s.date else "—")}</td>'
            f'<td class="t">{_e(s.start or "—")}</td><td>{_e(s.user_name)}</td>'
            f"<td>{_e(s.staff_name)}</td><td>{_e(s.reason_label or '')}</td></tr>"
            for s in report.skips
        )
        out.append(
            '<section class="day"><h2>取り込めなかった行</h2>'
            '<table><thead><tr><th style="width:20mm">日付</th>'
            '<th style="width:14mm">時刻</th><th>利用者</th><th>担当</th>'
            '<th style="width:56mm">理由</th></tr></thead>'
            f"<tbody>{body}</tbody></table></section>"
        )
    if report.trainee_solo:
        items = "".join(f"<li>{_e(t.staff_name)}: {t.count} 件</li>" for t in report.trainee_solo)
        out.append(
            '<section class="day"><h2>新人単独の警告</h2>'
            '<div class="box warn"><p>新人が単独で登録されています。'
            "同行者の設定をご確認ください。</p>"
            f"<ul>{items}</ul></div></section>"
        )
    return "".join(out)


def _events_section(report: SyncReport) -> str:
    if not report.events:
        return ""
    body = "".join(
        f'<tr><td class="t">{_e(day_label(e.date)[1] if e.date else "—")} '
        f"{_e(e.start or '')}</td><td>{_e(e.staff_name)}</td><td>{_e(e.title)}</td>"
        f"<td>{_e(e.action_label)}</td>"
        f"<td>{_tag(e.outcome_label, e.outcome_tag)}</td>"
        f"<td>{_e(e.reason_label or '')}</td></tr>"
        for e in report.events
    )
    return (
        '<section class="day"><h2>イベント</h2>'
        '<table><thead><tr><th style="width:30mm">時刻</th><th style="width:26mm">職員</th>'
        '<th>タイトル</th><th style="width:18mm">操作</th>'
        '<th style="width:18mm">結果</th><th style="width:36mm">理由</th></tr></thead>'
        f"<tbody>{body}</tbody></table></section>"
    )


def _footer(report: SyncReport) -> str:
    """静的フッタ (表紙末尾と文書末尾の 2 箇所)。固定フッタにしない理由は report_css。"""
    text = (
        "らく助 × カイポケ 連携結果報告 ｜ "
        f"対象 {_period_short(report)} ｜ 作成 {_fmt_dt(report.generated_at)}"
    )
    return f'<div class="pfoot">{_e(text)}</div>'


def _appendix(report: SyncReport) -> str:
    # 補足に .pb を付けると、数行のために白紙同然のページが 1 枚増える (実測)。
    # 入るなら直前の章に続けて置き、割れそうなら丸ごと次ページへ送る。
    parts = ['<section class="appendix">', "<h2>補足</h2>"]
    if report.detail_level == "summary_only":
        parts.append(
            '<div class="box warn">改修前のジョブのため行単位の明細はありません。'
            "表紙の件数と、連携ロボットが返した概要のみを掲載しています。</div>"
        )
    parts.append("<h3>理由コードの説明</h3>")
    if report.reason_codes:
        body = "".join(
            f'<tr><td class="k">{_e(code)}</td><td>{_e(label)}</td></tr>'
            for code, label in report.reason_codes
        )
        parts.append(
            '<table><thead><tr><th style="width:56mm">コード</th>'
            "<th>意味</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    else:
        parts.append("<p>このレポートには理由コード付きの行はありません。</p>")

    parts.append("<h3>データ根拠</h3>")
    v = report.verification
    snap = _fmt_dt(v.fetched_at) if v.available else (v.note or "突合なし")
    parts.append(
        "<ul>"
        f"<li>ジョブ ID: {_e(report.job.id)}（{_e(report.job.op)} / {_e(report.job.op_label)}）</li>"
        f"<li>明細の粒度: {_e('行単位' if report.detail_level == 'full' else '概要のみ')}</li>"
        f"<li>カイポケ控え: {_e(snap)}</li>"
        f"<li>作成: {_e(_fmt_dt(report.generated_at))}</li>"
        "</ul>"
    )
    parts.append("</section>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def render_sync_report_html(report: SyncReport) -> str:
    """レポート dataclass → 自己完結 HTML (A4 縦印刷対応)。"""
    outbound = report.job.direction == "outbound"
    title = "らく助 → カイポケ 送信結果報告" if outbound else "カイポケ → らく助 取込結果報告"

    detail = "".join(
        [
            _day_sections(report),
            _excluded_section(report),
            _replace_sections(report),
            _events_section(report),
        ]
    )
    detail_html = f'<div class="pb">{detail}</div>' if detail else ""

    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}（{_e(_period_short(report))}）</title>\n"
        "<style>" + REPORT_CSS + "</style>\n"
        "</head>\n<body>\n"
        '<div class="toolbar"><button onclick="window.print()">印刷 / PDF 保存</button></div>\n'
        '<div class="sheet">\n'
        + _cover(report)
        + detail_html
        + _appendix(report)
        + _footer(report)
        + "\n</div>\n</body>\n</html>"
    )
