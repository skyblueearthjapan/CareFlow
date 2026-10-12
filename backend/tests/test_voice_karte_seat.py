"""カルテの下書きを決めた管理者の ChatGPT の席で (``app.services.voice.karte_seat`` + ``jobs``) のテスト.

設計: ``docs/plans/karte-via-seat-2026-10-13.md``。中継へは通信しない (``httpx.MockTransport``)。

* 旗 OFF → 中継を呼ばない・今までと同じ (provider=vertex・error_kind なし)。
* 旗 ON + 中継が成功 → 要約を差し替え、provider=codex-seat・model=席のモデル。文字起こし・費用は Gemini のまま。
* 旗 ON + 中継が失敗 (ok:false・timeout・401・つながらない・形が違う・合言葉なし) → Gemini の要約で
  summarized、error_kind=seat_<kind>。
* 中継へ送るもの: 文字起こし・名前の無い文脈・押した人の user id・合言葉。患者名・職員名は送らない。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

import httpx
import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.models import Patient, Staff
from app.models.office import Office
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.visit import Visit
from app.services.voice import jobs, karte_seat
from tests.test_voice_jobs import SUMMARY, _FakeVoiceClient, _install, _result  # noqa: F401

SEAT_SUMMARY = {
    "主訴・様子": ["腰の痛みは昨日より楽"],
    "バイタル": {"血圧": "132/78", "体温": "36.7", "脈拍": "", "SpO2": "98", "血糖": ""},
    "処置・ケア": ["褥瘡の包交"],
    "申し送り": ["［不明］ 薬の残数"],
    "次回": "来週火曜",
    "free": "",
}
TOKEN = "t" * 48


class Relay:
    """中継の代わり。受けた頼みを覚えて、決めた答えを返す。"""

    def __init__(self, reply=None, status: int = 200, exc: Exception | None = None) -> None:
        self.reply = (
            reply
            if reply is not None
            else {
                "ok": True,
                "summary": SEAT_SUMMARY,
                "model": "gpt-6.1-sol",
                "prompt_version": "seat-v1",
                "seat_user_id": "x",
                "mode": "designated",
                "secs": 3.1,
            }
        )
        self.status, self.exc = status, exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, json=self.reply)


@pytest.fixture
def relay_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AI_PROVIDER", "vertex")
    monkeypatch.setenv("VISIT_AUDIO_DIR", str(tmp_path / "visit_audio"))
    monkeypatch.setenv("AGENT_RELAY_SERVICE_URL", "http://carelink-agent:8092")
    monkeypatch.setenv("AGENT_RELAY_SERVICE_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield tmp_path
    from app.services.voice import vertex_client

    vertex_client.set_test_client(None)
    karte_seat.set_test_transport(None)
    get_settings.cache_clear()


def _use(relay: Relay) -> Relay:
    karte_seat.set_test_transport(httpx.MockTransport(relay.handler))
    return relay


@pytest_asyncio.fixture
async def rec(db, relay_env):
    from app.core.security import hash_password
    from app.models import User
    from app.models.visit_recording import VisitRecording

    office = Office(name="稲毛", short_label="稲", code="ksoff")
    staff = Staff(name="録音 花子", qualification="看護師")
    patient = Patient(code="KS-1", name="利用者 太郎", status="active", sex="female")
    db.add_all([office, staff, patient])
    await db.commit()
    user = User(
        email="karte-seat@example.com",
        password_hash=hash_password("x"),
        role="staff",
        staff_id=staff.id,
    )
    visit = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 10, 13),
        start_time=time(10, 30),
        end_time=time(11, 0),
        type="regular",
        status="planned",
    )
    db.add_all([user, visit])
    await db.commit()
    audio = relay_env / "rec.webm"
    audio.write_bytes(b"fake-audio-bytes")
    row = VisitRecording(
        patient_id=patient.id,
        visit_id=visit.id,
        staff_id=staff.id,
        office_id=office.id,
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
    return row, office, user


async def _flag(db, office, on: bool = True) -> None:
    db.add(
        OfficeFeatureFlag(
            office_id=office.id,
            feature_key="karte_via_seat",
            enabled_at=datetime.now(UTC) if on else None,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_flag_off_does_not_call_relay(db, rec) -> None:
    row, office, _ = rec
    await _flag(db, office, on=False)
    relay = _use(Relay())
    _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert relay.requests == []
    assert row.status == "summarized" and row.provider == "vertex" and row.error_kind is None
    assert row.summary["主訴・様子"] == SUMMARY["主訴・様子"] and row.model == "gemini-2.5-flash"
    await db.rollback()


@pytest.mark.asyncio
async def test_flag_on_uses_seat_draft(db, rec) -> None:
    row, office, user = rec
    await _flag(db, office)
    relay = _use(Relay())
    _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized" and row.error_kind is None
    assert (
        row.provider == "codex-seat"
        and row.model == "gpt-6.1-sol"
        and row.prompt_version == "seat-v1"
    )
    assert row.summary["申し送り"] == ["［不明］ 薬の残数"]
    assert row.summary_text.startswith("【主訴・様子】\n・腰の痛みは昨日より楽")
    assert "【次回】\n・来週火曜" in row.summary_text
    # 文字起こし・費用は Gemini のもの
    assert row.transcript.startswith("看護師:") and row.tokens_in == 1200

    (req,) = relay.requests
    assert str(req.url) == "http://carelink-agent:8092/svc/karte/draft"
    assert req.headers[karte_seat.TOKEN_HEADER] == TOKEN
    body = json.loads(req.content)
    assert body["recording_id"] == str(row.id) and body["presser_user_id"] == str(user.id)
    assert body["transcript"].startswith("看護師:")
    assert body["context"] == {
        "sex": "女性",
        "staff_role": "看護師",
        "visit_date": "2026-10-13",
        "visit_time": "10:30",
    }
    dumped = json.dumps(body, ensure_ascii=False)
    assert "利用者 太郎" not in dumped and "録音 花子" not in dumped
    await db.rollback()


@pytest.mark.parametrize(
    ("relay", "kind"),
    [
        (Relay(reply={"ok": False, "kind": "signed_out"}), "seat_signed_out"),
        (Relay(reply={"ok": False, "kind": "rate_limited"}), "seat_rate_limited"),
        (Relay(exc=httpx.ReadTimeout("slow")), "seat_timeout"),
        (Relay(exc=httpx.ConnectError("down")), "seat_unreachable"),
        (Relay(status=401, reply={"detail": "unauthorized"}), "seat_auth"),
        (Relay(status=404, reply={"detail": "Not Found"}), "seat_off"),
        (Relay(status=500, reply={}), "seat_http"),
        (Relay(reply={"ok": True, "summary": {"other": 1}}), "seat_parse"),
    ],
)
@pytest.mark.asyncio
async def test_seat_failure_falls_back_to_gemini(db, rec, relay, kind) -> None:
    row, office, _ = rec
    await _flag(db, office)
    _use(relay)
    _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized" and row.error_message is None
    assert (
        row.provider == "vertex" and row.model == "gemini-2.5-flash" and row.prompt_version == "v1"
    )
    assert row.summary["主訴・様子"] == SUMMARY["主訴・様子"]
    assert row.error_kind == kind
    await db.rollback()


@pytest.mark.asyncio
async def test_missing_token_falls_back_without_calling(db, rec, monkeypatch) -> None:
    row, office, _ = rec
    await _flag(db, office)
    monkeypatch.setenv("AGENT_RELAY_SERVICE_TOKEN", "")
    get_settings.cache_clear()
    relay = _use(Relay())
    _install(_FakeVoiceClient(_result()))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert relay.requests == []
    assert row.provider == "vertex" and row.error_kind == "seat_config"
    await db.rollback()


@pytest.mark.asyncio
async def test_retry_after_seat_success_clears_fallback_kind(db, rec) -> None:
    """席で失敗 → 再処理で成功したら error_kind は消える。"""
    row, office, _ = rec
    await _flag(db, office)
    _use(Relay(reply={"ok": False, "kind": "timeout"}))
    _install(_FakeVoiceClient(_result(), _result()))
    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.error_kind == "seat_timeout"

    _use(Relay())
    row.status = "uploaded"
    await db.commit()
    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.provider == "codex-seat" and row.error_kind is None
    await db.rollback()
