"""SMS の送信の窓口 (``mock`` / ``kddi``).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §7・§7-2 (KDDI Message Cast API 仕様書 ver3.1)。

* ``mock``: 記録するだけ (外へ出さない)。偽の受付の ID を返す。
* ``kddi``: SMS だけを送る (``X-Fallback: 2``・3 つのトークンは固定値 ``Bearer smsonly``)。
  本文は ``smsText`` (UTF-8) と ``smsTextEnc`` (UTF-16BE を Base64) の両方に入れる。
  ``smsTextEnc`` を誤ると KDDI 側で異常として検知され、アクセスを止められることがある。
* 受け付けると 202。``smsmessage.msgId`` と応答ヘッダー ``X-Plusmsg-TransactionId`` を控える。
* エラー: 400/401/403/404 などの 4xx は送り直さない (``failed``)。502/503/504 は時間をおいて
  送り直してよい (``retry``)。500 は送り直さず、人に知らせる (``failed`` + ``needs_human``)。
  送ったあとで応答が途切れたとき (受け付けられたか分からない) は ``unknown`` (二重送信を避ける)。

接続の値 (Basic 認証の ID・パスワードなど) は設定から読む。ログ・記録に値を書かない。
番号と本文もログに書かない。
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

SendOutcome = Literal["accepted", "failed", "retry", "unknown"]

#: 3 つのキャリアのトークン (SMS だけを送るときは固定値)。
SMS_ONLY_TOKEN = "Bearer smsonly"
TOKEN_HEADERS = (
    "X-Plusmsg-mkn-Authorization",
    "X-Plusmsg-gmp-Authorization",
    "X-Plusmsg-tmt-Authorization",
)
TRANSACTION_HEADER = "X-Plusmsg-TransactionId"
#: SMS だけ (0 は動作保証の外・1 は +メッセージだけ)。
FALLBACK_SMS_ONLY = "2"
RETRYABLE_STATUSES = frozenset({502, 503, 504})

#: KDDI の状態の値 → らく助の状態 (知らない値は unknown・値そのものは別に残す)。
STATUS_MAP: dict[str, str] = {
    "pending": "accepted",
    "sent": "accepted",
    "delivered": "delivered",
    "failed": "failed",
}

#: 試験用の差し込み口 (KDDI へ実際に通信しない)。
_TRANSPORT: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _TRANSPORT
    _TRANSPORT = transport


@dataclass
class SendResult:
    outcome: SendOutcome
    msg_id: str | None = None
    transaction_id: str | None = None
    status_raw: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    #: 500 など、送り直さずに人が確かめるもの。
    needs_human: bool = False


@dataclass
class StatusResult:
    status: str
    status_raw: str | None
    split_num: int | None
    carrier: str | None
    reason: str | None
    timestamp: str | None
    transaction_id: str | None


class SmsProviderError(RuntimeError):
    """照会ができなかった (``kind`` は短い英語の印)."""

    def __init__(self, kind: str, http_status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.http_status = http_status


class SmsProvider(Protocol):
    name: str

    async def send(self, phone: str, text: str) -> SendResult: ...

    async def get_status(self, msg_id: str) -> StatusResult: ...


def encode_sms_text(text: str) -> str:
    """``smsTextEnc``: 本文を UCS-2 (UTF-16BE・BOM なし) にして Base64."""
    return base64.b64encode(text.encode("utf-16-be")).decode("ascii")


def to_user_contact(phone: str) -> str:
    """``09000000001`` → ``+819000000001`` (先頭の 0 を除いて +81)."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits.startswith("0") or len(digits) < 10:
        raise ValueError("phone must be a domestic number starting with 0")
    return "+81" + digits[1:]


def map_status(raw: str | None) -> str:
    return STATUS_MAP.get((raw or "").strip().lower(), "unknown")


class MockProvider:
    """記録するだけ (外へ出さない)."""

    name = "mock"

    async def send(self, phone: str, text: str) -> SendResult:
        return SendResult(
            outcome="accepted", msg_id=f"mock-{uuid.uuid4().hex}", status_raw="pending"
        )

    async def get_status(self, msg_id: str) -> StatusResult:
        return StatusResult(
            status="delivered",
            status_raw="delivered",
            split_num=None,
            carrier=None,
            reason=None,
            timestamp=None,
            transaction_id=None,
        )


def _error_detail(resp: httpx.Response) -> tuple[str | None, str | None]:
    """エラー応答から (MGW の番号, 見出しの文) を取る. 取れなければ (None, None)."""
    try:
        data = resp.json()
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    title = data.get("title")
    code = None
    detail = data.get("detail")
    if isinstance(detail, dict):
        messages = detail.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            code = messages[0].get("message_code")
    return (str(code)[:32] if code else None), (str(title)[:255] if title else None)


class KddiProvider:
    """KDDI Message Cast (SMS だけ)."""

    name = "kddi"

    def __init__(self, settings: Any, *, timeout: float = 15.0) -> None:
        self._host = str(getattr(settings, "sms_kddi_host", "") or "").strip().rstrip("/")
        self._agg_id = str(getattr(settings, "sms_kddi_agg_id", "") or "").strip()
        self._bot_id = str(getattr(settings, "sms_kddi_bot_id", "") or "").strip()
        self._sms_number = str(getattr(settings, "sms_kddi_sms_number", "") or "").strip()
        self._basic_id = str(getattr(settings, "sms_kddi_basic_id", "") or "")
        self._basic_password = str(getattr(settings, "sms_kddi_basic_password", "") or "")
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return all(
            (
                self._host,
                self._agg_id,
                self._bot_id,
                self._sms_number,
                self._basic_id,
                self._basic_password,
            )
        )

    def messages_url(self) -> str:
        host = (
            self._host
            if self._host.startswith(("https://", "http://"))
            else f"https://{self._host}"
        )
        return f"{host}/{self._agg_id}/v1/fallback/rcs/bot/v1/{self._bot_id}/messages"

    def headers(self) -> dict[str, str]:
        basic = base64.b64encode(f"{self._basic_id}:{self._basic_password}".encode()).decode(
            "ascii"
        )
        out = {
            "Authorization": f"Basic {basic}",
            "X-Fallback": FALLBACK_SMS_ONLY,
            "Content-Type": "application/json",
        }
        for name in TOKEN_HEADERS:
            out[name] = SMS_ONLY_TOKEN
        return out

    def build_body(self, phone: str, text: str) -> dict[str, Any]:
        return {
            "message": {"messageContact": {"userContact": to_user_contact(phone)}},
            "smsmessage": {
                "smsNumber": self._sms_number,
                "smsText": text,
                "smsTextEnc": encode_sms_text(text),
            },
            "dst": "0",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=_TRANSPORT)

    async def send(self, phone: str, text: str) -> SendResult:
        if not self.configured:
            return SendResult(outcome="failed", error_code="config", needs_human=True)
        try:
            body = self.build_body(phone, text)
        except ValueError:
            return SendResult(outcome="failed", error_code="bad_phone")
        try:
            async with self._client() as client:
                resp = await client.post(self.messages_url(), json=body, headers=self.headers())
        except httpx.ConnectError:
            # つながらなかった (送っていない) → 送り直してよい。
            return SendResult(outcome="retry", error_code="connect")
        except httpx.TimeoutException:
            # 送ったあとで途切れたかもしれない → 受け付けられたか分からない (送り直さない)。
            return SendResult(outcome="unknown", error_code="timeout", needs_human=True)
        except httpx.RequestError:
            return SendResult(outcome="unknown", error_code="request_error", needs_human=True)

        transaction_id = resp.headers.get(TRANSACTION_HEADER)
        transaction_id = transaction_id[:128] if transaction_id else None
        if 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except ValueError:
                data = None
            sms = data.get("smsmessage") if isinstance(data, dict) else None
            msg_id = sms.get("msgId") if isinstance(sms, dict) else None
            if not msg_id:
                return SendResult(
                    outcome="unknown",
                    transaction_id=transaction_id,
                    error_code="no_msg_id",
                    needs_human=True,
                )
            raw = sms.get("status") if isinstance(sms, dict) else None
            return SendResult(
                outcome="accepted",
                msg_id=str(msg_id)[:64],
                transaction_id=transaction_id,
                status_raw=str(raw)[:32] if raw else None,
            )

        code, title = _error_detail(resp)
        error_code = code or f"http_{resp.status_code}"
        if resp.status_code in RETRYABLE_STATUSES:
            outcome: SendOutcome = "retry"
            needs_human = False
        else:
            # 4xx は設定か入力の誤り・500 は送り直さず人に知らせる。
            outcome = "failed"
            needs_human = resp.status_code >= 500
        return SendResult(
            outcome=outcome,
            transaction_id=transaction_id,
            error_code=error_code,
            error_message=title,
            needs_human=needs_human,
        )

    async def get_status(self, msg_id: str) -> StatusResult:
        """``GET …/messages/{msgId}/status`` (送ってから 31 日間)."""
        if not self.configured:
            raise SmsProviderError("config")
        url = f"{self.messages_url()}/{msg_id}/status"
        headers = self.headers()
        headers["Accept"] = "application/json"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            raise SmsProviderError("timeout") from None
        except httpx.RequestError:
            raise SmsProviderError("unreachable") from None
        if resp.status_code != 200:
            raise SmsProviderError(
                "retry" if resp.status_code in RETRYABLE_STATUSES else "http", resp.status_code
            )
        try:
            data = resp.json()
        except ValueError:
            raise SmsProviderError("parse") from None
        sms = data.get("smsmessage") if isinstance(data, dict) else None
        if not isinstance(sms, dict):
            raise SmsProviderError("parse")
        raw = sms.get("status")
        split = sms.get("splitNum")
        try:
            split_num = int(split) if split not in (None, "") else None
        except (TypeError, ValueError):
            split_num = None
        carrier = sms.get("Destination") or data.get("Destination")
        reason = sms.get("reason")
        transaction_id = resp.headers.get(TRANSACTION_HEADER)
        return StatusResult(
            status=map_status(raw),
            status_raw=str(raw)[:32] if raw else None,
            split_num=split_num,
            carrier=str(carrier)[:16] if carrier else None,
            reason=str(reason)[:255] if reason else None,
            timestamp=str(sms.get("timestamp")) if sms.get("timestamp") else None,
            transaction_id=transaction_id[:128] if transaction_id else None,
        )


def get_provider(settings: Any) -> SmsProvider:
    """設定 ``sms_provider`` から窓口を選ぶ (知らない値は mock = 外へ出さない)."""
    if str(getattr(settings, "sms_provider", "mock") or "mock").strip().lower() == "kddi":
        return KddiProvider(settings)
    return MockProvider()
