"""訪問記録 (音声記録) の A4 印刷 HTML 化 — 純関数レンダラ.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §11-3。
手本 = ``app/services/kaipoke/sync_report_html.py`` (純関数・JST 変換・静的フッタ)。
CSS は ``app/services/kaipoke/report_css.py`` の ``REPORT_CSS`` をそのまま使う
(A4 縦・改ページ規則・表ヘッダの繰り返しが既に詰めてある。印刷物の見た目を
連携結果レポートと揃える意味もある)。

``render_visit_record_html(rec)`` は **DB にも時刻にも触らない**。入力は
``visit_recordings.py`` の ``_serialize()`` が返す dict そのもの ＋ 出力時刻を
表す ``generated_at`` キー (API 層が足す) だけ。テストはこの関数を直接叩く。

1 件 1 枚が基本 (``.sheet`` は 1 つだけ)。文字起こしが長い録音は表の行単位で
自然に次ページへ流れる (``REPORT_CSS`` の ``thead{display:table-header-group}``
＋ ``tr{break-inside:avoid}``)。**強制改ページ (``.pb``) は入れない** — 数行の
ために白紙同然のページを増やさないため。
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.kaipoke.report_css import REPORT_CSS
from app.services.voice.prompts import VITAL_KEYS

#: 報告書の時刻は常に日本時間 (コンテナは UTC で動いているので、``astimezone()``
#: = プロセスのローカル TZ に任せると 9 時間ずれる)。
_JST = ZoneInfo("Asia/Tokyo")

#: ``status`` 列 → 画面と同じ日本語。未知の値はそのまま出す (隠さない)。
_STATUS_LABELS: dict[str, str] = {
    "uploaded": "受領済み",
    "transcribing": "処理中",
    "summarized": "要約済み",
    "failed": "失敗",
    "unlinked": "紐付け待ち",
}

#: ``.tag`` の色。失敗だけ赤、完了は緑、それ以外は無彩色。
_STATUS_TAG_KIND: dict[str, str] = {
    "summarized": "ok",
    "failed": "ng",
    "transcribing": "warn",
}

#: ``summary`` JSON のうち箇条書きで出す見出し (順番どおりに描く)。
_LIST_SECTIONS: tuple[str, ...] = ("主訴・様子", "処置・ケア", "申し送り")

#: ``summary`` JSON のうち画面に出さない内部キー
#: (``previous_manual`` = 再処理で踏み潰した人手修正の退避先・設計 §11-2)。
_INTERNAL_SUMMARY_KEYS: frozenset[str] = frozenset({"previous_manual"})

#: REPORT_CSS に無い分だけを足す (改行を保つ本文・話者列・注記)。
_EXTRA_CSS = """
.pre{white-space:pre-wrap;word-break:break-word;font-size:8.8pt}
td.sp{white-space:nowrap;font-weight:700;width:18mm}
td.sec{white-space:nowrap;text-align:right;width:14mm;color:var(--soft);
  font-variant-numeric:tabular-nums}
.note{font-size:8pt;color:var(--soft);margin-top:1mm}
"""


def _e(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _tag(label: str, kind: str = "muted") -> str:
    return f'<span class="tag {_e(kind)}">{_e(label)}</span>'


def _to_jst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_JST)


def _parse_dt(value: Any) -> datetime | None:
    """dict 由来なので ``datetime`` でも ISO 文字列でも受ける (JSON 往復対策)。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _fmt_dt(value: Any) -> str:
    dt = _parse_dt(value)
    return _to_jst(dt).strftime("%Y-%m-%d %H:%M") if dt else "—"


def _fmt_date(value: Any) -> str:
    dt = _parse_dt(value)
    return _to_jst(dt).strftime("%Y-%m-%d") if dt else "—"


def _fmt_duration(sec: Any) -> str:
    """秒 → 「25分13秒」。1 時間以上は時間も出す。"""
    try:
        total = int(sec)
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        return "—"
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}時間{minutes}分{seconds}秒"
    return f"{minutes}分{seconds}秒"


def _mmss(sec: Any) -> str:
    """文字起こしの経過秒 → ``mm:ss`` (不明は空欄)。"""
    if sec is None or isinstance(sec, bool):
        return ""
    try:
        total = int(float(sec))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    return f"{total // 60:02d}:{total % 60:02d}"


def _status_label(status: Any) -> str:
    key = str(status or "")
    return _STATUS_LABELS.get(key, key or "—")


def _items(value: Any) -> list[str]:
    """箇条書きの中身を正規化する (文字列 1 本でも配列でも受ける)。

    入れ子の dict や数値など **想定外の形は ``str()`` にしてスカラー 1 件** として
    扱う。プロンプト改訂で節の形が変わったとき、紙から中身が黙って消えるより、
    見た目が不格好でも出ている方が安全 (看護記録なので欠落が最も困る)。
    空 (``None`` / ``{}`` / ``[]`` / ``""``) だけが「該当なし」。
    """
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not value:
        return []
    text = str(value).strip()
    return [text] if text else []


# ---------------------------------------------------------------------------
# ヘッダ
# ---------------------------------------------------------------------------


def _header(rec: dict[str, Any]) -> str:
    patient = str(rec.get("patient_name") or "").strip() or "（患者 未紐付け）"
    status = str(rec.get("status") or "")

    meta = [
        f"録音: {_fmt_dt(rec.get('recorded_at'))}",
        f"長さ: {_fmt_duration(rec.get('duration_sec'))}",
        f"担当: {rec.get('staff_name') or '—'}",
        f"拠点: {rec.get('office_name') or '—'}",
    ]
    meta_html = "".join(f"<span>{_e(x)}</span>" for x in meta)

    tags = [_tag(_status_label(status), _STATUS_TAG_KIND.get(status, "muted"))]
    if rec.get("reviewed_at"):
        tags.append(_tag(f"確認済み {_fmt_dt(rec.get('reviewed_at'))}", "ok"))
    else:
        tags.append(_tag("未確認", "warn"))
    if rec.get("summary_edited_at"):
        tags.append(_tag(f"手修正あり {_fmt_dt(rec.get('summary_edited_at'))}", "warn"))
    else:
        tags.append(_tag("AI のまま", "muted"))

    return (
        f"<h1>訪問記録　{_e(patient)}</h1>\n"
        f'<div class="meta">{meta_html}</div>\n'
        f'<div class="kpi">{"".join(tags)}</div>\n'
    )


# ---------------------------------------------------------------------------
# 要約
# ---------------------------------------------------------------------------


def _vitals_table(vitals: Any) -> str:
    """バイタル表。1 項目も測定値が無ければ **見出しごと出さない** (空表は雑音)。"""
    if not isinstance(vitals, dict):
        return ""
    ordered = [(k, str(vitals.get(k) or "").strip()) for k in VITAL_KEYS]
    # スキーマ外の項目も落とさない (AI が足した項目を握り潰さない)。
    ordered += [(str(k), str(v or "").strip()) for k, v in vitals.items() if k not in VITAL_KEYS]
    measured = [(k, v) for k, v in ordered if v]
    if not measured:
        return ""
    body = "".join(f'<tr><td class="k">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in measured)
    return (
        "<h3>バイタル</h3>"
        '<table><thead><tr><th style="width:28mm">項目</th><th>測定値</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _list_block(title: str, value: Any) -> str:
    items = _items(value)
    if not items:
        return ""
    lis = "".join(f"<li>{_e(x)}</li>" for x in items)
    return f"<h3>{_e(title)}</h3><ul>{lis}</ul>"


def _summary_from_json(summary: dict[str, Any]) -> str:
    # 見出しの順番は ``prompts.SUMMARY_SECTIONS`` と同じ。空の節は丸ごと出さない
    # (「該当なし」の見出しだけが並ぶ紙にしない)。
    parts: list[str] = [
        _list_block("主訴・様子", summary.get("主訴・様子")),
        _vitals_table(summary.get("バイタル")),
        _list_block("処置・ケア", summary.get("処置・ケア")),
        _list_block("申し送り", summary.get("申し送り")),
    ]

    next_visit = str(summary.get("次回") or "").strip()
    if next_visit:
        parts.append(f"<h3>次回</h3><p>{_e(next_visit)}</p>")
    free = str(summary.get("free") or "").strip()
    if free:
        parts.append(f'<h3>補足</h3><p class="pre">{_e(free)}</p>')

    # 未知のキーも黙って捨てない (プロンプト改訂で増えた項目を見落とさない)。
    known = set(_LIST_SECTIONS) | {"バイタル", "次回", "free"} | _INTERNAL_SUMMARY_KEYS
    for key, value in summary.items():
        if key in known:
            continue
        parts.append(_list_block(str(key), value))

    return "".join(p for p in parts if p)


def _summary_section(rec: dict[str, Any]) -> str:
    edited = bool(rec.get("summary_edited_at"))
    summary_text = str(rec.get("summary_text") or "").strip()
    summary = rec.get("summary")

    # 人手修正があるときは **``summary_text`` が正** (Phase 2 の規則)。AI の
    # 構造化 JSON は既に古いので、見出し付きの旧内容で上書きして見せない。
    # 空にしたのも人の判断なので、**空のときも JSON へ落とさない** — 消した本人が
    # 見る紙に、消したはずの旧要約が復活していてはならない。
    if edited:
        body = (
            f'<p class="pre">{_e(summary_text)}</p>'
            if summary_text
            else '<div class="box warn">要約は削除されています。</div>'
        )
    elif isinstance(summary, dict) and (rendered := _summary_from_json(summary)):
        body = rendered
    elif summary_text:
        body = f'<p class="pre">{_e(summary_text)}</p>'
    else:
        error = str(rec.get("error_message") or "").strip()
        detail = f"（{error}）" if error else ""
        body = f'<div class="box warn">要約はまだありません。{_e(detail)}</div>'

    return f'<section class="summary"><h2>要約</h2>{body}</section>'


# ---------------------------------------------------------------------------
# 文字起こし
# ---------------------------------------------------------------------------


def _transcript_section(rec: dict[str, Any]) -> str:
    segments = rec.get("transcript_json")
    rows: list[str] = []
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            rows.append(
                f'<tr><td class="sec">{_e(_mmss(seg.get("start_sec")))}</td>'
                f'<td class="sp">{_e(seg.get("speaker") or "不明")}</td>'
                f"<td>{_e(text)}</td></tr>"
            )
    if rows:
        body = (
            '<table><thead><tr><th style="width:14mm">時刻</th>'
            '<th style="width:18mm">話者</th><th>発話</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        transcript = str(rec.get("transcript") or "").strip()
        body = (
            f'<p class="pre">{_e(transcript)}</p>'
            if transcript
            else '<div class="box">文字起こしはありません。</div>'
        )
    return f'<section class="transcript"><h2>文字起こし（全文）</h2>{body}</section>'


# ---------------------------------------------------------------------------
# フッタ
# ---------------------------------------------------------------------------


def _footer(rec: dict[str, Any]) -> str:
    """静的フッタ (固定フッタにしない理由は ``report_css`` の冒頭コメント)。"""
    model = str(rec.get("model") or "").strip()
    version = str(rec.get("prompt_version") or "").strip()
    if model and version:
        ai = f"{model} / {version}"
    else:
        ai = model or version or "—"

    line1 = f"らく助 訪問記録 ｜ 出力 {_fmt_dt(rec.get('generated_at'))} ｜ AI: {ai}"
    line2 = "この記録は AI による自動生成です。内容は看護師が確認してください。"
    return f'<div class="pfoot">{_e(line1)}<div class="note">{_e(line2)}</div></div>'


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def render_visit_record_html(rec: dict[str, Any]) -> str:
    """音声記録 1 件 → 自己完結 HTML (A4 縦・1 件 1 枚)。

    Args:
        rec: ``visit_recordings._serialize()`` の戻り値 ＋ ``generated_at``
            (出力時刻・API 層が足す)。``datetime`` でも ISO 文字列でも可。

    Returns:
        ``<!doctype html>`` から始まる自己完結の HTML 文字列。
    """
    patient = str(rec.get("patient_name") or "").strip() or "患者未紐付け"
    title = f"訪問記録 {patient}（{_fmt_date(rec.get('recorded_at'))}）"

    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n"
        "<style>" + REPORT_CSS + _EXTRA_CSS + "</style>\n"
        "</head>\n<body>\n"
        # inline onclick は CSP の 'unsafe-inline' 前提 (連携結果レポートと同じ)。
        # 印刷ボタンを外部 JS にすると自己完結 HTML でなくなるため、この 1 箇所だけ許す。
        '<div class="toolbar"><button onclick="window.print()">印刷 / PDF 保存</button></div>\n'
        '<div class="sheet">\n'
        + _header(rec)
        + _summary_section(rec)
        + _transcript_section(rec)
        + _footer(rec)
        + "\n</div>\n</body>\n</html>"
    )
