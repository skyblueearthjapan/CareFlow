"""GET/POST /api/v1/agent/transcribe (らく助エージェントへの声の入力・admin のみ・保存しない)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.api.v1 import agent_transcribe
from app.core.security import create_access_token, hash_password
from app.models import User
from app.services.voice import vertex_client
from app.services.voice.vertex_client import VoiceAiError

URL = "/api/v1/agent/transcribe"


class _StubClient:
    """Vertex の代わり。受け取った引数を控える (ネットワークに出ない)."""

    def __init__(self, text: str = "田中さんが明日お休みです。", error: VoiceAiError | None = None):
        self.text = text
        self.error = error
        self.calls: list[tuple[int, str, str]] = []

    async def transcribe_plain(self, audio_bytes: bytes, mime_type: str, prompt: str) -> str:
        self.calls.append((len(audio_bytes), mime_type, prompt))
        if self.error is not None:
            raise self.error
        return self.text

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset():
    agent_transcribe.reset_rate_limit()
    vertex_client.set_test_client(None)
    yield
    agent_transcribe.reset_rate_limit()
    vertex_client.set_test_client(None)


@pytest.fixture
def stub() -> _StubClient:
    s = _StubClient()
    vertex_client.set_test_client(s)  # type: ignore[arg-type]
    return s


async def _user(db, role: str) -> User:
    u = User(email=f"{role}@ex.com", password_hash=hash_password("x"), role=role)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    }


def _files(data: bytes = b"\x1aE\xdf\xa3fake-webm", mime: str = "audio/webm;codecs=opus"):
    return {"audio": ("voice.webm", data, mime)}


@pytest.mark.asyncio
async def test_requires_login(client, stub) -> None:
    res = await client.post(URL, files=_files())
    assert res.status_code == 401
    assert stub.calls == []


@pytest.mark.asyncio
async def test_staff_forbidden(client, db, stub) -> None:
    u = await _user(db, "staff")
    assert (await client.get(URL, headers=_bearer(u))).status_code == 403
    res = await client.post(URL, files=_files(), headers=_bearer(u))
    assert res.status_code == 403
    assert stub.calls == []


@pytest.mark.asyncio
async def test_status_available_with_client(client, db, stub) -> None:
    u = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert body["max_seconds"] == 60


@pytest.mark.asyncio
async def test_status_unavailable_when_not_configured(client, db, monkeypatch) -> None:
    # 既定の設定は VOICE_AI_PROVIDER=none → 使えない。POST も 503 で Vertex を呼ばない。
    u = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 200
    assert res.json()["available"] is False
    res = await client.post(URL, files=_files(), headers=_bearer(u))
    assert res.status_code == 503


def test_is_configured_needs_provider_project_and_key(tmp_path: Path) -> None:
    class S:
        voice_ai_provider = "vertex"
        vertex_project_id = "p"
        google_application_credentials = str(tmp_path / "missing.json")

    assert vertex_client.is_configured(S()) is False
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    S.google_application_credentials = str(key)
    assert vertex_client.is_configured(S()) is True
    S.voice_ai_provider = "none"
    assert vertex_client.is_configured(S()) is False


@pytest.mark.asyncio
async def test_transcribes_and_returns_text(client, db, stub) -> None:
    u = await _user(db, "admin")
    res = await client.post(URL, files=_files(), data={"duration_sec": "4.2"}, headers=_bearer(u))
    assert res.status_code == 200, res.text
    assert res.json() == {"text": "田中さんが明日お休みです。"}
    size, mime, prompt = stub.calls[0]
    assert size == len(b"\x1aE\xdf\xa3fake-webm")
    assert mime == "audio/webm"  # codecs は外して渡す
    assert "要約" in prompt and "らく助" in prompt


@pytest.mark.asyncio
async def test_ios_mp4_accepted(client, db, stub) -> None:
    u = await _user(db, "admin")
    res = await client.post(URL, files=_files(mime="audio/mp4"), headers=_bearer(u))
    assert res.status_code == 200
    assert stub.calls[0][1] == "audio/mp4"


@pytest.mark.asyncio
async def test_rejects_too_large(client, db, stub) -> None:
    u = await _user(db, "admin")
    big = b"\0" * (agent_transcribe.MAX_AUDIO_BYTES + 1)
    res = await client.post(URL, files=_files(big), headers=_bearer(u))
    assert res.status_code == 413
    assert stub.calls == []


@pytest.mark.asyncio
async def test_rejects_too_long_duration(client, db, stub) -> None:
    u = await _user(db, "admin")
    res = await client.post(URL, files=_files(), data={"duration_sec": "120"}, headers=_bearer(u))
    assert res.status_code == 422
    assert stub.calls == []


@pytest.mark.asyncio
async def test_rejects_non_audio_and_empty(client, db, stub) -> None:
    u = await _user(db, "admin")
    res = await client.post(URL, files=_files(mime="text/plain"), headers=_bearer(u))
    assert res.status_code == 415
    res = await client.post(URL, files=_files(b""), headers=_bearer(u))
    assert res.status_code == 422
    assert stub.calls == []


@pytest.mark.asyncio
async def test_rate_limit_per_user(client, db, stub) -> None:
    a = await _user(db, "admin")
    b = User(email="admin2@ex.com", password_hash=hash_password("x"), role="admin")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    for _ in range(agent_transcribe.RATE_LIMIT_COUNT):
        assert (await client.post(URL, files=_files(), headers=_bearer(a))).status_code == 200
    res = await client.post(URL, files=_files(), headers=_bearer(a))
    assert res.status_code == 429
    assert "10 分に 30 回" in res.json()["detail"]
    # 別の人は数えが別
    assert (await client.post(URL, files=_files(), headers=_bearer(b))).status_code == 200
    assert len(stub.calls) == agent_transcribe.RATE_LIMIT_COUNT + 1


@pytest.mark.asyncio
async def test_ai_failure_is_502_with_calm_message(client, db) -> None:
    vertex_client.set_test_client(_StubClient(error=VoiceAiError("http", status_code=500)))  # type: ignore[arg-type]
    u = await _user(db, "admin")
    res = await client.post(URL, files=_files(), headers=_bearer(u))
    assert res.status_code == 502
    assert "もう一度" in res.json()["detail"]


@pytest.mark.asyncio
async def test_does_not_store_audio(client, db, stub, tmp_path, monkeypatch) -> None:
    """音声をファイルにも DB の監査記録にも残さない。"""
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))
    u = await _user(db, "admin")
    marker = b"SECRET-AUDIO-BYTES"
    res = await client.post(URL, files=_files(marker), headers=_bearer(u))
    assert res.status_code == 200
    assert set(os.listdir(tmp_path)) == before
    # 監査ミドルウェアは本文を読まない (印だけ)。
    import asyncio

    await asyncio.sleep(0.05)
    rows = (await db.execute(select(AuditLog))).scalars().all()
    for row in rows:
        assert "SECRET-AUDIO" not in str(row.request_body or "")


def test_audit_middleware_skips_body_for_transcribe() -> None:
    from app.middleware.audit import _skips_body_buffering

    assert _skips_body_buffering("POST", URL, "multipart/form-data; boundary=x", "1000") == {
        "_multipart": True
    }
