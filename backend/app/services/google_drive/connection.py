"""Google ドライブの接続 (``google_drive_connections``・1 行だけ) の読み書き.

* 接続できたら行を作る (あれば上書き)。切断は Google 側の許可を取り消してから行を消す。
* アクセストークンは期限の 60 秒前までプロセスの中に持つ (毎回の更新を省く・M-2)。
  接続・切断・``needs_reconnect`` で捨てる。リフレッシュトークンが変われば使わない。
* トークンが使えなくなったら ``status='needs_reconnect'`` にし、管理者全員へ通知 (ベル) する。
  **commit しない** — 呼び出し側のトランザクションに乗せる。例外
  ``GoogleDriveReconnectRequiredError`` を受けた呼び出し側は、ほかの処理 (月の確定など) を
  続けて commit してよい (勤務表は「未作成」で残す・§7-2 の 5)。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave import GoogleDriveConnection
from app.models.notification import Notification
from app.models.user import User
from app.services.google_drive.client import (
    GoogleDriveNotConnectedError,
    GoogleDriveReconnectRequiredError,
)
from app.services.google_drive.oauth import decrypt_token, encrypt_token, refresh_access_token

STATUS_CONNECTED: Final = "connected"
STATUS_NEEDS_RECONNECT: Final = "needs_reconnect"

NOTIFY_GOOGLE_DRIVE_RECONNECT: Final = "google_drive_reconnect"
NOTIFY_GOOGLE_DRIVE_CHANGED: Final = "google_drive_changed"

#: アクセストークンを期限のこの秒数前に捨てる。
TOKEN_CACHE_MARGIN_SECONDS: Final = 60


@dataclass(frozen=True)
class _CachedToken:
    #: どのリフレッシュトークン (暗号文) から得たか。変わったら使わない。
    source: str
    token: str
    #: time.monotonic() での捨てる時刻。
    valid_until: float


_cached_token: _CachedToken | None = None


def clear_token_cache() -> None:
    """持っているアクセストークンを捨てる (接続・切断・needs_reconnect で呼ぶ)。"""
    global _cached_token
    _cached_token = None


async def load_connection(db: AsyncSession) -> GoogleDriveConnection | None:
    return await db.scalar(
        select(GoogleDriveConnection).where(GoogleDriveConnection.is_singleton.is_(True))
    )


async def save_connection(
    db: AsyncSession, *, refresh_token: str, user_id: uuid.UUID
) -> GoogleDriveConnection:
    """接続を保存する (1 行だけ)。保存先フォルダは接続し直しても残す。"""
    clear_token_cache()
    row = await load_connection(db)
    if row is None:
        row = GoogleDriveConnection(
            is_singleton=True,
            refresh_token_encrypted=encrypt_token(refresh_token),
            connected_by=user_id,
            status=STATUS_CONNECTED,
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
            return row
        except IntegrityError:
            # 同時に 2 本の接続が戻ってきたときは、先にできた行を上書きする。
            existing = await load_connection(db)
            if existing is None:
                raise
            row = existing
    row.refresh_token_encrypted = encrypt_token(refresh_token)
    row.connected_by = user_id
    row.status = STATUS_CONNECTED
    row.last_error = None
    await db.flush()
    return row


async def _admin_users(db: AsyncSession) -> list[User]:
    return list(
        (
            await db.scalars(
                select(User).where(User.role.in_(("admin", "manager")), User.deleted_at.is_(None))
            )
        ).all()
    )


async def notify_connection_changed(db: AsyncSession, *, account: str, by: str) -> int:
    """接続先が変わったことを管理者全員のベルへ (1 回ごとに 1 通・commit しない・L-2)。"""
    ref = uuid.uuid4()
    users = await _admin_users(db)
    for u in users:
        db.add(
            Notification(
                user_id=u.id,
                type=NOTIFY_GOOGLE_DRIVE_CHANGED,
                title="Google ドライブの接続先が変わりました",
                body=(
                    f"勤務表を置く Google ドライブの接続先が「{account}」になりました"
                    f"（つないだ人: {by}）。心当たりがなければ、設定の「Google ドライブ」で"
                    "確かめてください。"
                ),
                reference_type=NOTIFY_GOOGLE_DRIVE_CHANGED,
                reference_id=ref,
            )
        )
    return len(users)


async def _notify_admins(db: AsyncSession, row: GoogleDriveConnection) -> int:
    """管理者全員のベルへ 1 通。同じ接続の通知は 1 人 1 通で、未読に戻して出し直す。"""
    title = "Google ドライブにもう一度接続してください"
    body = (
        "Google ドライブへの接続が使えなくなりました（許可の取り消し・長く使わなかったなど）。\n"
        "勤務表は「未作成」として残っています。設定の「Google ドライブ」からもう一度接続したあと、"
        "「作る」で作れます。"
    )
    users = await _admin_users(db)
    existing = {
        n.user_id: n
        for n in (
            await db.scalars(
                select(Notification).where(
                    Notification.reference_type == NOTIFY_GOOGLE_DRIVE_RECONNECT,
                    Notification.reference_id == row.id,
                )
            )
        ).all()
    }
    for u in users:
        current = existing.get(u.id)
        if current is None:
            db.add(
                Notification(
                    user_id=u.id,
                    type=NOTIFY_GOOGLE_DRIVE_RECONNECT,
                    title=title,
                    body=body,
                    reference_type=NOTIFY_GOOGLE_DRIVE_RECONNECT,
                    reference_id=row.id,
                )
            )
        else:
            current.title = title
            current.body = body
            current.read_at = None
    return len(users)


async def mark_needs_reconnect(db: AsyncSession, row: GoogleDriveConnection, error: str) -> None:
    """``needs_reconnect`` にする。接続中から変わったときだけ通知する (commit しない)。"""
    clear_token_cache()
    was_connected = row.status == STATUS_CONNECTED
    row.status = STATUS_NEEDS_RECONNECT
    row.last_error = error
    if was_connected:
        await _notify_admins(db, row)
    await db.flush()


async def get_access_token(db: AsyncSession) -> tuple[str, int, GoogleDriveConnection]:
    """接続からアクセストークンを得る。(token, 残り秒, 接続の行)。

    Raises:
        GoogleDriveNotConfiguredError: .env が未設定。
        GoogleDriveNotConnectedError: 接続の行が無い。
        GoogleDriveReconnectRequiredError: 接続し直しが要る (status は更新済み・未 commit)。
    """
    row = await load_connection(db)
    if row is None or row.refresh_token_encrypted is None:
        raise GoogleDriveNotConnectedError()
    if row.status == STATUS_NEEDS_RECONNECT:
        raise GoogleDriveReconnectRequiredError()
    global _cached_token
    cached = _cached_token
    now = time.monotonic()
    if (
        cached is not None
        and cached.source == row.refresh_token_encrypted
        and now < cached.valid_until
    ):
        return cached.token, int(cached.valid_until - now) + TOKEN_CACHE_MARGIN_SECONDS, row
    refresh_token = decrypt_token(row.refresh_token_encrypted)
    if refresh_token is None:
        await mark_needs_reconnect(db, row, "保存したトークンを読めませんでした（鍵が変わった）")
        raise GoogleDriveReconnectRequiredError()
    try:
        token, expires_in = await refresh_access_token(refresh_token)
    except GoogleDriveReconnectRequiredError:
        await mark_needs_reconnect(
            db, row, "Google が許可を受け付けませんでした（invalid_grant: 取り消し・期限切れ）"
        )
        raise
    if expires_in > TOKEN_CACHE_MARGIN_SECONDS:
        _cached_token = _CachedToken(
            source=row.refresh_token_encrypted,
            token=token,
            valid_until=now + expires_in - TOKEN_CACHE_MARGIN_SECONDS,
        )
    return token, expires_in, row


async def delete_connection(db: AsyncSession) -> GoogleDriveConnection | None:
    """接続の行を消し、消した行を返す (Google 側の取り消しは呼び出し側が先に行う)。"""
    clear_token_cache()
    row = await load_connection(db)
    if row is not None:
        await db.delete(row)
        await db.flush()
    return row
