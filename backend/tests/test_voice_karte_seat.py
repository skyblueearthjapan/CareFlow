"""カルテの下書きを決めた管理者の ChatGPT の席で (``app.services.voice.karte_seat`` + ``jobs``) のテスト.

設計: ``docs/plans/karte-via-seat-2026-10-13.md``・v2 ``docs/plans/visit-voice-record-v2-design-2026-10-27.md``
§3 P3。中継へは通信しない (``httpx.MockTransport``)。

* 旗 OFF → 中継を呼ばない・Gemini の v2 で書く (provider=vertex・error_kind なし)。
* 旗 ON + 中継が成功 → 席の下書き (v2 の本文＋事実)。provider=codex-seat・model=席のモデル。
  Gemini の ③ は呼ばない。文字起こし・費用は Gemini の ①② のもの。
* 中継が v1 (summary の 6 欄) で答えても受ける (前の中継)。
* 旗 ON + 中継が失敗 (ok:false・timeout・401・つながらない・形が違う・合言葉なし) → Gemini の v2 で
  summarized、error_kind=seat_<kind>。
* 中継へ送るもの: format=v2・**直した** 文字起こし ([m:ss] の行)・名前の無い文脈・押した人の user id・合言葉。
  患者名・職員名は送らない。
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
from tests.test_voice_jobs import _FakeVoiceClient, _install

GEMINI_BODY = "褥瘡の痛みの訴えあり [0:03]。"
SEAT_BODY = "腰の痛みは昨日より楽 [0:03]。\n\n- 血圧 132/78 [0:00]"
SEAT_FACTS = {
    "vitals": {"血圧": "132/78", "体温": "", "脈拍": "", "SpO2": "", "血糖": ""},
    "handover": ["薬の残数を確認"],
    "next": "来週火曜",
    "needs_check": [],
}

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
                "body": SEAT_BODY,
                "facts": SEAT_FACTS,
                "model": "gpt-6.1-sol",
                "prompt_version": "seat-v2",
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
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert relay.requests == []
    assert row.status == "summarized" and row.provider == "vertex" and row.error_kind is None
    assert row.summary["body"] == GEMINI_BODY and row.model == "gemini-2.5-flash"
    assert row.prompt_version == "v2" and len(fake.summary_prompts()) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_flag_on_uses_seat_draft(db, rec) -> None:
    row, office, user = rec
    await _flag(db, office)
    relay = _use(Relay())
    fake = _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized" and row.error_kind is None
    assert (
        row.provider == "codex-seat"
        and row.model == "gpt-6.1-sol"
        and row.prompt_version == "seat-v2"
    )
    assert row.summary == {"format": "v2", "body": SEAT_BODY, "facts": SEAT_FACTS}
    assert row.summary_text == SEAT_BODY
    # 席で書けたら Gemini の ③ は呼ばない。文字起こし・費用は Gemini の ①② のもの。
    assert fake.summary_prompts() == []
    assert row.transcript.startswith("看護師:") and row.tokens_in == 2000

    (req,) = relay.requests
    assert str(req.url) == "http://carelink-agent:8092/svc/karte/draft"
    assert req.headers[karte_seat.TOKEN_HEADER] == TOKEN
    body = json.loads(req.content)
    assert body["recording_id"] == str(row.id) and body["presser_user_id"] == str(user.id)
    assert body["format"] == "v2"
    # 直した文字起こし ([m:ss] の行) が渡る。
    assert (
        body["transcript"]
        == "[0:00] 看護師: 今日の体調はいかがですか。\n[0:03] 患者: 褥瘡が少し痛いです。"
    )
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
        (Relay(reply={"ok": True, "body": "  ", "facts": {}}), "seat_parse"),
    ],
)
@pytest.mark.asyncio
async def test_seat_failure_falls_back_to_gemini(db, rec, relay, kind) -> None:
    row, office, _ = rec
    await _flag(db, office)
    _use(relay)
    _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "summarized" and row.error_message is None
    assert (
        row.provider == "vertex" and row.model == "gemini-2.5-flash" and row.prompt_version == "v2"
    )
    assert row.summary["body"] == GEMINI_BODY
    assert row.error_kind == kind
    await db.rollback()


@pytest.mark.asyncio
async def test_missing_token_falls_back_without_calling(db, rec, monkeypatch) -> None:
    row, office, _ = rec
    await _flag(db, office)
    monkeypatch.setenv("AGENT_RELAY_SERVICE_TOKEN", "")
    get_settings.cache_clear()
    relay = _use(Relay())
    _install(_FakeVoiceClient())

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
    _install(_FakeVoiceClient())
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


@pytest.mark.asyncio
async def test_claude_seat_is_recorded_separately(db, rec) -> None:
    """中継が Claude の席で作ったら provider=claude-seat。AI を返さない (前の) 中継は codex-seat。"""
    row, office, _ = rec
    await _flag(db, office)
    _use(Relay(reply={**Relay().reply, "provider": "claude", "model": "claude-opus-5-5"}))
    _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert (
        row.provider == "claude-seat" and row.model == "claude-opus-5-5" and row.error_kind is None
    )

    _use(
        Relay(reply={**Relay().reply, "provider": "gemini"})
    )  # 知らない AI の名前は ChatGPT の席として扱う
    row.status = "uploaded"
    await db.commit()
    await jobs.run_transcribe_job(row.id)
    await db.refresh(row)
    assert row.provider == "codex-seat"
    await db.rollback()


@pytest.mark.asyncio
async def test_seat_v1_answer_is_still_accepted(db, rec) -> None:
    """v2 を知らない前の中継が v1 (summary の 6 欄) で答えても受ける。"""
    row, office, _ = rec
    await _flag(db, office)
    _use(
        Relay(
            reply={
                "ok": True,
                "summary": SEAT_SUMMARY,
                "model": "gpt-6.1-sol",
                "prompt_version": "seat-v1",
            }
        )
    )
    _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.provider == "codex-seat" and row.prompt_version == "seat-v1"
    assert row.summary["申し送り"] == ["［不明］ 薬の残数"]
    assert row.summary_text.startswith("【主訴・様子】\n・腰の痛みは昨日より楽")
    assert "【次回】\n・来週火曜" in row.summary_text
    await db.rollback()


@pytest.mark.asyncio
async def test_seat_body_with_facts_block_left_in_is_split(db, rec) -> None:
    """中継が事実のブロックを本文に残したまま返しても、backend で読み分ける。"""
    row, office, _ = rec
    await _flag(db, office)
    body = (
        "体温 36.5 [0:10]\n```json\n" + json.dumps({"next": "火曜"}, ensure_ascii=False) + "\n```"
    )
    _use(Relay(reply={"ok": True, "body": body, "model": "m", "prompt_version": "seat-v2"}))
    _install(_FakeVoiceClient())

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.summary["body"] == "体温 36.5 [0:10]" and row.summary["facts"]["next"] == "火曜"
    await db.rollback()


@pytest.mark.asyncio
async def test_seat_failure_then_gemini_failure_fails_the_recording(db, rec) -> None:
    """席がだめで Gemini の ③ も失敗 → failed (summary_<kind>)。"""
    row, office, _ = rec
    await _flag(db, office)
    _use(Relay(reply={"ok": False, "kind": "signed_out"}))
    from app.services.voice.vertex_client import VoiceAiError
    from tests.test_voice_jobs import FIXED_ANSWER, _reply

    def text(prompt: str):
        return _reply(FIXED_ANSWER) if "校正係" in prompt else VoiceAiError("auth", "x")

    _install(_FakeVoiceClient(text=text))

    await jobs.run_transcribe_job(row.id)

    await db.refresh(row)
    assert row.status == "failed" and row.error_kind == "summary_auth"
    await db.rollback()
