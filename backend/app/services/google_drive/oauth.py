"""Google OAuth (Web サーバーの流れ・PKCE) とリフレッシュトークンの暗号化.

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-3。
公式: https://developers.google.com/identity/protocols/oauth2/web-server

* 権限は ``drive.file`` だけ (らく助が作った・Picker で選ばれたファイルとフォルダだけに触れる)。
* ``access_type=offline`` と ``prompt=consent`` で毎回リフレッシュトークンを受け取る。
* state は保存せず、Fernet で暗号化した小さな束 (管理者の id・合言葉・PKCE の code_verifier)
  にする。有効は 10 分。合言葉は接続を始めたブラウザーの HttpOnly の cookie にも置き、
  戻りで両方が一致したときだけ受け付ける (他人のブラウザーで戻りを踏ませる CSRF を防ぐ)。
* リフレッシュトークンは Fernet で暗号化して保存する (手本 ``services/kaipoke/credentials.py``)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings
from app.services.google_drive.client import (
    UNEXPECTED_RESPONSE,
    GoogleDriveError,
    GoogleDriveNotConfiguredError,
    GoogleDriveReconnectRequiredError,
    http_client,
)

SCOPE_DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

#: state の有効 (秒)。許可の画面で迷っても足りる長さ。
STATE_TTL_SECONDS = 600


class InvalidOAuthStateError(Exception):
    """state が壊れている・古い・cookie と合わない。"""


def is_configured(settings: Settings | None = None) -> bool:
    """接続に要る値がそろっているか。

    本番では暗号化の鍵 ``GOOGLE_DRIVE_TOKEN_SECRET`` も必須 (jwt_secret から導出しない・L-6)。
    """
    s = settings or get_settings()
    if s.is_production and not (s.google_drive_token_secret or "").strip():
        return False
    return bool(
        s.google_drive_client_id.strip()
        and s.google_drive_client_secret.strip()
        and s.google_drive_redirect_uri.strip()
    )


def is_picker_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.google_picker_api_key.strip() and s.google_picker_app_id.strip())


def _fernet(purpose: str) -> Fernet:
    settings = get_settings()
    secret = settings.google_drive_token_secret or settings.jwt_secret
    # 用途ごとに接頭辞を変えて鍵を分ける (トークンの鍵で state を開けないように)。
    digest = hashlib.sha256(f"{purpose}:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(raw: str) -> str:
    return _fernet("google-drive-token").encrypt(raw.encode()).decode()


def decrypt_token(token: str) -> str | None:
    """復号。鍵が変わったなどで読めなければ None (= 接続し直し)。"""
    try:
        return _fernet("google-drive-token").decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


@dataclass(frozen=True)
class AuthorizationStart:
    url: str
    #: 接続を始めたブラウザーの cookie に置く合言葉。
    nonce: str


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization(user_id: uuid.UUID) -> AuthorizationStart:
    """許可の画面の URL と、cookie に置く合言葉を作る。"""
    settings = get_settings()
    if not is_configured(settings):
        raise GoogleDriveNotConfiguredError()
    nonce = secrets.token_urlsafe(24)
    # RFC 7636: 43〜128 文字の [A-Za-z0-9-._~]。token_urlsafe(48) は 64 文字。
    verifier = secrets.token_urlsafe(48)
    payload = json.dumps({"u": str(user_id), "n": nonce, "v": verifier}, separators=(",", ":"))
    state = _fernet("google-drive-oauth-state").encrypt(payload.encode()).decode()
    query = {
        "client_id": settings.google_drive_client_id.strip(),
        "redirect_uri": settings.google_drive_redirect_uri.strip(),
        "response_type": "code",
        "scope": SCOPE_DRIVE_FILE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return AuthorizationStart(url=f"{AUTH_URL}?{urlencode(query)}", nonce=nonce)


def verify_state(state: str, cookie_nonce: str | None) -> tuple[uuid.UUID, str]:
    """state を開き、cookie の合言葉と比べる。戻り値は (管理者の id, code_verifier)。"""
    try:
        raw = _fernet("google-drive-oauth-state").decrypt(state.encode(), ttl=STATE_TTL_SECONDS)
        data = json.loads(raw)
        user_id = uuid.UUID(data["u"])
        nonce = str(data["n"])
        verifier = str(data["v"])
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise InvalidOAuthStateError("state を確かめられませんでした") from exc
    if not cookie_nonce or not hmac.compare_digest(nonce, cookie_nonce):
        raise InvalidOAuthStateError("接続を始めたブラウザーと違います")
    return user_id, verifier


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    return str(body.get("error") or "") if isinstance(body, dict) else ""


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise GoogleDriveError(UNEXPECTED_RESPONSE) from exc
    if not isinstance(body, dict):
        raise GoogleDriveError(UNEXPECTED_RESPONSE)
    return body


@dataclass(frozen=True)
class ExchangedTokens:
    refresh_token: str
    access_token: str


async def exchange_code(code: str, code_verifier: str) -> ExchangedTokens:
    """許可のコードをトークンに換える。

    許可が ``drive.file`` だけでないとき (足りない・ほかの許可も付いている) は断る。
    受け取ったトークンは、``drive.file`` とほかの許可が付いていたときだけ取り消す (L-1)。
    """
    settings = get_settings()
    if not is_configured(settings):
        raise GoogleDriveNotConfiguredError()
    data = {
        "code": code,
        "client_id": settings.google_drive_client_id.strip(),
        "client_secret": settings.google_drive_client_secret.strip(),
        "redirect_uri": settings.google_drive_redirect_uri.strip(),
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    try:
        async with http_client() as client:
            resp = await client.post(TOKEN_URL, data=data)
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google に接続できませんでした") from exc
    if resp.status_code != 200:
        raise GoogleDriveError(
            f"Google から許可を受け取れませんでした（{_error_code(resp) or resp.status_code}）"
        )
    body = _json(resp)
    refresh_token = body.get("refresh_token")
    access_token = body.get("access_token")
    # 細かい許可の画面でドライブのチェックを外すと scope に入らない。
    granted = set(str(body.get("scope") or "").split())
    if granted != {SCOPE_DRIVE_FILE}:
        # 取り消すのは drive.file に加えてほかの許可も付いていたときだけ (広すぎる許可を
        # 残さない)。drive.file が無いときは取り消さない: Google の取り消しはアプリへの許可
        # ごと消すので、いまの接続 (前に許可した drive.file) まで使えなくなるため。
        if SCOPE_DRIVE_FILE in granted and (refresh_token or access_token):
            await revoke_token(str(refresh_token or access_token))
        if SCOPE_DRIVE_FILE not in granted:
            raise GoogleDriveError(
                "ドライブへの許可がありませんでした。許可の画面でドライブのチェックを入れてください"
            )
        raise GoogleDriveError(
            "ドライブ（drive.file）以外の許可が付いていたため、接続しませんでした"
        )
    if not refresh_token or not access_token:
        raise GoogleDriveError("Google からリフレッシュトークンを受け取れませんでした")
    return ExchangedTokens(refresh_token=str(refresh_token), access_token=str(access_token))


async def refresh_access_token(refresh_token: str) -> tuple[str, int]:
    """リフレッシュトークンから短い寿命のアクセストークンを得る。(token, 残り秒)。

    ``invalid_grant`` (取り消し・6 か月未使用・「テスト中」で 7 日・パスワード変更など) は
    ``GoogleDriveReconnectRequiredError``。status の更新と通知は呼び出し側 (connection.py) が行う。
    """
    settings = get_settings()
    if not is_configured(settings):
        raise GoogleDriveNotConfiguredError()
    data = {
        "client_id": settings.google_drive_client_id.strip(),
        "client_secret": settings.google_drive_client_secret.strip(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with http_client() as client:
            resp = await client.post(TOKEN_URL, data=data)
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google に接続できませんでした") from exc
    if resp.status_code != 200:
        if _error_code(resp) == "invalid_grant":
            raise GoogleDriveReconnectRequiredError()
        raise GoogleDriveError(
            f"Google のトークンを更新できませんでした（{_error_code(resp) or resp.status_code}）"
        )
    body = _json(resp)
    try:
        return str(body["access_token"]), int(body.get("expires_in") or 3600)
    except (KeyError, TypeError, ValueError) as exc:
        raise GoogleDriveError(UNEXPECTED_RESPONSE) from exc


async def revoke_token(token: str) -> bool:
    """Google 側の許可を取り消す。既に使えないトークンでも失敗として扱うだけ (例外にしない)。"""
    try:
        async with http_client() as client:
            resp = await client.post(
                REVOKE_URL,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError:
        return False
    return resp.status_code == 200


@dataclass(frozen=True)
class DriveAccount:
    email: str | None
    name: str | None


async def get_account(access_token: str) -> DriveAccount | None:
    """つないだ Google アカウント (``about?fields=user(emailAddress,displayName)``)。

    読めなければ None (画面に出さないだけで、接続は続ける)。
    """
    try:
        async with http_client() as client:
            resp = await client.get(
                ABOUT_URL,
                params={"fields": "user(emailAddress,displayName)"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    try:
        user = _json(resp).get("user") or {}
    except GoogleDriveError:
        return None
    if not isinstance(user, dict):
        return None
    return DriveAccount(email=user.get("emailAddress"), name=user.get("displayName"))
