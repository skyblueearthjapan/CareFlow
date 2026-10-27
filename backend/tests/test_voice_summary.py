"""③ まとめの読み取り (``app.services.voice.summary``) のテスト.

設計: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P3・§6。
本文と事実 JSON の読み分け (コードフェンスの有無・閉じ忘れ・壊れた JSON・JSON なし)、本文が空なら失敗。
"""

from __future__ import annotations

import pytest

from app.services.voice import summary
from app.services.voice.vertex_client import VoiceAiError

FACTS_JSON = '{"vitals": {"血圧": "132/78"}, "handover": ["主治医へ"], "next": "火曜"}'
EMPTY_FACTS = {
    "vitals": {"血圧": "", "体温": "", "脈拍": "", "SpO2": "", "血糖": ""},
    "handover": [],
    "next": "",
    "needs_check": [],
}


@pytest.mark.parametrize(
    ("text", "body", "has_facts"),
    [
        ("本文 [0:01]\n\n```json\n" + FACTS_JSON + "\n```", "本文 [0:01]", True),
        ("本文 [0:01]\n```\n" + FACTS_JSON + "\n```\n", "本文 [0:01]", True),  # 言語名なし
        ("本文 [0:01]\n" + FACTS_JSON, "本文 [0:01]", True),  # フェンスなし
        ("本文 [0:01]\n```json\n" + FACTS_JSON, "本文 [0:01]", True),  # 閉じ忘れ
        ('本文 [0:01]\n```json\n{"vitals": {"血圧": 1,,}\n```', "本文 [0:01]", False),  # 壊れた
        ("本文だけ [0:01]", "本文だけ [0:01]", False),  # JSON なし
        ("```markdown\n# 記録\n本文 [0:01]\n```", "# 記録\n本文 [0:01]", False),
    ],
)
def test_split_reply_variants(text: str, body: str, has_facts: bool) -> None:
    got_body, facts = summary.split_reply(text)
    assert got_body == body
    assert (facts is not None) == has_facts


def test_code_block_in_the_middle_stays_in_the_body() -> None:
    text = "前\n```\n" + FACTS_JSON + "\n```\n後ろの文"
    body, facts = summary.split_reply(text)
    assert facts is None and "後ろの文" in body and FACTS_JSON in body


def test_read_reply_builds_v2_summary() -> None:
    got = summary.read_reply(
        "褥瘡の痛み [0:03]【要確認】\n```json\n"
        '{"vitals": {"体温": "36.5", "x": "1"}, "handover": ["…", "主治医へ"],'
        ' "next": {"text": "火曜"}, "needs_check": [{"text": "薬の量", "time": "0:03"}, "残数"]}'
        "\n```"
    )
    assert got == {
        "format": "v2",
        "body": "褥瘡の痛み [0:03]【要確認】",
        "facts": {
            "vitals": {"血圧": "", "体温": "36.5", "脈拍": "", "SpO2": "", "血糖": ""},
            "handover": ["主治医へ"],
            "next": "火曜",
            "needs_check": [{"text": "薬の量", "time": "0:03"}, {"text": "残数", "time": ""}],
        },
    }


def test_read_reply_with_broken_facts_keeps_body() -> None:
    got = summary.read_reply('本文 [0:01]\n```json\n{"vitals": oops\n```')
    assert got["body"] == "本文 [0:01]" and got["facts"] == EMPTY_FACTS


@pytest.mark.parametrize("text", ["", "   ", "```json\n" + FACTS_JSON + "\n```", FACTS_JSON])
def test_read_reply_without_body_fails(text: str) -> None:
    with pytest.raises(VoiceAiError) as exc:
        summary.read_reply(text)
    assert exc.value.kind == "parse"
