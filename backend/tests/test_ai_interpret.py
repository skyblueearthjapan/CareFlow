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
    assert body["model"] == "gemini-2.0-flash"
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
async def test_interpret_model_not_found_returns_503(
    client, db, monkeypatch
) -> None:
    """A 404 from upstream (retired model id) must surface as 503, NOT 429.

    Reproduces the W4-B hotfix scenario where gemini-1.5-flash was removed
    from `v1beta` and the previous `_invoke` exception classifier was
    masking the 404 as a quota error, hiding the real misconfiguration.
    """
    admin = await _make_user(db, "ai-404@example.com", "admin")
    # Simulate the SDK error string verbatim so we exercise the 404
    # detection branch in `_invoke` if the test wants to drop one level
    # deeper. Here we raise the typed exception directly to keep the
    # test independent from the SDK message format.
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke_raises(
            gc.GeminiModelNotFound(
                "404 models/gemini-1.5-flash is not found for API version v1beta, "
                "or is not supported for generateContent"
            )
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "田中さん木曜休み", "context_type": "general"},
    )
    assert res.status_code == 503, res.text
    body = res.json()
    assert "GEMINI_MODEL" in body["detail"]

    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert len(rows) == 1
    meta = rows[0].response["_meta"]
    assert meta["error_type"] == "GeminiModelNotFound"
    assert "is not found" in meta["error"]


@pytest.mark.asyncio
async def test_interpret_generic_gemini_error_returns_502(
    client, db, monkeypatch
) -> None:
    """An unclassified upstream failure should map to 502, not 422.

    422 is reserved for unparseable JSON bodies; transport / 5xx-style
    failures must use 502 so monitoring can distinguish them.
    """
    admin = await _make_user(db, "ai-502@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke_raises(gc.GeminiError("upstream connection reset")),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "テスト", "context_type": "general"},
    )
    assert res.status_code == 502, res.text
    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert rows[0].response["_meta"]["error_type"] == "GeminiError"


@pytest.mark.asyncio
async def test_invoke_classifies_404_as_model_not_found(monkeypatch) -> None:
    """Unit-level: the SDK-string sniffer in `_invoke` must route 404
    messages to `GeminiModelNotFound` rather than `GeminiQuotaExceeded`."""

    class _FakeGenAI:
        def configure(self, *, api_key):  # noqa: D401
            return None

        class GenerativeModel:  # noqa: D401
            def __init__(self, model):
                self.model = model

            def generate_content(self, *args, **kwargs):
                raise RuntimeError(
                    "404 models/gemini-1.5-flash is not found for API "
                    "version v1beta, or is not supported for generateContent"
                )

    fake_module = _FakeGenAI()
    import sys

    monkeypatch.setitem(sys.modules, "google.generativeai", fake_module)

    client = gc.GeminiClient(api_key="test-key")
    with pytest.raises(gc.GeminiModelNotFound):
        client._invoke("hello")


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
    assert item["model"] == "gemini-2.0-flash"


@pytest.mark.asyncio
async def test_logs_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "ai-logs-staff@example.com", "staff")
    res = await client.get("/api/v1/ai/logs", headers=_bearer(staff))
    assert res.status_code == 403, res.text
