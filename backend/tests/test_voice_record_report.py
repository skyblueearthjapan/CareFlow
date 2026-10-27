"""訪問記録 A4 レポートの純関数レンダラのテスト.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §11-3。

``render_visit_record_html`` は DB にも時刻にも触らないので、ここでは dict を
直接渡して HTML の中身だけを確かめる (エンドポイント側は
``test_visit_recordings.py`` の ``/report`` のテスト)。

網羅:

* HTML エスケープ (患者名・要約・発話のいずれも素通ししない)。
* 要約の出し分け: 人手修正あり → ``summary_text`` 平文 / 無し → ``summary``
  JSON を見出し + バイタル表 / どちらも無い → 「まだありません」。
* バイタルは測定値がある項目だけ (全部空なら見出しごと出さない)。
* 文字起こしの出し分け: ``transcript_json`` があれば話者 + 時刻の表、
  無ければ ``transcript`` の平文。
* 時刻は JST (UTC のまま出さない)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.voice.record_report_html import render_visit_record_html


def _rec(**overrides) -> dict:
    """``_serialize()`` が返す形の最小セット (+ ``generated_at``)。"""
    rec = {
        "patient_name": "利用者 太郎",
        "staff_name": "録音 花子",
        "office_name": "都賀A",
        "recorded_at": datetime(2026, 9, 17, 15, 30, tzinfo=UTC),
        "duration_sec": 1513,
        "status": "summarized",
        "reviewed_at": None,
        "summary": None,
        "summary_text": None,
        "summary_edited_at": None,
        "transcript": None,
        "transcript_json": None,
        "model": "gemini-2.5-flash",
        "prompt_version": "v1",
        "error_message": None,
        "generated_at": datetime(2026, 9, 18, 1, 0, tzinfo=UTC),
    }
    rec.update(overrides)
    return rec


_SUMMARY = {
    "主訴・様子": ["膝の痛みが続くとの訴え", "食欲は良好"],
    "バイタル": {"血圧": "128/76", "体温": "36.4", "脈拍": "", "SpO2": "98", "血糖": ""},
    "処置・ケア": ["創部の包交"],
    "申し送り": [],
    "次回": "9/24 10:00",
    "free": "",
}


# ---------------------------------------------------------------------------
# 体裁・エスケープ


def test_renders_self_contained_a4_document() -> None:
    html_doc = render_visit_record_html(_rec())
    assert html_doc.startswith("<!doctype html>")
    assert html_doc.rstrip().endswith("</html>")
    # REPORT_CSS を同梱している (外部 CSS に依存しない)。
    assert "@page{size:A4 portrait" in html_doc
    # 1 件 1 枚 = .sheet は 1 つ・強制改ページ (.pb) は入れない。
    assert html_doc.count('<div class="sheet">') == 1
    assert '<div class="pb">' not in html_doc
    assert "利用者 太郎" in html_doc
    assert "録音 花子" in html_doc
    assert "都賀A" in html_doc


def test_escapes_html_everywhere() -> None:
    """外から来る値は 1 つも素通りしない (レポートは人に配る紙)."""
    html_doc = render_visit_record_html(
        _rec(
            patient_name="<script>alert(1)</script>",
            staff_name="<i>看護師</i>",
            office_name="<u>拠点</u>",
            model="<em>gemini</em>",
            prompt_version="<s>v1</s>",
            status="failed",
            error_message="<marquee>失敗</marquee>",
            summary={**_SUMMARY, "主訴・様子": ["<b>痛み</b> & 倦怠感"], "新項目": ["<hr>"]},
            transcript_json=[{"speaker": "<svg>", "start_sec": 3, "text": "<img onerror=x>"}],
        )
    )
    for raw in (
        "<script>alert(1)</script>",
        "<i>看護師</i>",
        "<u>拠点</u>",
        "<em>gemini</em>",
        "<s>v1</s>",
        "<marquee>失敗</marquee>",
        "<b>痛み</b>",
        "<hr>",
        "<img onerror=x>",
        "<svg>",
    ):
        assert raw not in html_doc, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_doc
    assert "&lt;b&gt;痛み&lt;/b&gt; &amp; 倦怠感" in html_doc
    # 未知の見出し自体もエスケープした上で出す (黙って落とさない)。
    assert "<h3>新項目</h3>" in html_doc


def test_unknown_summary_keys_are_kept() -> None:
    """プロンプト改訂で増えた節も紙に残す (入れ子 dict はスカラー扱い)."""
    html_doc = render_visit_record_html(
        _rec(
            summary={
                **_SUMMARY,
                "服薬": ["インスリン 4 単位"],
                "家族の様子": {"同居": "長男"},
                "空の節": [],
                # 内部キーは出さない (再処理で退避した人手修正の置き場)。
                "previous_manual": {"summary_text": "旧・人手修正"},
            }
        )
    )
    assert "<h3>服薬</h3>" in html_doc
    assert "インスリン 4 単位" in html_doc
    assert "<h3>家族の様子</h3>" in html_doc
    assert "長男" in html_doc
    assert "<h3>空の節</h3>" not in html_doc
    assert "旧・人手修正" not in html_doc


def test_times_are_rendered_in_jst() -> None:
    """UTC 15:30 は JST では翌日 00:30 (コンテナの TZ に引きずられない)."""
    html_doc = render_visit_record_html(_rec())
    assert "録音: 2026-09-18 00:30" in html_doc
    assert "2026-09-17 15:30" not in html_doc
    # 長さは 1513 秒 = 25 分 13 秒。
    assert "長さ: 25分13秒" in html_doc
    # フッタ: 出力日時 (JST) + AI の出所 + 注記。
    assert "出力 2026-09-18 10:00" in html_doc
    assert "gemini-2.5-flash / v1" in html_doc
    assert "AI による自動生成です" in html_doc


# ---------------------------------------------------------------------------
# 要約の出し分け


def test_summary_json_renders_headings_and_vitals_table() -> None:
    html_doc = render_visit_record_html(_rec(summary=_SUMMARY, summary_text="（AI の平文）"))
    assert "<h3>主訴・様子</h3>" in html_doc
    assert "<li>膝の痛みが続くとの訴え</li>" in html_doc
    assert "<h3>バイタル</h3>" in html_doc
    # 測定値がある項目だけ表に出す (脈拍・血糖は空なので出さない)。
    assert "128/76" in html_doc
    assert "<td>98</td>" in html_doc
    assert "脈拍" not in html_doc
    # 空配列の節は見出しごと出さない。
    assert "<h3>申し送り</h3>" not in html_doc
    assert "<h3>次回</h3>" in html_doc
    # JSON があるときは AI の平文 (summary_text) は使わない。
    assert "（AI の平文）" not in html_doc


def test_manual_edit_renders_plain_text_not_json() -> None:
    """手修正があればその文が正 (古い JSON の見出しで上書きして見せない)."""
    html_doc = render_visit_record_html(
        _rec(
            summary=_SUMMARY,
            summary_text="看護師が直した要約。\n2 行目。",
            summary_edited_at=datetime(2026, 9, 18, 0, 5, tzinfo=UTC),
        )
    )
    assert "看護師が直した要約。" in html_doc
    assert "<h3>主訴・様子</h3>" not in html_doc
    assert "<h3>バイタル</h3>" not in html_doc
    assert "手修正あり 2026-09-18 09:05" in html_doc


def test_manual_edit_to_empty_does_not_fall_back_to_json() -> None:
    """人が要約を消したら **消えたまま** 出す (旧 JSON を復活させない)."""
    html_doc = render_visit_record_html(
        _rec(
            summary=_SUMMARY,
            summary_text="",
            summary_edited_at=datetime(2026, 9, 18, 0, 5, tzinfo=UTC),
        )
    )
    assert "要約は削除されています" in html_doc
    # 旧 JSON の中身も見出しも出ない。
    assert "膝の痛みが続くとの訴え" not in html_doc
    assert "128/76" not in html_doc
    assert "<h3>主訴・様子</h3>" not in html_doc
    assert "<h3>次回</h3>" not in html_doc


def test_vitals_table_is_omitted_when_nothing_measured() -> None:
    summary = {**_SUMMARY, "バイタル": dict.fromkeys(["血圧", "体温", "脈拍", "SpO2", "血糖"], "")}
    html_doc = render_visit_record_html(_rec(summary=summary))
    assert "<h3>バイタル</h3>" not in html_doc
    assert "<h3>主訴・様子</h3>" in html_doc


def test_empty_summary_shows_placeholder_with_error() -> None:
    html_doc = render_visit_record_html(
        _rec(status="failed", summary=None, summary_text=None, error_message="要約に失敗しました")
    )
    assert "要約はまだありません" in html_doc
    assert "要約に失敗しました" in html_doc
    assert "失敗" in html_doc


def test_summary_text_without_manual_edit_is_used_when_json_missing() -> None:
    html_doc = render_visit_record_html(_rec(summary=None, summary_text="AI が書いた平文"))
    assert "AI が書いた平文" in html_doc
    assert "要約はまだありません" not in html_doc


# ---------------------------------------------------------------------------
# 文字起こしの出し分け


def test_transcript_json_renders_speaker_and_timestamps() -> None:
    html_doc = render_visit_record_html(
        _rec(
            transcript="平文は使われないはず",
            transcript_json=[
                {"speaker": "看護師", "start_sec": 0, "text": "今日の体調はいかがですか。"},
                {"speaker": "患者", "start_sec": 75, "text": "少し膝が痛みます。"},
                # 秒が分からない行と空行 (空は落とす)。
                {"speaker": "家族", "start_sec": None, "text": "昨夜は眠れていました。"},
                {"speaker": "不明", "start_sec": 90, "text": "   "},
            ],
        )
    )
    assert "<td>今日の体調はいかがですか。</td>" in html_doc
    assert "01:15" in html_doc
    assert "少し膝が痛みます。" in html_doc
    assert "昨夜は眠れていました。" in html_doc
    assert "平文は使われないはず" not in html_doc


def test_transcript_plain_text_when_no_segments() -> None:
    html_doc = render_visit_record_html(_rec(transcript="看護師: こんにちは。\n患者: どうも。"))
    assert "看護師: こんにちは。" in html_doc
    assert "文字起こしはありません" not in html_doc


def test_transcript_missing_shows_placeholder() -> None:
    html_doc = render_visit_record_html(_rec())
    assert "文字起こしはありません" in html_doc


def test_accepts_iso_strings_for_datetimes() -> None:
    """JSON を往復した dict (日時が文字列) でも同じ紙になる."""
    html_doc = render_visit_record_html(
        _rec(recorded_at="2026-09-17T15:30:00Z", generated_at="2026-09-18T01:00:00+00:00")
    )
    assert "録音: 2026-09-18 00:30" in html_doc
    assert "出力 2026-09-18 10:00" in html_doc


def test_unlinked_recording_renders_without_patient() -> None:
    html_doc = render_visit_record_html(_rec(patient_name=None, status="unlinked"))
    assert "患者 未紐付け" in html_doc
    assert "紐付け待ち" in html_doc


# ---------------------------------------------------------------------------
# 音声記録 v2 (docs/plans/visit-voice-record-v2-design-2026-10-27.md §3 P3)


def test_v1_record_renders_exactly_as_before() -> None:
    """v1 の記録 (format の無い要約・start_sec の行) の見た目は変わらない。"""
    html_doc = render_visit_record_html(
        _rec(
            summary=_SUMMARY,
            transcript_json=[{"speaker": "看護師", "start_sec": 65, "text": "こんにちは"}],
        )
    )
    assert "<h3>主訴・様子</h3><ul><li>膝の痛みが続くとの訴え</li>" in html_doc
    assert '<td class="k">血圧</td><td>128/76</td>' in html_doc
    assert '<td class="sec">01:05</td><td class="sp">看護師</td><td>こんにちは</td>' in html_doc


def test_v2_summary_renders_body_as_escaped_plain_text() -> None:
    body = "褥瘡の痛み [0:03]\n- 血圧 132/78 [0:00]\n<script>alert(1)</script>"
    html_doc = render_visit_record_html(
        _rec(
            summary={"format": "v2", "body": body, "facts": {"vitals": {"血圧": "132/78"}}},
            summary_text=body,
            prompt_version="v2",
        )
    )
    assert '<p class="pre">褥瘡の痛み [0:03]\n- 血圧 132/78 [0:00]\n&lt;script&gt;' in html_doc
    assert "<script>alert" not in html_doc
    # v2 の内部のキー (format / body / facts) を見出しとして出さない。
    assert "<h3>format</h3>" not in html_doc and "<h3>facts</h3>" not in html_doc


def test_v2_manual_edit_still_wins() -> None:
    html_doc = render_visit_record_html(
        _rec(
            summary={"format": "v2", "body": "AI の本文", "facts": {}},
            summary_text="人が直した本文",
            summary_edited_at=datetime(2026, 9, 17, 16, 0, tzinfo=UTC),
        )
    )
    assert "人が直した本文" in html_doc and "AI の本文" not in html_doc


def test_v2_transcript_prefers_fixed_lines_and_reads_start() -> None:
    html_doc = render_visit_record_html(
        _rec(
            transcript_json=[{"start": 3, "end": 5, "speaker": "患者", "text": "じょくそう"}],
            transcript_fixed_json=[{"start": 3, "end": 5, "speaker": "患者", "text": "褥瘡"}],
        )
    )
    assert '<td class="sec">00:03</td><td class="sp">患者</td><td>褥瘡</td>' in html_doc
    assert "じょくそう" not in html_doc
    # 直した行が無ければ直す前の行。
    html_doc = render_visit_record_html(
        _rec(transcript_json=[{"start": 3, "end": 5, "speaker": "患者", "text": "じょくそう"}])
    )
    assert "<td>じょくそう</td>" in html_doc
