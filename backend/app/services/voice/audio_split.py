"""長い録音を区切る (設計 ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §8 P1b).

P0 の実測: Gemini 2.5 Flash は 1 回で読む音声が長いほど悪くなる (10 分 4.8%・29 分 7.2%・
56 分は約 16% を落とし並びも乱れる)。言葉で「この範囲だけ」と頼むと範囲の外を書くので、
**音声そのものを切って**送る。

- 15 分以下は切らない (``None`` を返し、今までどおり 1 本で送る)。
- それより長ければ約 12 分ずつに分ける。切れ目は目安の前後 60 秒で一番長い無音の真ん中
  (無音が無ければ目安の時刻そのまま)。
- 各区切りは 16 kHz・モノラル・32 kbps の MP3 にして渡す (端末の形式 mp4/AAC・webm/Opus は
  途中から切り出せないため ffmpeg で読み直す)。
- ffmpeg / ffprobe が無い環境 (開発 PC など) では切らずに ``None`` (警告を 1 行)。

ffmpeg の呼び出しは ``_run`` 1 か所に集める (試験はここを差し替える)。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: これより長い録音だけ区切る。
SPLIT_OVER_SEC = 15 * 60
#: 区切りの目安の長さ。
PIECE_TARGET_SEC = 12 * 60
#: 切れ目を探す幅 (目安の前後)。
SEARCH_SEC = 60.0
#: これより短い区切りは作らない。
MIN_PIECE_SEC = 60.0
#: 無音とみなす音量と長さ (silencedetect)。
SILENCE_NOISE = "-35dB"
SILENCE_MIN_SEC = 0.4
#: 送る形。
PIECE_MIME = "audio/mpeg"
PIECE_BITRATE = "32k"
#: ffmpeg 1 回の時間の上限 (秒)。
RUN_TIMEOUT_SEC = 180.0

_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


class AudioSplitError(Exception):
    """ffmpeg が使えるのに読めなかった (壊れた音声など)。"""


@dataclass(frozen=True)
class Piece:
    start: float
    end: float
    data: bytes = b""
    mime: str = PIECE_MIME

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(args: list[str], timeout: float = RUN_TIMEOUT_SEC) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise AudioSplitError(f"{args[0]} timed out after {timeout:.0f}s") from None
    return proc.returncode or 0, out, err


async def probe_duration(path: Path) -> float:
    rc, out, err = await _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        value = float(out.decode("ascii", "replace").strip())
    except ValueError:
        value = 0.0
    if rc != 0 or not math.isfinite(value) or value <= 0:
        raise AudioSplitError(f"ffprobe could not read the duration (rc={rc}): {err[-300:]!r}")
    return value


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """silencedetect の出力 → ``[(始まり, 終わり)]``。終わりの無い最後の無音は捨てる。"""
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SILENCE_END.search(line)
        if m and start is not None:
            end = float(m.group(1))
            if end > start:
                out.append((start, end))
            start = None
    return out


async def detect_silences(path: Path) -> list[tuple[float, float]]:
    rc, _out, err = await _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN_SEC}",
            "-f",
            "null",
            "-",
        ]
    )
    if rc != 0:
        raise AudioSplitError(f"ffmpeg silencedetect failed (rc={rc}): {err[-300:]!r}")
    return parse_silences(err.decode("utf-8", "replace"))


def plan_cuts(duration: float, silences: list[tuple[float, float]]) -> list[float]:
    """切れ目の時刻 (秒) を返す。15 分以下なら空。"""
    if duration <= SPLIT_OVER_SEC:
        return []
    count = math.ceil(duration / PIECE_TARGET_SEC)
    cuts: list[float] = []
    prev = 0.0
    for k in range(1, count):
        ideal = duration * k / count
        best: tuple[float, float] | None = None  # (無音の長さ, 目安からの近さの逆)
        cut = ideal
        for s, e in silences:
            mid = (s + e) / 2
            if abs(mid - ideal) > SEARCH_SEC:
                continue
            key = (e - s, -abs(mid - ideal))
            if best is None or key > best:
                best, cut = key, mid
        if cut - prev < MIN_PIECE_SEC or duration - cut < MIN_PIECE_SEC:
            continue
        cuts.append(round(cut, 2))
        prev = cut
    return cuts


async def cut_piece(path: Path, start: float, end: float) -> bytes:
    rc, out, err = await _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            f"{start:.2f}",
            "-t",
            f"{max(0.1, end - start):.2f}",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            PIECE_BITRATE,
            "-f",
            "mp3",
            "pipe:1",
        ]
    )
    if rc != 0 or not out:
        raise AudioSplitError(f"ffmpeg cut {start:.0f}-{end:.0f}s failed (rc={rc}): {err[-300:]!r}")
    return out


async def split_audio(path: Path) -> list[Piece] | None:
    """区切った音声を返す。切らなくてよい (短い・ffmpeg が無い) ときは ``None``。

    Raises:
        AudioSplitError: ffmpeg はあるのに音声を読めない・切れない。
    """
    if not available():
        logger.warning("voice: ffmpeg / ffprobe が無いため録音を区切らずに送ります")
        return None
    duration = await probe_duration(path)
    if duration <= SPLIT_OVER_SEC:
        return None
    cuts = plan_cuts(duration, await detect_silences(path))
    if not cuts:
        return None
    bounds = [0.0, *cuts, duration]
    pieces: list[Piece] = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        pieces.append(Piece(start=start, end=end, data=await cut_piece(path, start, end)))
    logger.info(
        "voice: 録音 %.0f 秒を %d つに区切りました (切れ目 %s)",
        duration,
        len(pieces),
        ", ".join(f"{c:.0f}" for c in cuts),
    )
    return pieces
