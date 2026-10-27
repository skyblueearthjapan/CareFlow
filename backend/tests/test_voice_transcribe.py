"""① 文字起こし (``app.services.voice.transcribe``) のテスト — AI は呼ばない (模擬の ask).

設計: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P1・§6。
切れた配列の救い出し・ループと重複行の除去・時刻の逆行・続きの取得と上限。
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.services.voice import transcribe
from app.services.voice.vertex_client import ModelReply, VoiceAiError


def _line(start, text, speaker="看護師", end=None):
    return {
        "start": start,
        "end": start + 1 if end is None else end,
        "speaker": speaker,
        "text": text,
    }


def _reply(payload, finish="STOP") -> ModelReply:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return ModelReply(text, finish, 1, 1, Decimal("0"), "gemini-2.5-flash")


# --- parse_items -----------------------------------------------------------


def test_parse_items_reads_array_and_normalizes() -> None:
    items, ok = transcribe.parse_items(
        json.dumps(
            [
                {"start": 0, "end": 2, "speaker": "看護師", "text": " こんにちは。 "},
                {"start": "0:05", "end": "0:04", "speaker": "医師", "text": "どうも。"},
                {"start": 7, "speaker": "患者", "text": ""},  # 本文なし → 捨てる
                {"end": 9, "speaker": "患者", "text": "時刻なし"},  # 時刻なし → 捨てる
                "ゴミ",
            ],
            ensure_ascii=False,
        )
    )
    assert ok is True
    assert items == [
        {"start": 0.0, "end": 2.0, "speaker": "看護師", "text": "こんにちは。"},
        # 知らない話者は 不明、終わりが始まりより前なら始まりにそろえる。
        {"start": 5.0, "end": 5.0, "speaker": "不明", "text": "どうも。"},
    ]


def test_parse_items_salvages_complete_objects_of_a_cut_array() -> None:
    cut = (
        '[{"start": 0, "end": 1, "speaker": "看護師", "text": "一"},'
        ' {"start": 2, "end": 3, "speaker": "患者", "text": "二"},'
        ' {"start": 4, "end": 5, "speaker": "看護'
    )
    items, ok = transcribe.parse_items(cut)
    assert ok is True
    assert [i["text"] for i in items] == ["一", "二"]


def test_parse_items_unreadable() -> None:
    assert transcribe.parse_items("これは JSON ではありません") == ([], False)
    assert transcribe.parse_items("[]") == ([], True)


# --- accept ----------------------------------------------------------------


def test_accept_drops_backward_lines() -> None:
    before = [_line(30, "前の行")]
    got = [_line(10, "逆行した行"), _line(29, "少しだけ前 (許す)"), _line(31, "次")]
    kept = transcribe.accept(got, before, from_sec=0, duration_sec=None)
    assert [k["text"] for k in kept] == ["少しだけ前 (許す)", "次"]


def test_accept_drops_repeated_line_near_its_time() -> None:
    before = [_line(10, "同じ文"), _line(12, "最後")]
    got = [_line(12.5, "最後"), _line(14, "新しい")]
    kept = transcribe.accept(got, before, from_sec=12, duration_sec=None)
    assert [k["text"] for k in kept] == ["新しい"]


def test_accept_drops_third_repeat_of_the_same_line() -> None:
    got = [_line(i * 10, "はい。") for i in range(5)] + [_line(60, "はい。", speaker="患者")]
    kept = transcribe.accept(got, [], from_sec=0, duration_sec=None)
    # 同じ話し手の同じ文は 2 回まで。話し手が違えば別の行。
    assert [(k["speaker"], k["start"]) for k in kept] == [
        ("看護師", 0),
        ("看護師", 10),
        ("患者", 60),
    ]


def test_accept_drops_lines_before_range_and_after_audio() -> None:
    got = [_line(50, "範囲より前"), _line(99, "範囲"), _line(130, "録音より後", end=140)]
    kept = transcribe.accept(got, [], from_sec=100, duration_sec=120)
    assert [k["text"] for k in kept] == ["範囲"]
    kept = transcribe.accept([_line(110, "終わりを丸める", end=200)], [], 0, 120)
    assert kept[0]["end"] == 120


# --- transcribe (続きの取得) ----------------------------------------------


def _asker(*replies):
    queue = list(replies)
    prompts: list[str] = []

    async def ask(prompt: str) -> ModelReply:
        prompts.append(prompt)
        return queue.pop(0)

    return ask, prompts


def _build(from_sec, before) -> str:
    return f"from={from_sec} before={len(before)}"


@pytest.mark.asyncio
async def test_transcribe_continues_from_last_complete_line() -> None:
    first = json.dumps([_line(0, "一"), _line(40, "二")], ensure_ascii=False)[:-1] + ', {"start'
    ask, prompts = _asker(
        _reply(first, "MAX_TOKENS"),
        _reply([_line(40, "二"), _line(80, "三")]),
    )
    lines = await transcribe.transcribe(ask, _build, duration_sec=300)
    assert [line["text"] for line in lines] == ["一", "二", "三"]
    assert prompts == ["from=None before=0", "from=40.0 before=2"]


@pytest.mark.asyncio
async def test_transcribe_stops_when_cut_near_the_end() -> None:
    ask, prompts = _asker(_reply([_line(0, "一"), _line(100, "二", end=118)], "MAX_TOKENS"))
    lines = await transcribe.transcribe(ask, _build, duration_sec=120)
    assert len(lines) == 2 and len(prompts) == 1


@pytest.mark.asyncio
async def test_transcribe_gives_up_after_four_continuations() -> None:
    replies = [_reply([_line(i * 10, f"話{i}")], "MAX_TOKENS") for i in range(6)]
    ask, prompts = _asker(*replies)
    with pytest.raises(VoiceAiError) as exc:
        await transcribe.transcribe(ask, _build, duration_sec=None)
    assert exc.value.kind == "incomplete"
    assert len(prompts) == transcribe.MAX_CONTINUATIONS + 1


@pytest.mark.asyncio
async def test_transcribe_cut_without_progress_fails() -> None:
    ask, _ = _asker(_reply([_line(0, "一")], "MAX_TOKENS"), _reply([_line(0, "一")], "MAX_TOKENS"))
    with pytest.raises(VoiceAiError) as exc:
        await transcribe.transcribe(ask, _build, duration_sec=300)
    assert exc.value.kind == "incomplete"


@pytest.mark.asyncio
async def test_transcribe_unreadable_answer_is_parse() -> None:
    ask, _ = _asker(_reply("すみません"))
    with pytest.raises(VoiceAiError) as exc:
        await transcribe.transcribe(ask, _build, duration_sec=None)
    assert exc.value.kind == "parse"


def test_transcript_text_matches_v1_shape() -> None:
    assert transcribe.transcript_text([_line(0, "一"), _line(1, "二", speaker="患者")]) == (
        "看護師: 一\n患者: 二"
    )
