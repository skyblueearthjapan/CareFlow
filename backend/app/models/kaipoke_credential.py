"""KaipokeCredential — カイポケ ログイン情報のアプリ内設定 (C-1・汎用化).

docs/plans/kaipoke-credentials-config-design.md:
従来 RPA 側の .env / コードに固定されていた 法人ID・ユーザーID・パスワード を
CareFlow DB に暗号化保存し、ジョブ起動時に payload で RPA へ渡す。
これによりロジックは全事業所共通のまま、ログイン情報だけが設定データになる。

- パスワードは Fernet で暗号化 (鍵は env・DBバックアップとは分離)。
- 当面1行運用 (1デプロイ=1法人) だが、複数行を許すスキーマにしておく。
- API はパスワードを絶対に返さない (設定済みか否か＋更新日時のみ)。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class KaipokeCredential(Base, TimestampMixin):
    __tablename__ = "kaipoke_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    corp_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
