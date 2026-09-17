"""音声記録の文字起こしジョブ (``app.services.voice.jobs``) のテスト.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-4。

AI は ``vertex_client.set_test_client()`` で偽クライアントに差し替える
(ネットワーク・認証情報を一切使わない)。網羅:

* 成功 → ``summarized`` + 保存列 (transcript/summary/cost/model) + 通知 1 行。
* ``timeout`` は 1 回だけ再実行して 2 回目で成功する (合計 2 回)。
* ``429`` は ``RATE_LIMIT_BACKOFF_SECONDS`` 待ってから 1 回だけ再実行する。
* ``parse`` は再実行せず ``failed`` + 日本語の固定文言 + ``error_kind='parse'``。
* クライアントが作れない (設定ミス) → ``failed`` + ``error_kind='config'``。
* ``VOICE_AI_PROVIDER='none'`` は何もしない (status 据え置き・呼び出し 0 回)。
* 音声ファイルが消えている → ``failed`` + ``audio file missing``。
* 20 MB 超 → ``failed`` + ``too_large: ...``。
* 患者未紐付けの行は ``unlinked`` を維持したまま要約を保存する。
"""

from __future__ import annotations

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
from app.services.voice import jobs, vertex_client
from app.services.voice.vertex_client import VoiceAiError, VoiceAiResult

SUMMARY = {
    "主訴・様子": ["だるさの訴えあり"],
    "バイタル": {"血圧": "132/78", "体温": "36.7", "脈拍": "", "SpO2": "98", "血糖": ""},
    "処置・ケア": ["褥瘡の包交を実施"],
    "申し送り": [],
    "次回": "",
    "free": "",
}


def _result() -> VoiceAiResult:
    return VoiceAiResult(
        transcript="看護師: 今日の体調はいかがですか。",
        transcript_segments=[{"speaker": "看護師", "start_sec": 0, "text": "体調は?"}],
        summary=dict(SUMMARY),
        summary_text="【主訴・様子】\n・だるさの訴えあり",
        tokens_in=1200,
        tokens_out=300,
        cost_usd=Decimal("0.0021"),
        model="gemini-2.5-flash",
        prompt_version="v1",
        raw_usage={"promptTokenCount": 1200},
    )


class _FakeVoiceClient:
    """``transcribe_and_summarize`` の戻りを台本で差し込む偽クライアント。"""

    def __init__(self, *script: object) -> None:
        self._script = list(script)
        self.calls: list[tuple[bytes, str, str]] = []
        self.closed = 0

    @property
    def model(self) -> str:
        return "gemini-2.5-flash"

    async def transcribe_and_summarize(
        self, audio_bytes: bytes, mime_type: str, prompt: str
    ) -> VoiceAiResult:
        self.calls.append((audio_bytes, mime_type, prompt))
        item = self._script.pop(0) if self._script else _result()
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture
def voice_env(tmp_path, monkeypatch):
    """``VOICE_AI_PROVIDER=vertex`` + 音声置き場を tmp_path にする。"""
    monkeypatch.setenv("VOICE_AI_PROVIDER", "vertex")
    monkeypatch.setenv("VISIT_AUDIO_DIR", str(tmp_path / "visit_audio"))
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
async def test_success_saves_columns_and_notifies(db, seeded) -> None:
    row, user, patient, _audio = seeded
    fake = _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized"
    assert row.transcript.startswith("看護師:")
    assert row.transcript_json == [{"speaker": "看護師", "start_sec": 0, "text": "体調は?"}]
    assert row.summary["主訴・様子"] == ["だるさの訴えあり"]
    assert row.summary_text.startswith("【主訴・様子】")
    assert row.provider == "vertex"
    assert row.model == "gemini-2.5-flash"
    assert row.prompt_version == "v1"
    assert row.tokens_in == 1200
    assert row.tokens_out == 300
    assert Decimal(str(row.cost_usd)) == Decimal("0.0021")
    assert row.error_message is None

    # 1 コールで音声と MIME がそのまま渡り、プロンプトに文脈が載る。
    assert len(fake.calls) == 1
    audio_bytes, mime, prompt = fake.calls[0]
    assert audio_bytes == b"fake-audio-bytes"
    assert mime == "audio/webm"
    assert patient.name in prompt
    assert "録音 花子" in prompt
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
async def test_timeout_is_retried_once_then_succeeds(db, seeded) -> None:
    row, _user, _patient, _audio = seeded
    fake = _install(_FakeVoiceClient(VoiceAiError("timeout", "deadline"), _result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.calls) == 2
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
    _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "unlinked"
    assert row.summary_text is not None
    assert row.transcript is not None
    await db.rollback()


# ---------------------------------------------------------------------------
# 失敗系


@pytest.mark.asyncio
async def test_parse_error_fails_without_retry(db, seeded) -> None:
    row, user, _patient, _audio = seeded
    fake = _install(_FakeVoiceClient(VoiceAiError("parse", "model output was not JSON")))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.calls) == 1  # parse は再実行しない
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
async def test_missing_audio_file_fails(db, seeded) -> None:
    row, _user, _patient, audio = seeded
    Path(audio).unlink()
    fake = _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_message == jobs.AUDIO_MISSING_MESSAGE
    assert row.error_kind == jobs.ERROR_KIND_AUDIO_MISSING
    assert fake.calls == []
    await db.rollback()


@pytest.mark.asyncio
async def test_too_large_audio_fails(db, seeded, monkeypatch) -> None:
    """inline 上限超過は AI を呼ばずに ``too_large`` で failed."""
    row, _user, _patient, _audio = seeded
    monkeypatch.setattr(jobs, "MAX_INLINE_AUDIO_BYTES", 4)
    fake = _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed"
    assert row.error_message == jobs.TOO_LARGE_MESSAGE
    assert row.error_kind == jobs.ERROR_KIND_TOO_LARGE
    assert fake.calls == []
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
        _FakeVoiceClient(VoiceAiError("http", "rate limited", status_code=429), _result())
    )

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert len(fake.calls) == 2
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
    fake = _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "uploaded"
    assert fake.calls == []
    assert row.transcript is None
    await db.rollback()


@pytest.mark.asyncio
async def test_deleted_or_transcribing_rows_are_skipped(db, seeded) -> None:
    """削除済み / 既に処理中の行は起動しない (二重起動の後勝ちを防ぐ)."""
    row, _user, _patient, _audio = seeded
    row.status = "transcribing"
    await db.commit()
    fake = _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.status == "transcribing"
    assert fake.calls == []

    row.status = "uploaded"
    row.deleted_at = datetime.now(UTC)
    await db.commit()
    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.status == "uploaded"
    assert fake.calls == []
    await db.rollback()
