"""① 文字起こし — 音声 → 発言の行 (上限で切れたら最後の行の時刻から続きを頼む).

正典: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P1。
守りは PC-Assistant の ``GeminiTranscriber`` (``ParseItems``・``Accept``・``MaxContinuations``) を写した:

* 切れた JSON の配列は、完全なオブジェクトだけを使う。
* すでにある時刻より前の行 (時刻の逆行) と、直前と同じ文の行 (続きの読み直し) は捨てる。
* 同じ話し手の同じ文が 3 回続いたら捨てる (ループ)。
* 続きは 1 件 4 回まで。切れたのに新しい行が 1 つも無い / 4 回で読み切れない → ``incomplete``。

行は ``{"start": 秒, "end": 秒, "speaker": 4 種のどれか, "text": 文}`` の dict
(``visit_recordings.transcript_json`` にそのまま入る)。AI の呼び出しと再試行は呼び出し側
(``jobs._call_with_retry``) が持つ。この層は 1 回ずつ ``ask(prompt)`` を呼ぶだけ。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.services.voice.prompts import SPEAKER_LABELS
from app.services.voice.vertex_client import ModelReply, VoiceAiError

#: 1 件で続きを頼む回数の上限 (最初の 1 回 + 4 回)。
MAX_CONTINUATIONS = 4

#: 続きの読み直しで「同じ行」とみなす時刻の差 (秒)。
_SAME_LINE_SEC = 2.0
#: 直前の行の開始より、これ以上前に始まる行は逆行として捨てる (秒)。
_BACKWARD_SLACK_SEC = 1.5
#: 続きを頼んだ時刻より、これ以上前に始まる行は前の答えが持っているので捨てる (秒)。
_BEFORE_RANGE_SLACK_SEC = 2.0
#: 録音の終わりまでこの秒数以内なら、切れていても読み切ったとみなす。
_END_SLACK_SEC = 5.0

_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

Line = dict[str, Any]


def _seconds(value: Any) -> float | None:
    """秒の数値・数字の文字列・``m:ss`` / ``h:mm:ss`` → 秒。時刻でなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    parts = _NUMBER_RE.findall(value)
    if not parts or len(parts) > 3:
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_items(text: str) -> tuple[list[Line], bool]:
    """答えの JSON 配列 → 行。切れた配列は、完全なオブジェクトだけを救う。

    Returns:
        (行, 読めたか)。読めた = 配列として読めた、または 1 つでもオブジェクトを救えた。
    """
    nodes: list[Any] = []
    readable = False
    stripped = (text or "").strip()
    try:
        data = json.loads(stripped)
    except ValueError:
        data = None
    if isinstance(data, list):
        nodes = data
        readable = True
    else:
        for match in _OBJECT_RE.finditer(stripped):
            try:
                nodes.append(json.loads(match.group(0)))
            except ValueError:
                continue
        readable = bool(nodes)

    items: list[Line] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        body = _clean(str(node.get("text") or ""))
        start = _seconds(node.get("start"))
        if start is None or not body:
            continue
        end = _seconds(node.get("end"))
        if end is None or end < start:
            end = start
        speaker = str(node.get("speaker") or "").strip()
        if speaker not in SPEAKER_LABELS:
            speaker = "不明"
        items.append(
            {"start": round(start, 1), "end": round(end, 1), "speaker": speaker, "text": body}
        )
    return items, readable


def accept(
    got: Sequence[Line],
    before: Sequence[Line],
    from_sec: float,
    duration_sec: float | None,
) -> list[Line]:
    """答えの行のうち、使えるものだけを返す (``before`` は既に持っている行)。"""
    last_start = before[-1]["start"] if before else float("-inf")
    kept: list[Line] = []
    for line in got:
        start, text, speaker = line["start"], line["text"], line["speaker"]
        if duration_sec and start > duration_sec + 1:
            continue  # 録音より後の時刻 (作り話)
        if start < from_sec - _BEFORE_RANGE_SLACK_SEC:
            continue  # 頼んだ範囲より前 (前の答えが持っている)
        if any(
            r["text"] == text and abs(r["start"] - start) <= _SAME_LINE_SEC for r in before[-5:]
        ):
            continue  # 続きの読み直しで同じ行がもう一度来た
        if start < last_start - _BACKWARD_SLACK_SEC:
            continue  # 時刻の逆行
        tail = (list(before) + kept)[-2:]
        if len(tail) == 2 and all(t["text"] == text and t["speaker"] == speaker for t in tail):
            continue  # ループ: 同じ話し手の同じ文が 3 回目
        end = max(line["end"], start)
        if duration_sec:
            end = min(end, float(duration_sec))
        kept.append({**line, "end": round(max(end, start), 1)})
        last_start = max(last_start, start)
    return kept


async def transcribe(
    ask: Callable[[str], Awaitable[ModelReply]],
    build_prompt: Callable[[float | None, Sequence[Line]], str],
    *,
    duration_sec: float | None,
) -> list[Line]:
    """音声を最後まで行にする。``ask`` は 1 回の呼び出し (再試行込み)・``build_prompt`` は
    ``(続きの時刻 or None, 直前の行)`` → 指示文。

    Raises:
        VoiceAiError: ``parse`` (切れていないのに読めない) / ``incomplete`` (読み切れない)、
            ほか ``ask`` が投げたもの。
    """
    duration = float(duration_sec) if duration_sec and duration_sec > 0 else None
    items: list[Line] = []
    from_sec = 0.0
    for part in range(MAX_CONTINUATIONS + 1):
        prompt = build_prompt(None if part == 0 else from_sec, items)
        reply = await ask(prompt)
        cut = reply.finish_reason == "MAX_TOKENS"
        got, readable = parse_items(reply.text)
        if not readable and not cut:
            raise VoiceAiError("parse", "transcript answer was not a JSON array", body=reply.text)
        new = accept(got, items, from_sec, duration)
        items.extend(new)
        if not cut:
            return items
        if not new:
            raise VoiceAiError("incomplete", "cut off without a new complete line")
        if duration is not None and items[-1]["end"] >= duration - _END_SLACK_SEC:
            return items
        # 最後の行の始まりから (終わりは伸びていることがある)。
        from_sec = max(from_sec + 1, items[-1]["start"])
    raise VoiceAiError("incomplete", f"still cut off after {MAX_CONTINUATIONS} continuations")


def transcript_text(lines: Sequence[Line]) -> str:
    """行 → 今の ``transcript`` の形 (``看護師: …`` を 1 行 1 発話)。"""
    return "\n".join(f"{line.get('speaker') or '不明'}: {line.get('text') or ''}" for line in lines)
