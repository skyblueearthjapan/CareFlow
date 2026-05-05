"""Tests for /api/v1/ai/interpret + /api/v1/ai/logs (D4 Phase E / Wave 4-B).

The Gemini SDK is monkeypatched at the GeminiClient layer so these tests
never touch the network and pass without `google-generativeai` installed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import AiInterpretLog, User
from app.services import gemini_client as gc

# ---------------------------------------------------------------------------
# Helpers


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email, password_hash=hash_password("does-not-matter"), role=role
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(
        subject=user.id, role=user.role, staff_id=user.staff_id
    )
    return {"Authorization": f"Bearer {token}"}


def _stub_invoke(payload: dict[str, Any] | str, *, prompt_tokens: int = 50, completion_tokens: int = 30):
    """Build an `_invoke` replacement that returns a canned response.

    `payload` may be the dict (will be JSON-encoded) or a raw string (used to
    test invalid-JSON handling).
    """
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload

    def _impl(self, prompt: str) -> tuple[str, int, int]:
        return text, prompt_tokens, completion_tokens

    return _impl


def _stub_invoke_raises(exc: Exception):
    def _impl(self, prompt: str) -> tuple[str, int, int]:
        raise exc

    return _impl


# ---------------------------------------------------------------------------
# Happy paths per context_type


@pytest.mark.asyncio
async def test_interpret_general_returns_actions_and_persists_log(
    client, db, monkeypatch
) -> None:
    admin = await _make_user(db, "ai-general@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_weekly_override",
                        "confidence": 0.92,
                        "fields": {
                            "staff_id": "S001",
                            "iso_week": "2026-W18",
                            "weekday": "thu",
                        },
                    }
                ]
            }
        ),
    )

    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "田中さん木曜休み", "context_type": "general"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confidence"] == pytest.approx(0.92)
    assert body["context_type"] == "general"
    assert body["interpreted"]["actions"][0]["action_type"] == "staff_weekly_override"
    assert body["model"] == "gemini-1.5-flash"
    assert "log_id" in body

    # Persistence: exactly one row, and `_meta` carries context_type / cost.
    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert len(rows) == 1
    meta = rows[0].response["_meta"]
    assert meta["context_type"] == "general"
    assert meta["confidence"] == pytest.approx(0.92)
    assert meta["cost_usd"] >= 0.0


@pytest.mark.asyncio
async def test_interpret_event_create_strategy(client, db, monkeypatch) -> None:
    staff = await _make_user(db, "ai-event@example.com", "staff")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_event",
                        "confidence": 0.88,
                        "fields": {
                            "staff_id": "S001",
                            "weekday": "tue",
                            "time_start": "13:00",
                            "time_end": "18:00",
                            "reason": "管理者会議",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(staff),
        json={"prompt": "火曜午後 管理者会議", "context_type": "event_create"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["context_type"] == "event_create"


@pytest.mark.asyncio
async def test_interpret_override_create_strategy(client, db, monkeypatch) -> None:
    manager = await _make_user(db, "ai-ov@example.com", "manager")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_weekly_override",
                        "confidence": 0.95,
                        "fields": {"staff_id": "S001", "weekday": "thu"},
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(manager),
        json={"prompt": "田中さん木曜終日休み", "context_type": "override_create"},
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_interpret_patient_create_strategy(client, db, monkeypatch) -> None:
    admin = await _make_user(db, "ai-patient@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_create",
                        "confidence": 0.81,
                        "fields": {"name": "山田 花子", "phone": "090-1234-5678"},
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "新規 山田花子 090-1234-5678",
            "context_type": "patient_create",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["interpreted"]["actions"][0]["action_type"] == "patient_create"


# ---------------------------------------------------------------------------
# Error paths


@pytest.mark.asyncio
async def test_interpret_invalid_json_returns_422_and_logs(
    client, db, monkeypatch
) -> None:
    admin = await _make_user(db, "ai-bad@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient, "_invoke", _stub_invoke("えーっと あの えーっと")
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "えーっと", "context_type": "general"},
    )
    assert res.status_code == 422, res.text
    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert len(rows) == 1
    assert rows[0].response["_meta"]["error"]


@pytest.mark.asyncio
async def test_interpret_rate_limit_returns_429(client, db, monkeypatch) -> None:
    admin = await _make_user(db, "ai-rate@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke_raises(gc.GeminiRateLimitError("quota exceeded")),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "鈴木さん明日キャンセル", "context_type": "general"},
    )
    assert res.status_code == 429, res.text
    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_interpret_unavailable_returns_503(client, db, monkeypatch) -> None:
    admin = await _make_user(db, "ai-unavail@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke_raises(gc.GeminiUnavailableError("no api key")),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "テスト", "context_type": "general"},
    )
    assert res.status_code == 503, res.text


@pytest.mark.asyncio
async def test_interpret_anonymous_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/ai/interpret",
        json={"prompt": "テスト", "context_type": "general"},
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# Logs endpoint RBAC


@pytest.mark.asyncio
async def test_logs_admin_returns_rows(client, db, monkeypatch) -> None:
    admin = await _make_user(db, "ai-logs-admin@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke({"actions": [{"action_type": "unknown", "confidence": 0.0, "fields": {}}]}),
    )
    # Seed one row by calling the endpoint.
    await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "テスト", "context_type": "general"},
    )

    res = await client.get("/api/v1/ai/logs", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    item = body["items"][0]
    assert item["context_type"] == "general"
    assert item["model"] == "gemini-1.5-flash"


@pytest.mark.asyncio
async def test_logs_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "ai-logs-staff@example.com", "staff")
    res = await client.get("/api/v1/ai/logs", headers=_bearer(staff))
    assert res.status_code == 403, res.text
