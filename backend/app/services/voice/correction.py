"""② 補正 — 文字起こしの聞き違いを、文字だけの呼び出しで直す (部分ごとに検査・失敗しても止めない).

正典: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P2・§4。
約束と検査は PC-Assistant の ``TranscriptCorrection`` (``Prompt``・``Parse``) を看護向けに写した:

* 10 分ごとに区切って頼む (``CHUNK_SEC``)。区切りは並べて投げる (``PARALLEL`` まで)。
* 答えの行の時刻は元の行の時刻のどれか (順番どおり) でなければならない。無い時刻・逆の順 → その区切りは元の行。
* 文字数が元の半分未満 / 1.3 倍超 (+20 字) → その区切りは元の行 (落とした・足した)。
* 最初の答えの行より前の元の行を黙って落とした / つないだ行の言葉の大半が消えた → 元の行。
* 同じ話し手の細切れの行をつなぐのは許す (時刻は最初の行・終わりはつないだ最後の行)。
  話し手が違う行をつないだら元の行。話し手のラベルは **元の行のものを使う** (答えの方は見ない)。
* 呼び出しの失敗・形の違う答えも、その区切りは元の行。全部失敗したら ``None`` (呼び出し側が元の行で進む)。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.services.voice import prompts
from app.services.voice.errors import JobSupersededError
from app.services.voice.vertex_client import ModelReply

logger = logging.getLogger(__name__)

#: 1 回に頼む長さ (秒)。
CHUNK_SEC = 600.0
#: 同時に投げる区切りの数。
PARALLEL = 3
#: 言葉を書かずに落としてよい字数 (「えー」「あのー、はい」)。
DROPPED_FILLER = 4

_LINE_RE = re.compile(r"^\s*[\[［](?P<t>\d{1,2}(?::\d{2}){1,2})[\]］]\s*(?P<rest>.*)$")
_WHO_RE = re.compile(r"^(?P<who>[^:：\s][^:：]{0,9}?)\s*(?::\s*|：\s*)(?P<text>.*)$")
_MARKS = (prompts.LOST, "（？）", "(?)", "（?）")

Line = dict[str, Any]


def split_parts(lines: Sequence[Line], chunk_sec: float = CHUNK_SEC) -> list[list[Line]]:
    """行を ``chunk_sec`` ごとの区切りに分ける (空の区切りは作らない)。"""
    parts: list[list[Line]] = []
    for line in lines:
        start = float(line.get("start") or 0)
        if not parts or start >= float(parts[-1][0].get("start") or 0) + chunk_sec:
            parts.append([])
        parts[-1].append(line)
    return parts


def _count(text: str) -> int:
    """話の文字数 (空白と（聞き取れず）・（？）の印は数えない)。"""
    for mark in _MARKS:
        text = text.replace(mark, "")
    return sum(1 for c in text if not c.isspace())


def _seconds(label: str) -> float:
    total = 0.0
    for piece in label.split(":"):
        total = total * 60 + int(piece)
    return total


def parse_correction(answer: str, part: Sequence[Line]) -> list[Line] | None:
    """答え → 直した行。区切りの直しになっていなければ None (呼び出し側は元の行を使う)。"""
    if not part:
        return None
    found: list[tuple[int, str]] = []
    cursor = 0
    for raw in (answer or "").replace("\r\n", "\n").split("\n"):
        match = _LINE_RE.match(raw)
        if not match:
            stripped = raw.strip()
            if stripped and not stripped.startswith("```") and found:
                index, text = found[-1]
                found[-1] = (index, text + stripped)  # 折り返された行
            continue
        label = prompts.clock(_seconds(match.group("t")))  # 「04:12」も「4:12」
        index = next(
            (j for j in range(cursor, len(part)) if prompts.clock(part[j].get("start")) == label),
            -1,
        )
        if index < 0:
            return None  # 元に無い時刻 (作った・順番が逆)
        cursor = index + 1
        text = match.group("rest").strip()
        who = _WHO_RE.match(text)
        if who and who.group("who").strip() in prompts.SPEAKER_LABELS:
            text = who.group("text").strip()
        if text:
            found.append((index, text))
    if not found:
        return None

    before = sum(_count(str(line.get("text") or "")) for line in part)
    after = sum(_count(text) for _, text in found)
    if after < before / 2 or after > before * 13 / 10 + 20:
        return None  # 大きく落とした・足した
    if sum(_count(str(line.get("text") or "")) for line in part[: found[0][0]]) > DROPPED_FILLER:
        return None  # 最初の答えの行より前の元の行を落とした

    fixed: list[Line] = []
    for k, (start_index, text) in enumerate(found):
        end_index = found[k + 1][0] if k + 1 < len(found) else len(part)
        covered = list(part[start_index : max(end_index, start_index + 1)])
        first = covered[0]
        if any(line.get("speaker") != first.get("speaker") for line in covered):
            return None  # 話し手の違う行をつないだ・間の行を落とした
        said = sum(_count(str(line.get("text") or "")) for line in covered)
        kept = _count(text)
        if kept * 5 < said * 2 and said - kept > DROPPED_FILLER:
            return None  # つないだ行の言葉の大半が消えた (途中の行を落とした)
        fixed.append(
            {
                **first,
                "end": max(float(line.get("end") or line.get("start") or 0) for line in covered),
                "text": " ".join(text.split()),
            }
        )
    return fixed


async def correct_lines(
    lines: Sequence[Line],
    ask: Callable[[str], Awaitable[ModelReply]],
    *,
    recording_id: Any = None,
) -> list[Line] | None:
    """行を区切りごとに直す。1 つも直せなければ None。例外は投げない (補正で止めない)。"""
    if not lines:
        return None
    parts = split_parts(lines)
    gate = asyncio.Semaphore(PARALLEL)

    async def _one(index: int, part: list[Line]) -> list[Line] | None:
        async with gate:
            try:
                reply = await ask(prompts.build_correction_prompt(part, index, len(parts)))
            except JobSupersededError:
                raise  # 受け持ちが替わった: 残りの区切りを AI に送らない
            except Exception as exc:  # noqa: BLE001 — その区切りは元の行で続ける
                logger.info(
                    "voice: 補正の区切り %d/%d が失敗 %s (recording_id=%s)",
                    index + 1,
                    len(parts),
                    getattr(exc, "kind", type(exc).__name__),
                    recording_id,
                )
                return None
        result = parse_correction(reply.text, part)
        if result is None:
            logger.info(
                "voice: 補正の区切り %d/%d は検査を通らず元の行を使います (recording_id=%s)",
                index + 1,
                len(parts),
                recording_id,
            )
        return result

    results = await asyncio.gather(*(_one(i, part) for i, part in enumerate(parts)))
    if not any(r is not None for r in results):
        return None
    merged: list[Line] = []
    for part, result in zip(parts, results, strict=True):
        merged.extend(result if result is not None else [dict(line) for line in part])
    return merged
