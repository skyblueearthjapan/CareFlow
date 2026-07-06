"""カイポケ ログイン情報の暗号化保存と取り出し (C-1・汎用化).

docs/plans/kaipoke-credentials-config-design.md §2:
  * 保管 = CareFlow DB (kaipoke_credentials・当面1行) にパスワードを Fernet 暗号化。
  * 鍵 = env ``KAIPOKE_CRED_SECRET``。未設定時は jwt_secret から導出 (HKDF代わりの
    sha256・接頭辞でドメイン分離)。鍵は .env にありDBバックアップとは分離される。
  * 受け渡し = ジョブ起動時に ``get_credentials_payload`` の dict を RPA payload へ
    同梱する (RPA 側は永続保存しない)。**KaipokeJob.params には絶対に入れない**
    (DB に平文が残るため)。
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.core.config import get_settings
from app.models.kaipoke_credential import KaipokeCredential

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


@lru_cache(maxsize=1)
def _fernet_for(secret: str) -> Fernet:
    # ドメイン分離の接頭辞つきで 32byte 鍵へ導出 → urlsafe base64 (Fernet 形式)。
    digest = hashlib.sha256(f"kaipoke-credentials:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> Fernet:
    settings = get_settings()
    # 本番は KAIPOKE_CRED_SECRET を必ず設定する (jwt_secret フォールバックは
    # 初期デプロイ簡便用。鍵は署名用と運用上分離しておくこと)。
    return _fernet_for(settings.kaipoke_cred_secret or settings.jwt_secret)


def encrypt_password(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_password(token: str) -> str | None:
    """復号。鍵違い (jwt_secret 変更等) は None (=再設定が必要)。"""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


async def read_credential(db: AsyncSession) -> KaipokeCredential | None:
    """設定行 (当面1行) を返す。"""
    return await db.scalar(
        select(KaipokeCredential)
        .order_by(KaipokeCredential.updated_at.desc(), KaipokeCredential.id.desc())
        .limit(1)
    )


async def upsert_credential(
    db: AsyncSession,
    *,
    corp_id: str,
    user_id: str,
    password: str,
    updated_by_user_id: uuid.UUID | None,
) -> KaipokeCredential:
    row = await read_credential(db)
    if row is None:
        row = KaipokeCredential(
            corp_id=corp_id,
            user_id=user_id,
            password_encrypted=encrypt_password(password),
            updated_by_user_id=updated_by_user_id,
        )
        db.add(row)
    else:
        row.corp_id = corp_id
        row.user_id = user_id
        row.password_encrypted = encrypt_password(password)
        row.updated_by_user_id = updated_by_user_id
    await db.flush()
    return row


async def get_credentials_payload(db: AsyncSession) -> dict[str, str] | None:
    """RPA へ渡す認証情報 dict。未設定/復号不能は None (RPA は env フォールバック)。"""
    row = await read_credential(db)
    if row is None:
        return None
    password = decrypt_password(row.password_encrypted)
    if password is None:
        return None
    return {"corp_id": row.corp_id, "user_id": row.user_id, "password": password}
