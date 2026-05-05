"""Tests for /api/v1/ai/interpret + /api/v1/ai/logs (D4 Phase E / Wave 4-B + W2-BE6).

The Gemini SDK is monkeypatched at the GeminiClient layer so these tests
never touch the network and pass without `google-generativeai` installed.

W2-BE6 追記:
- 各 v2 公式 context_type (10 種) のハッピーパス
- 旧 `event_create` / `override_create` 互換（内部で `staff_event` / `staff_off`）
- `out_of_scope` 検出時のレスポンス
- `missing_fields` 検出時のレスポンス
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
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _stub_invoke(
    payload: dict[str, Any] | str, *, prompt_tokens: int = 50, completion_tokens: int = 30
):
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
async def test_interpret_general_returns_actions_and_persists_log(client, db, monkeypatch) -> None:
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
    assert body["model"] == "gemini-2.5-flash"
    assert "log_id" in body

    # Persistence: exactly one row, and `_meta` carries context_type / cost.
    rows = (await db.scalars(select(AiInterpretLog))).all()
    assert len(rows) == 1
    meta = rows[0].response["_meta"]
    assert meta["context_type"] == "general"
    assert meta["confidence"] == pytest.approx(0.92)
    assert meta["cost_usd"] >= 0.0


@pytest.mark.asyncio
async def test_interpret_legacy_event_create_compat(client, db, monkeypatch) -> None:
    """W2-BE6 互換性: 旧 `event_create` 入力 → 内部で `staff_event` に正規化."""
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
    body = res.json()
    # 旧名は受け付けつつ、レスポンスでは v2 公式名 `staff_event` に正規化される
    assert body["context_type"] == "staff_event"


@pytest.mark.asyncio
async def test_interpret_legacy_override_create_compat(client, db, monkeypatch) -> None:
    """W2-BE6 互換性: 旧 `override_create` 入力 → 内部で `staff_off` に正規化."""
    manager = await _make_user(db, "ai-ov@example.com", "manager")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_off",
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
    body = res.json()
    assert body["context_type"] == "staff_off"


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
# W2-BE6: v2 で追加された context_type 6 種のハッピーパス


@pytest.mark.asyncio
async def test_interpret_staff_create_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規: スタッフ新規登録の context_type."""
    admin = await _make_user(db, "ai-staff-create@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_create",
                        "confidence": 0.78,
                        "fields": {
                            "name": "佐藤 健",
                            "role": "nurse",
                            "office_id": "inage",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "新規スタッフで佐藤健さん、看護師、稲毛拠点",
            "context_type": "staff_create",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["context_type"] == "staff_create"
    assert body["interpreted"]["actions"][0]["action_type"] == "staff_create"


@pytest.mark.asyncio
async def test_interpret_staff_event_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規（公式名）: `staff_event` を直接受け付ける."""
    manager = await _make_user(db, "ai-staff-event@example.com", "manager")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_event",
                        "confidence": 0.91,
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
        headers=_bearer(manager),
        json={"prompt": "火曜午後 管理者会議", "context_type": "staff_event"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["context_type"] == "staff_event"


@pytest.mark.asyncio
async def test_interpret_staff_off_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規（公式名）: `staff_off` を直接受け付ける."""
    manager = await _make_user(db, "ai-staff-off@example.com", "manager")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_off",
                        "confidence": 0.93,
                        "fields": {"staff_id": "S001", "weekday": "thu"},
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(manager),
        json={"prompt": "田中さん木曜休み", "context_type": "staff_off"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["context_type"] == "staff_off"


@pytest.mark.asyncio
async def test_interpret_staff_mentor_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規: メンター登録."""
    admin = await _make_user(db, "ai-mentor@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_mentor",
                        "confidence": 0.86,
                        "fields": {
                            "staff_id": "S010",
                            "mentor_staff_id": "S001",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "鈴木さんのメンターを山田さんに",
            "context_type": "staff_mentor",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["context_type"] == "staff_mentor"
    assert body["interpreted"]["actions"][0]["action_type"] == "staff_mentor"


@pytest.mark.asyncio
async def test_interpret_patient_cancel_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規: 訪問キャンセル."""
    staff = await _make_user(db, "ai-cancel@example.com", "staff")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_cancel",
                        "confidence": 0.94,
                        "fields": {
                            "patient_id": "P0001",
                            "visit_date": "2026-05-07",
                            "reason": "発熱",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(staff),
        json={"prompt": "田中さん明日キャンセル", "context_type": "patient_cancel"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["context_type"] == "patient_cancel"


@pytest.mark.asyncio
async def test_interpret_patient_reschedule_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規: 訪問日時変更（scope 含む）."""
    admin = await _make_user(db, "ai-reschedule@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_reschedule",
                        "confidence": 0.83,
                        "fields": {
                            "patient_id": "P0002",
                            "scope": "one_time",
                            "visit_date": "2026-05-07",
                            "new_time_start": "10:00",
                            "new_time_end": "11:00",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "鈴木さん明日10時に1時間追加",
            "context_type": "patient_reschedule",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["context_type"] == "patient_reschedule"
    assert body["interpreted"]["actions"][0]["fields"]["scope"] == "one_time"


@pytest.mark.asyncio
async def test_interpret_patient_special_week_strategy(client, db, monkeypatch) -> None:
    """W2-BE6 新規: 特別訪問週間（on/off は payload で区別）."""
    admin = await _make_user(db, "ai-special@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_special_week",
                        "confidence": 0.9,
                        "fields": {
                            "patient_id": "P0003",
                            "mode": "on",
                            "iso_week": "2026-W19",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "佐藤さん来週特別週オン",
            "context_type": "patient_special_week",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["context_type"] == "patient_special_week"
    # on / off は payload 側で表現される（context_type は同一）
    assert body["interpreted"]["actions"][0]["fields"]["mode"] == "on"


# ---------------------------------------------------------------------------
# W2-BE6: out_of_scope アクション


@pytest.mark.asyncio
async def test_interpret_out_of_scope_returns_reason(client, db, monkeypatch) -> None:
    """範囲外発話: AI が `out_of_scope` を返す → 200 で reason / suggested_path を含む."""
    admin = await _make_user(db, "ai-oos@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "out_of_scope",
                        "confidence": 0.95,
                        "fields": {
                            "reason": "給与の変更は AI では対応していません",
                            "suggested_path": "別システムで対応",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={
            "prompt": "田中さんの給与を 30 万円に変更",
            "context_type": "general",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    action = body["interpreted"]["actions"][0]
    assert action["action_type"] == "out_of_scope"
    assert "給与" in action["fields"]["reason"]
    assert action["fields"]["suggested_path"] == "別システムで対応"


@pytest.mark.asyncio
async def test_interpret_accepts_out_of_scope_context_type(client, db, monkeypatch) -> None:
    """`context_type='out_of_scope'` を直接送ってもエラーにならない."""
    admin = await _make_user(db, "ai-oos-ctx@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "out_of_scope",
                        "confidence": 0.99,
                        "fields": {
                            "reason": "範囲外",
                            "suggested_path": "Web フォームから",
                        },
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "拠点を追加したい", "context_type": "out_of_scope"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["context_type"] == "out_of_scope"


# ---------------------------------------------------------------------------
# W2-BE6: missing_fields


@pytest.mark.asyncio
async def test_interpret_returns_missing_fields_when_action_lacks_required(
    client, db, monkeypatch
) -> None:
    """必須情報欠落時: 各 action の `missing_fields` をレスポンスに集約する."""
    admin = await _make_user(db, "ai-missing@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_create",
                        "confidence": 0.7,
                        "fields": {"name": "山田 花子"},
                        "missing_fields": ["address", "phone"],
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "新規患者 山田花子", "context_type": "patient_create"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["missing_fields"] == ["address", "phone"]


@pytest.mark.asyncio
async def test_interpret_missing_fields_absent_when_complete(client, db, monkeypatch) -> None:
    """欠落なし: `missing_fields` は null として返す（後方互換）."""
    admin = await _make_user(db, "ai-complete@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "staff_off",
                        "confidence": 0.95,
                        "fields": {"staff_id": "S001", "weekday": "thu"},
                    }
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "田中さん木曜休み", "context_type": "staff_off"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["missing_fields"] is None


@pytest.mark.asyncio
async def test_interpret_missing_fields_dedup_across_actions(client, db, monkeypatch) -> None:
    """複数 actions に分かれた missing_fields は重複排除して返す."""
    admin = await _make_user(db, "ai-missing-multi@example.com", "admin")
    monkeypatch.setattr(
        gc.GeminiClient,
        "_invoke",
        _stub_invoke(
            {
                "actions": [
                    {
                        "action_type": "patient_create",
                        "confidence": 0.7,
                        "fields": {},
                        "missing_fields": ["name", "address"],
                    },
                    {
                        "action_type": "patient_create",
                        "confidence": 0.6,
                        "fields": {},
                        "missing_fields": ["address", "phone"],
                    },
                ]
            }
        ),
    )
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "新規患者", "context_type": "patient_create"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 出現順を保ちつつ重複は排除
    assert body["missing_fields"] == ["name", "address", "phone"]


# ---------------------------------------------------------------------------
# W2-BE6: 未知 context_type の拒否


@pytest.mark.asyncio
async def test_interpret_unknown_context_type_returns_422(client, db) -> None:
    """v2 公式 10 種 + 旧 2 種 以外の context_type は Pydantic で 422."""
    admin = await _make_user(db, "ai-unknown-ctx@example.com", "admin")
    res = await client.post(
        "/api/v1/ai/interpret",
        headers=_bearer(admin),
        json={"prompt": "テスト", "context_type": "totally_made_up"},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Error paths


@pytest.mark.asyncio
async def test_interpret_invalid_json_returns_422_and_logs(client, db, monkeypatch) -> None:
    admin = await _make_user(db, "ai-bad@example.com", "admin")
    monkeypatch.setattr(gc.GeminiClient, "_invoke", _stub_invoke("えーっと あの えーっと"))
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
async def test_interpret_model_not_found_returns_503(client, db, monkeypatch) -> None:
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
async def test_interpret_generic_gemini_error_returns_502(client, db, monkeypatch) -> None:
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
    assert item["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_logs_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "ai-logs-staff@example.com", "staff")
    res = await client.get("/api/v1/ai/logs", headers=_bearer(staff))
    assert res.status_code == 403, res.text
