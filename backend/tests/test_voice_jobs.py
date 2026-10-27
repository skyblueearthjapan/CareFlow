"""音声記録のジョブ (``app.services.voice.jobs``) のテスト — v2 の 3 段.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-4、
v2 は ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3・§4・§6。

AI は ``vertex_client.set_test_client()`` で偽クライアントに差し替える
(ネットワーク・認証情報を一切使わない)。網羅:

* 成功 → ① 行 → ② 直した行 → ③ v2 の要約。保存列・費用の合計・通知 1 行。
* ① 上限で切れたら続きを頼む (重なりは捨てる)・4 回で読み切れなければ ``incomplete``。
* ① ``timeout`` は 1 回だけ再実行・``429`` は待ってから 1 回・``parse`` は再実行しない。
* ① 声が無い (行 0) → ``transcript_empty``。
* ② 全部失敗しても止めない (元の行でまとめへ)・検査に通らない区切りは元の行。
* ③ 本文が空 → ``summary_parse``・呼び出し失敗 → ``summary_<kind>``。
* クライアントが作れない・provider=none・音声が無い・20 MB 超・削除済み/処理中。
* 患者未紐付けの行は ``unlinked`` を維持したまま要約を保存する。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import Notification, Patient, Staff, User
from app.models.visit_recording import VisitRecording
from app.services.voice import audio_split, jobs, vertex_client
from app.services.voice.vertex_client import ModelReply, VoiceAiError

LINES = [
    {"start": 0, "end": 2.5, "speaker": "看護師", "text": "今日の体調はいかがですか。"},
    {"start": 3, "end": 5, "speaker": "患者", "text": "じょくそうが少し痛いです。"},
]
FIXED_ANSWER = "[0:00] 看護師: 今日の体調はいかがですか。\n[0:03] 患者: 褥瘡が少し痛いです。"
FACTS = {
    "vitals": {"血圧": "132/78"},
    "handover": ["褥瘡の痛みを主治医へ"],
    "next": "",
    "needs_check": [{"text": "痛みの場所", "time": "0:03"}],
}
SUMMARY_ANSWER = (
    "褥瘡の痛みの訴えあり [0:03]。\n\n```json\n" + json.dumps(FACTS, ensure_ascii=False) + "\n```"
)


def _reply(text: str, *, finish: str | None = "STOP", cost: str = "0.001") -> ModelReply:
    return ModelReply(
        text=text,
        finish_reason=finish,
        tokens_in=1000,
        tokens_out=200,
        cost_usd=Decimal(cost),
        model="gemini-2.5-flash",
        raw_usage={},
    )


def _lines_reply(lines=LINES, *, finish: str | None = "STOP") -> ModelReply:
    return _reply(json.dumps(lines, ensure_ascii=False), finish=finish)


def _default_text(prompt: str) -> ModelReply:
    if "校正係" in prompt:
        return _reply(FIXED_ANSWER)
    return _reply(SUMMARY_ANSWER)


class _FakeVoiceClient:
    """① は台本 (``audio``)、② ③ は関数 (``text``: 指示文 → 答え or 例外) で返す偽クライアント。"""

    def __init__(self, *audio: object, text=None) -> None:
        self._audio = list(audio)
        self._text = text or _default_text
        self.audio_calls: list[tuple[bytes, str, str]] = []
        self.text_calls: list[str] = []
        self.closed = 0

    @property
    def model(self) -> str:
        return "gemini-2.5-flash"

    async def transcribe_lines(self, audio_bytes: bytes, mime_type: str, prompt: str):
        self.audio_calls.append((audio_bytes, mime_type, prompt))
        item = self._audio.pop(0) if self._audio else _lines_reply()
        if isinstance(item, Exception):
            raise item
        return item

    async def generate_text(self, prompt: str, **_kw):
        self.text_calls.append(prompt)
        item = self._text(prompt)
        if isinstance(item, Exception):
            raise item
        return item

    def correction_prompts(self) -> list[str]:
        return [p for p in self.text_calls if "校正係" in p]

    def summary_prompts(self) -> list[str]:
        return [p for p in self.text_calls if "校正係" not in p]

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture
def voice_env(tmp_path, monkeypatch):
    """``VOICE_AI_PROVIDER=vertex`` + 音声置き場を tmp_path にする。"""
    monkeypatch.setenv("VOICE_AI_PROVIDER", "vertex")
    monkeypatch.setenv("VISIT_AUDIO_DIR", str(tmp_path / "visit_audio"))
    # ffmpeg の有無で結果が変わらないように、既定は「区切らない」(区切る試験は下で差し替える)。
    monkeypatch.setattr(audio_split, "available", lambda: False)
    get_settings.cache_clear()
    yield tmp_path
    vertex_client.set_test_client(None)
    get_settings.cache_clear()


def _install(client: _FakeVoiceClient) -> _FakeVoiceClient:
    vertex_client.set_test_client(client)  # type: ignore[arg-type]
    return client


@pytest_asyncio.fixture
async def seeded(db, voice_env):
    """スタッフ + ユーザー + 患者 + 音声ファイル付きの録音 1 件。"""
    staff = Staff(name="録音 花子")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    user = User(
        email="voice-job@example.com",
        password_hash=hash_password("x"),
        role="staff",
        staff_id=staff.id,
    )
    patient = Patient(code="VJ-1", name="利用者 太郎", status="active")
    db.add_all([user, patient])
    await db.commit()
    await db.refresh(user)
    await db.refresh(patient)

    audio = voice_env / "rec.webm"
    audio.write_bytes(b"fake-audio-bytes")

    row = VisitRecording(
        patient_id=patient.id,
        staff_id=staff.id,
        recorded_at=datetime.now(UTC),
        duration_sec=120,
        audio_path=str(audio),
        audio_mime="audio/webm",
        audio_bytes=audio.stat().st_size,
        status="uploaded",
        consent_confirmed=True,
        created_by_user_id=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, user, patient, audio


# ---------------------------------------------------------------------------
# 成功系


@pytest.mark.asyncio
async def test_success_runs_three_stages_and_saves_columns(db, seeded) -> None:
    row, user, patient, _audio = seeded
    fake = _install(_FakeVoiceClient(_lines_reply()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    # 直す前の行 / 直した行 / 文字列は直した行から。
    assert row.transcript_json == LINES
    assert [line["text"] for line in row.transcript_fixed_json] == [
        "今日の体調はいかがですか。",
        "褥瘡が少し痛いです。",
    ]
    assert row.transcript_fixed_json[1]["start"] == 3 and row.transcript_fixed_json[1]["end"] == 5
    assert row.transcript == "看護師: 今日の体調はいかがですか。\n患者: 褥瘡が少し痛いです。"
    # v2 の要約 (本文 + 事実)。
    assert row.summary["format"] == "v2"
    assert row.summary["body"] == "褥瘡の痛みの訴えあり [0:03]。"
    assert row.summary_text == row.summary["body"]
    assert row.summary["facts"]["vitals"]["血圧"] == "132/78"
    assert row.summary["facts"]["needs_check"] == [{"text": "痛みの場所", "time": "0:03"}]
    assert row.provider == "vertex"
    assert row.model == "gemini-2.5-flash"
    assert row.prompt_version == "v2"
    # 3 回分 (① 1 回・② 1 区切り・③ 1 回) の合計。
    assert row.tokens_in == 3000
    assert row.tokens_out == 600
    assert Decimal(str(row.cost_usd)) == Decimal("0.003")
    assert row.error_message is None and row.error_kind is None

    # ① 音声と MIME がそのまま渡り、指示文に文脈が載る。
    assert len(fake.audio_calls) == 1
    audio_bytes, mime, prompt = fake.audio_calls[0]
    assert audio_bytes == b"fake-audio-bytes" and mime == "audio/webm"
    assert patient.name in prompt and "録音 花子" in prompt and "（長さ 2:00）" in prompt
    # ② には直す前の行、③ には直した行が渡る。
    (fix_prompt,) = fake.correction_prompts()
    assert "[0:03] 患者: じょくそうが少し痛いです。" in fix_prompt
    (sum_prompt,) = fake.summary_prompts()
    assert "[0:03] 患者: 褥瘡が少し痛いです。" in sum_prompt and "じょくそう" not in sum_prompt
    assert fake.closed == 1

    notes = (await db.scalars(select(Notification).where(Notification.user_id == user.id))).all()
    assert len(notes) == 1
    assert notes[0].type == jobs.NOTIFY_TYPE_VOICE_SUMMARY
    assert notes[0].title == jobs.NOTIFY_TITLE
    assert patient.name in notes[0].body
    assert notes[0].reference_type == jobs.NOTIFY_REFERENCE_TYPE
    assert notes[0].reference_id == row.id
    await db.rollback()


@pytest.mark.asyncio
async def test_cut_answer_is_continued_from_last_line(db, seeded) -> None:
    """上限で切れたら最後の完全な行の時刻から続きを頼み、重なった行は捨てる。"""
    row, _user, _patient, _audio = seeded
    cut = (
        json.dumps(LINES, ensure_ascii=False)[:-1]
        + ', {"start": 6, "end": 8, "speaker": "看護師", "text": "途中で'
    )
    rest = [
        LINES[1],  # 続きの読み直しで同じ行がもう一度来る → 捨てる
        {"start": 6, "end": 8, "speaker": "看護師", "text": "お薬は飲めていますか。"},
        {"start": 9, "end": 10, "speaker": "患者", "text": "はい。"},
    ]
    fake = _install(_FakeVoiceClient(_reply(cut, finish="MAX_TOKENS"), _lines_reply(rest)))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    assert len(fake.audio_calls) == 2
    assert "0:03（3 秒）から最後までの部分だけ" in fake.audio_calls[1][2]
    assert "[0:03] 患者: じょくそうが少し痛いです。" in fake.audio_calls[1][2]  # 直前の行を見せる
    assert [line["text"] for line in row.transcript_json] == [
        "今日の体調はいかがですか。",
        "じょくそうが少し痛いです。",
        "お薬は飲めていますか。",
        "はい。",
    ]
    assert row.tokens_in == 4000  # ① 2 回 + ② + ③
    await db.rollback()


@pytest.mark.asyncio
async def test_continuation_limit_fails_as_incomplete(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    replies = [
        _lines_reply(
            [{"start": i * 10, "end": i * 10 + 2, "speaker": "看護師", "text": f"話{i}"}],
            finish="MAX_TOKENS",
        )
        for i in range(6)
    ]
    fake = _install(_FakeVoiceClient(*replies))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.audio_calls) == 5  # 最初 + 続き 4 回
    assert row.status == "failed"
    assert row.error_kind == "incomplete"
    assert row.error_message == jobs.AI_FAILED_MESSAGE
    assert fake.text_calls == []
    await db.rollback()


@pytest.mark.asyncio
async def test_cut_without_complete_line_fails(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    _install(_FakeVoiceClient(_reply('[{"start": 0, "end": 1, "spea', finish="MAX_TOKENS")))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed" and row.error_kind == "incomplete"
    await db.rollback()


@pytest.mark.asyncio
async def test_timeout_is_retried_once_then_succeeds(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    fake = _install(_FakeVoiceClient(VoiceAiError("timeout", "deadline"), _lines_reply()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.audio_calls) == 2
    assert row.status == "summarized"
    assert row.error_message is None
    await db.rollback()


@pytest.mark.asyncio
async def test_unlinked_keeps_status_and_saves_summary(db, seeded) -> None:
    """患者未紐付けの行は ``unlinked`` を維持したまま要約だけ保存する。"""
    row, _user, _patient, _audio = seeded
    row.patient_id = None
    row.status = "unlinked"
    await db.commit()
    _install(_FakeVoiceClient(_lines_reply()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "unlinked"
    assert row.summary_text is not None
    assert row.transcript is not None
    await db.rollback()


# ---------------------------------------------------------------------------
# ② 補正は失敗しても止めない


@pytest.mark.asyncio
async def test_correction_failure_keeps_raw_lines_and_continues(db, seeded) -> None:
    row, _user, _patient, _audio = seeded

    def text(prompt: str):
        if "校正係" in prompt:
            return VoiceAiError("parse", "auth trouble")  # 再試行しない種別
        return _reply(SUMMARY_ANSWER)

    fake = _install(_FakeVoiceClient(_lines_reply(), text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized" and row.error_kind is None
    assert row.transcript_fixed_json is None
    assert row.transcript == "看護師: 今日の体調はいかがですか。\n患者: じょくそうが少し痛いです。"
    (sum_prompt,) = fake.summary_prompts()
    assert "[0:03] 患者: じょくそうが少し痛いです。" in sum_prompt
    assert row.tokens_in == 2000  # 失敗した呼び出しは数えない
    await db.rollback()


@pytest.mark.asyncio
async def test_correction_that_fails_the_check_uses_raw_lines(db, seeded) -> None:
    """答えが要約になっている (文字数が半分未満) → 直す前の行で進む。"""
    row, _user, _patient, _audio = seeded

    def text(prompt: str):
        if "校正係" in prompt:
            return _reply("[0:00] 看護師: 体調")
        return _reply(SUMMARY_ANSWER)

    _install(_FakeVoiceClient(_lines_reply(), text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    assert row.transcript_fixed_json is None
    assert "じょくそう" in row.transcript
    await db.rollback()


# ---------------------------------------------------------------------------
# 失敗系


@pytest.mark.asyncio
async def test_parse_error_fails_without_retry(db, seeded) -> None:
    row, user, _patient, _audio = seeded
    fake = _install(_FakeVoiceClient(VoiceAiError("parse", "model output was not JSON")))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.audio_calls) == 1  # parse は再実行しない
    assert row.status == "failed"
    # 画面に出るのは日本語の固定文言。AI の生メッセージは漏らさない。
    assert row.error_message == jobs.AI_FAILED_MESSAGE
    assert "model output was not JSON" not in (row.error_message or "")
    # 種別だけ機械向けの列に残す。
    assert row.error_kind == "parse"
    assert row.transcript is None
    # 失敗のときは通知しない。
    assert (await db.scalar(select(Notification.id).where(Notification.user_id == user.id))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_unreadable_transcript_answer_is_parse_failure(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    _install(_FakeVoiceClient(_reply("JSON ではない答え")))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed" and row.error_kind == "parse"
    await db.rollback()


@pytest.mark.asyncio
async def test_silent_recording_fails_as_transcript_empty(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    fake = _install(_FakeVoiceClient(_lines_reply([])))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_kind == jobs.ERROR_KIND_EMPTY_TRANSCRIPT
    assert row.error_message == jobs.EMPTY_TRANSCRIPT_MESSAGE
    assert fake.text_calls == []
    await db.rollback()


@pytest.mark.asyncio
async def test_summary_without_body_fails_as_summary_parse(db, seeded) -> None:
    row, user, _patient, _audio = seeded

    def text(prompt: str):
        if "校正係" in prompt:
            return _reply(FIXED_ANSWER)
        return _reply("```json\n" + json.dumps(FACTS, ensure_ascii=False) + "\n```")

    _install(_FakeVoiceClient(_lines_reply(), text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_kind == "summary_parse"
    assert row.error_message == jobs.AI_FAILED_MESSAGE
    assert row.summary is None and row.transcript is None
    assert (await db.scalar(select(Notification.id).where(Notification.user_id == user.id))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_summary_timeout_is_retried_once_then_fails(db, seeded) -> None:
    row, _user, _patient, _audio = seeded

    def text(prompt: str):
        if "校正係" in prompt:
            return _reply(FIXED_ANSWER)
        return VoiceAiError("timeout", "slow")

    fake = _install(_FakeVoiceClient(_lines_reply(), text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.summary_prompts()) == 2  # 1 回だけ再実行
    assert row.status == "failed" and row.error_kind == "summary_timeout"
    await db.rollback()


@pytest.mark.asyncio
async def test_missing_audio_file_fails(db, seeded) -> None:
    row, _user, _patient, audio = seeded
    Path(audio).unlink()
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_message == jobs.AUDIO_MISSING_MESSAGE
    assert row.error_kind == jobs.ERROR_KIND_AUDIO_MISSING
    assert fake.audio_calls == []
    await db.rollback()


@pytest.mark.asyncio
async def test_too_large_audio_fails(db, seeded, monkeypatch) -> None:
    """inline 上限超過は AI を呼ばずに ``too_large`` で failed."""
    row, _user, _patient, _audio = seeded
    monkeypatch.setattr(jobs, "MAX_INLINE_AUDIO_BYTES", 4)
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_message == jobs.TOO_LARGE_MESSAGE
    assert row.error_kind == jobs.ERROR_KIND_TOO_LARGE
    assert fake.audio_calls == []
    await db.rollback()


@pytest.mark.asyncio
async def test_rate_limited_call_waits_then_retries_once(db, seeded, monkeypatch) -> None:
    """429 は即やり直さず ``RATE_LIMIT_BACKOFF_SECONDS`` 待ってから 1 回だけ."""
    row, _user, _patient, _audio = seeded
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(jobs.asyncio, "sleep", _fake_sleep)
    fake = _install(
        _FakeVoiceClient(VoiceAiError("http", "rate limited", status_code=429), _lines_reply())
    )

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.audio_calls) == 2
    assert slept == [jobs.RATE_LIMIT_BACKOFF_SECONDS]
    assert row.status == "summarized"
    await db.rollback()


@pytest.mark.asyncio
async def test_missing_ai_client_marks_config_failure(db, seeded, monkeypatch) -> None:
    """provider は vertex なのにクライアントが作れない = 設定ミスとして failed."""
    row, _user, _patient, _audio = seeded
    vertex_client.set_test_client(None)
    monkeypatch.setattr(jobs, "get_voice_client", lambda _settings: None)

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_message == jobs.AI_CONFIG_ERROR_MESSAGE
    assert row.error_kind == jobs.ERROR_KIND_CONFIG
    await db.rollback()


@pytest.mark.asyncio
async def test_provider_none_is_noop(db, seeded, monkeypatch) -> None:
    row, _user, _patient, _audio = seeded
    monkeypatch.setenv("VOICE_AI_PROVIDER", "none")
    get_settings.cache_clear()
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "uploaded"
    assert fake.audio_calls == []
    assert row.transcript is None
    await db.rollback()


@pytest.mark.asyncio
async def test_deleted_or_transcribing_rows_are_skipped(db, seeded) -> None:
    """削除済み / 既に処理中の行は起動しない (二重起動の後勝ちを防ぐ)."""
    row, _user, _patient, _audio = seeded
    row.status = "transcribing"
    await db.commit()
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.status == "transcribing"
    assert fake.audio_calls == []

    row.status = "uploaded"
    row.deleted_at = datetime.now(UTC)
    await db.commit()
    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.status == "uploaded"
    assert fake.audio_calls == []
    await db.rollback()


# ---------------------------------------------------------------------------
# 長い録音を区切る (P1b)


@pytest.mark.asyncio
async def test_long_recording_is_read_piece_by_piece_and_times_are_shifted(
    db, seeded, monkeypatch
) -> None:
    """区切りごとに ① を頼み、区切りの始まりの分だけ時刻をずらしてつなぐ。"""
    row, _user, _patient, _audio = seeded
    pieces = [
        audio_split.Piece(start=0.0, end=700.0, data=b"piece-1"),
        audio_split.Piece(start=700.0, end=1500.0, data=b"piece-2"),
    ]

    async def fake_split(_path, fallback_duration=None):
        return audio_split.SplitResult(pieces, 1500.0)

    monkeypatch.setattr(audio_split, "split_audio", fake_split)
    second = [
        {"start": "0:02", "end": "0:04", "speaker": "看護師", "text": "では足の運動をしましょう。"},
        {"start": "1:10", "end": "1:12", "speaker": "患者", "text": "はい。"},
    ]
    fake = _install(_FakeVoiceClient(_lines_reply(), _lines_reply(second)))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    assert [(c[0], c[1]) for c in fake.audio_calls] == [
        (b"piece-1", "audio/mpeg"),
        (b"piece-2", "audio/mpeg"),
    ]
    # 2 つ目の区切りの指示: 長さはその区切りの分・前の区切りの終わりを時刻なしで見せる。
    second_prompt = fake.audio_calls[1][2]
    assert "（長さ 13:20）" in second_prompt
    assert "長い録音の途中の部分" in second_prompt
    assert "  患者: じょくそうが少し痛いです。" in second_prompt
    assert [line["start"] for line in row.transcript_json] == [0, 3, 702.0, 770.0]
    await db.rollback()


@pytest.mark.asyncio
async def test_split_failure_falls_back_to_the_whole_recording(db, seeded, monkeypatch) -> None:
    """区切れない (ffmpeg が音声を読めない) ときは 1 本で送る。"""
    row, _user, _patient, audio = seeded

    async def broken(_path, fallback_duration=None):
        raise audio_split.AudioSplitError("Invalid data")

    monkeypatch.setattr(audio_split, "split_audio", broken)
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    assert fake.audio_calls[0][0] == audio.read_bytes()
    assert fake.audio_calls[0][1] == "audio/webm"
    await db.rollback()


@pytest.mark.asyncio
async def test_transcript_stopped_by_safety_is_not_treated_as_complete(db, seeded) -> None:
    """SAFETY などで途中で止まった答えを読み切ったとして扱わない。費用は残す。"""
    row, _user, _patient, _audio = seeded
    _install(_FakeVoiceClient(_lines_reply(finish="SAFETY")))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_kind == "blocked"
    assert row.tokens_in == 1000 and row.cost_usd == Decimal("0.001")
    await db.rollback()


@pytest.mark.asyncio
async def test_summary_cut_by_the_limit_fails_and_keeps_the_cost(db, seeded) -> None:
    row, _user, _patient, _audio = seeded

    def text(prompt: str):
        if "校正係" in prompt:
            return _reply(FIXED_ANSWER)
        return _reply(SUMMARY_ANSWER, finish="MAX_TOKENS")

    _install(_FakeVoiceClient(text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_kind == "summary_cut"
    assert row.tokens_in == 3000  # ① + ② + ③ の費用が残る
    await db.rollback()


@pytest.mark.asyncio
async def test_job_stops_when_the_row_was_reaped_meanwhile(db, seeded) -> None:
    """処理中に stale 掃除で failed に倒されたら、続きを頼まず結果も書き戻さない。"""
    row, _user, _patient, _audio = seeded

    class _Reaped(_FakeVoiceClient):
        async def transcribe_lines(self, audio_bytes, mime_type, prompt):
            reply = await super().transcribe_lines(audio_bytes, mime_type, prompt)
            factory = jobs.get_session_factory()
            async with factory() as other:
                found = await other.get(VisitRecording, row.id)
                found.status = "failed"
                found.error_kind = "stale"
                await other.commit()
            return reply

    fake = _install(_Reaped())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_kind == "stale"
    assert fake.text_calls == []  # ② ③ へ進まない
    await db.rollback()
