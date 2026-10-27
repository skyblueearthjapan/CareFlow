"""長い録音を区切る (``app.services.voice.audio_split``・設計 §8 P1b).

ffmpeg は開発 PC に無いので、呼び出し (``_run``) と有無 (``available``) を差し替えて試す。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.voice import audio_split
from app.services.voice.audio_split import (
    MIN_PIECE_SEC,
    SEARCH_SEC,
    SPLIT_OVER_SEC,
    AudioSplitError,
    parse_last_time,
    parse_silences,
    plan_cuts,
)

SILENCE_LOG = """\
[silencedetect @ 0x1] silence_start: 12.5
[silencedetect @ 0x1] silence_end: 13.4 | silence_duration: 0.9
size=N/A time=00:20:00.00
[silencedetect @ 0x1] silence_start: 590.0
[silencedetect @ 0x1] silence_end: 593.0 | silence_duration: 3.0
[silencedetect @ 0x1] silence_start: 1199.0
"""


def test_parse_silences_pairs_start_and_end_and_drops_open_tail() -> None:
    assert parse_silences(SILENCE_LOG) == [(12.5, 13.4), (590.0, 593.0)]


def test_short_recording_is_not_cut() -> None:
    assert plan_cuts(SPLIT_OVER_SEC, [(400.0, 405.0)]) == []


def test_cuts_land_on_the_longest_silence_near_each_target() -> None:
    # 30 分 → 3 つ (目安 600 秒・1200 秒)。600 秒の近くに短い無音と長い無音がある。
    silences = [(570.0, 571.0), (620.0, 624.0), (1500.0, 1510.0)]
    cuts = plan_cuts(1800.0, silences)
    assert len(cuts) == 2
    assert cuts[0] == 622.0  # 長い方の真ん中
    assert cuts[1] == 1200.0  # 前後 60 秒に無音が無ければ目安のまま (1505 は遠い)


def test_silence_outside_the_search_window_is_ignored() -> None:
    cuts = plan_cuts(1800.0, [(600.0 + SEARCH_SEC + 5, 600.0 + SEARCH_SEC + 9)])
    assert cuts[0] == 600.0


def test_every_piece_is_long_enough() -> None:
    cuts = plan_cuts(3400.0, [])
    bounds = [0.0, *cuts, 3400.0]
    assert all(b - a >= MIN_PIECE_SEC for a, b in zip(bounds, bounds[1:], strict=False))
    assert all(b - a <= 15 * 60 for a, b in zip(bounds, bounds[1:], strict=False))


class _FakeFfmpeg:
    def __init__(self, duration: float | str, silences: str = "", fail_cut: bool = False) -> None:
        self.duration = duration
        self.silences = silences
        self.fail_cut = fail_cut
        self.cuts: list[tuple[str, str]] = []

    async def __call__(self, args: list[str], timeout: float = 0.0):
        if args[0] == "ffprobe":
            return 0, f"{self.duration}\n".encode(), b""
        if "silencedetect" in " ".join(args):
            return 0, b"", self.silences.encode()
        start, length = args[args.index("-ss") + 1], args[args.index("-t") + 1]
        self.cuts.append((start, length))
        if self.fail_cut:
            return 1, b"", b"broken"
        return 0, f"mp3:{start}".encode(), b""


@pytest.fixture
def ffmpeg(monkeypatch):
    def install(fake: _FakeFfmpeg) -> _FakeFfmpeg:
        monkeypatch.setattr(audio_split, "available", lambda: True)
        monkeypatch.setattr(audio_split, "_run", fake)
        return fake

    return install


@pytest.mark.asyncio
async def test_split_audio_cuts_long_recording_into_mp3_pieces(ffmpeg, tmp_path: Path) -> None:
    fake = ffmpeg(_FakeFfmpeg(1800.0, SILENCE_LOG))
    result = await audio_split.split_audio(tmp_path / "rec.webm")
    pieces = result.pieces
    assert pieces is not None
    assert result.duration == 1800.0
    assert [(p.start, p.end) for p in pieces] == [(0.0, 591.5), (591.5, 1200.0), (1200.0, 1800.0)]
    assert all(p.mime == "audio/mpeg" for p in pieces)
    assert pieces[1].data == b"mp3:591.50"
    assert fake.cuts[1] == ("591.50", "608.50")


@pytest.mark.asyncio
async def test_split_audio_leaves_short_recording_whole(ffmpeg, tmp_path: Path) -> None:
    fake = ffmpeg(_FakeFfmpeg(600.0))
    result = await audio_split.split_audio(tmp_path / "rec.webm")
    assert result.pieces is None
    assert result.duration == 600.0
    assert fake.cuts == []


@pytest.mark.asyncio
async def test_split_audio_without_ffmpeg_sends_whole(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audio_split, "available", lambda: False)
    result = await audio_split.split_audio(tmp_path / "rec.webm")
    assert result.pieces is None and result.duration is None


@pytest.mark.asyncio
async def test_split_audio_raises_when_a_cut_fails(ffmpeg, tmp_path: Path) -> None:
    ffmpeg(_FakeFfmpeg(1800.0, fail_cut=True))
    with pytest.raises(AudioSplitError):
        await audio_split.split_audio(tmp_path / "rec.webm")


@pytest.mark.asyncio
async def test_unreadable_duration_raises(monkeypatch, tmp_path: Path) -> None:
    async def broken(args, timeout=0.0):
        return 1, b"", b"Invalid data found when processing input"

    monkeypatch.setattr(audio_split, "available", lambda: True)
    monkeypatch.setattr(audio_split, "_run", broken)
    with pytest.raises(AudioSplitError):
        await audio_split.split_audio(tmp_path / "rec.webm")


def test_parse_last_time_reads_the_final_progress() -> None:
    log = "size=N/A time=00:12:00.50 bitrate=N/A\rsize=N/A time=00:56:27.80 bitrate=N/A\n"
    assert parse_last_time(log) == 3387.8
    assert parse_last_time("no progress") is None


@pytest.mark.asyncio
async def test_webm_without_duration_uses_what_ffmpeg_read(ffmpeg, tmp_path: Path) -> None:
    """端末の MediaRecorder の webm は長さを持たない (ffprobe は N/A)。ffmpeg が最後まで読んだ時刻で区切る。"""
    fake = ffmpeg(_FakeFfmpeg("N/A", SILENCE_LOG + "size=N/A time=00:30:00.00 bitrate=N/A\n"))
    result = await audio_split.split_audio(tmp_path / "rec.webm", fallback_duration=120.0)
    assert result.duration == 1800.0
    assert result.pieces is not None and len(result.pieces) == 3
    assert len(fake.cuts) == 3


@pytest.mark.asyncio
async def test_webm_without_any_length_falls_back_to_the_device_value(
    ffmpeg, tmp_path: Path
) -> None:
    no_progress = "".join(line for line in SILENCE_LOG.splitlines(True) if "time=" not in line)
    ffmpeg(_FakeFfmpeg("N/A", no_progress))
    result = await audio_split.split_audio(tmp_path / "rec.webm", fallback_duration=1800.0)
    assert result.duration == 1800.0
    assert result.pieces is not None and len(result.pieces) == 3


@pytest.mark.asyncio
async def test_no_length_anywhere_raises(ffmpeg, tmp_path: Path) -> None:
    ffmpeg(_FakeFfmpeg("N/A", ""))
    with pytest.raises(AudioSplitError):
        await audio_split.split_audio(tmp_path / "rec.webm")
