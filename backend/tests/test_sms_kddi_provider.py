"""KDDI Message Cast の窓口の試験 (外へは通信しない・httpx.MockTransport).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §7-2。
接続の値はすべて偽物。番号は見本 (09000000001)。
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.sms import provider as kddi

FAKE = SimpleNamespace(
    sms_provider="kddi",
    sms_kddi_host="fake-host.invalid",
    sms_kddi_agg_id="fake-agg",
    sms_kddi_bot_id="fake-bot",
    sms_kddi_sms_number="0000000000",
    sms_kddi_basic_id="fake-id",
    sms_kddi_basic_password="fake-pass",
)


@pytest.fixture
def transport():
    """呼ばれた要求を控え、決めた応答を返す MockTransport を差し込む."""
    state = {"requests": [], "response": httpx.Response(202, json={})}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        resp = state["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    kddi.set_test_transport(httpx.MockTransport(handler))
    try:
        yield state
    finally:
        kddi.set_test_transport(None)


def test_encode_matches_vendor_sample() -> None:
    assert (
        kddi.encode_sms_text("これはSMSメッセージです。")
        == "MFMwjDBvAFMATQBTMOEwwzC7MPwwuDBnMFkwAg=="
    )


def test_user_contact() -> None:
    assert kddi.to_user_contact("09000000001") == "+819000000001"
    with pytest.raises(ValueError):
        kddi.to_user_contact("9000000001")


def test_get_provider_default_is_mock() -> None:
    assert kddi.get_provider(SimpleNamespace(sms_provider="mock")).name == "mock"
    assert kddi.get_provider(SimpleNamespace(sms_provider="")).name == "mock"
    assert kddi.get_provider(FAKE).name == "kddi"


@pytest.mark.asyncio
async def test_send_builds_exact_request(transport) -> None:
    transport["response"] = httpx.Response(
        202,
        json={"smsmessage": {"msgId": "fake-msg-1", "orderId": "fake-msg-1", "status": "pending"}},
        headers={"X-Plusmsg-TransactionId": "fake-tx-1"},
    )
    result = await kddi.KddiProvider(FAKE).send("09000000001", "見本の本文")

    assert result.outcome == "accepted"
    assert result.msg_id == "fake-msg-1"
    assert result.transaction_id == "fake-tx-1"
    assert result.status_raw == "pending"

    (req,) = transport["requests"]
    assert req.method == "POST"
    assert str(req.url) == (
        "https://fake-host.invalid/fake-agg/v1/fallback/rcs/bot/v1/fake-bot/messages"
    )
    expected_basic = base64.b64encode(b"fake-id:fake-pass").decode()
    assert req.headers["Authorization"] == f"Basic {expected_basic}"
    assert req.headers["X-Fallback"] == "2"
    for name in (
        "X-Plusmsg-mkn-Authorization",
        "X-Plusmsg-gmp-Authorization",
        "X-Plusmsg-tmt-Authorization",
    ):
        assert req.headers[name] == "Bearer smsonly"
    assert req.headers["Content-Type"].startswith("application/json")
    assert json.loads(req.content) == {
        "message": {"messageContact": {"userContact": "+819000000001"}},
        "smsmessage": {
            "smsNumber": "0000000000",
            "smsText": "見本の本文",
            "smsTextEnc": kddi.encode_sms_text("見本の本文"),
        },
        "dst": "0",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 401, 403, 404])
async def test_4xx_is_permanent_failure(transport, code) -> None:
    transport["response"] = httpx.Response(
        code,
        json={
            "type": f"{code} Error",
            "title": "[MGW1000028] 見本のエラー",
            "detail": {"messages": [{"message": "x", "message_code": "MGW1000028"}]},
        },
        headers={"X-Plusmsg-TransactionId": "fake-tx-2"},
    )
    result = await kddi.KddiProvider(FAKE).send("09000000001", "本文")
    assert result.outcome == "failed"
    assert result.error_code == "MGW1000028"
    assert result.error_message == "[MGW1000028] 見本のエラー"
    assert result.transaction_id == "fake-tx-2"
    assert result.needs_human is False


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [502, 503, 504])
async def test_502_503_504_are_retryable(transport, code) -> None:
    transport["response"] = httpx.Response(code, text="<html>Bad Gateway</html>")
    result = await kddi.KddiProvider(FAKE).send("09000000001", "本文")
    assert result.outcome == "retry"
    assert result.error_code == f"http_{code}"


@pytest.mark.asyncio
async def test_500_fails_and_needs_human(transport) -> None:
    transport["response"] = httpx.Response(500, json={"title": "見本"})
    result = await kddi.KddiProvider(FAKE).send("09000000001", "本文")
    assert result.outcome == "failed"
    assert result.needs_human is True
    assert result.error_code == "http_500"


@pytest.mark.asyncio
async def test_connect_error_retry_and_timeout_unknown(transport) -> None:
    transport["response"] = httpx.ConnectError("no route")
    assert (await kddi.KddiProvider(FAKE).send("09000000001", "本文")).outcome == "retry"
    transport["response"] = httpx.ReadTimeout("slow")
    result = await kddi.KddiProvider(FAKE).send("09000000001", "本文")
    # 送ったあとで途切れたかもしれないので、送り直さない。
    assert result.outcome == "unknown" and result.needs_human is True


@pytest.mark.asyncio
async def test_202_without_msg_id_is_unknown(transport) -> None:
    transport["response"] = httpx.Response(202, json={"smsmessage": {}})
    assert (await kddi.KddiProvider(FAKE).send("09000000001", "本文")).outcome == "unknown"


@pytest.mark.asyncio
async def test_not_configured_does_not_call(transport) -> None:
    settings = SimpleNamespace(**{**vars(FAKE), "sms_kddi_basic_password": ""})
    result = await kddi.KddiProvider(settings).send("09000000001", "本文")
    assert result.outcome == "failed" and result.error_code == "config"
    assert transport["requests"] == []


@pytest.mark.asyncio
async def test_get_status_parses_response(transport) -> None:
    transport["response"] = httpx.Response(
        200,
        json={
            "smsmessage": {
                "orderId": "fake-msg-1",
                "status": "delivered",
                "msgId": "fake-msg-1",
                "reason": "DELIVRD",
                "splitNum": "2",
                "timestamp": "2026-11-25T01:00:00.000Z",
                "Destination": "softbank",
            },
            "Destination": "softbank",
        },
        headers={"X-Plusmsg-TransactionId": "fake-tx-3"},
    )
    st = await kddi.KddiProvider(FAKE).get_status("fake-msg-1")
    assert st.status == "delivered" and st.status_raw == "delivered"
    assert st.split_num == 2 and st.carrier == "softbank" and st.reason == "DELIVRD"
    assert st.timestamp == "2026-11-25T01:00:00.000Z" and st.transaction_id == "fake-tx-3"
    (req,) = transport["requests"]
    assert req.method == "GET"
    assert str(req.url).endswith("/fake-bot/messages/fake-msg-1/status")
    assert req.headers["X-Fallback"] == "2"


@pytest.mark.asyncio
async def test_get_status_unknown_value_is_kept(transport) -> None:
    transport["response"] = httpx.Response(
        200, json={"smsmessage": {"status": "partial", "msgId": "m"}}
    )
    st = await kddi.KddiProvider(FAKE).get_status("m")
    assert st.status == "unknown" and st.status_raw == "partial" and st.split_num is None


@pytest.mark.asyncio
async def test_get_status_errors(transport) -> None:
    transport["response"] = httpx.Response(503)
    with pytest.raises(kddi.SmsProviderError) as exc:
        await kddi.KddiProvider(FAKE).get_status("m")
    assert exc.value.kind == "retry" and exc.value.http_status == 503
    transport["response"] = httpx.Response(404)
    with pytest.raises(kddi.SmsProviderError) as exc:
        await kddi.KddiProvider(FAKE).get_status("m")
    assert exc.value.kind == "http"


@pytest.mark.parametrize(
    ("raw", "mapped"),
    [
        ("pending", "accepted"),
        ("sent", "accepted"),
        ("delivered", "delivered"),
        ("failed", "failed"),
        ("retry", "unknown"),
        (None, "unknown"),
    ],
)
def test_map_status(raw, mapped) -> None:
    assert kddi.map_status(raw) == mapped
