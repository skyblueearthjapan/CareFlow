"""BusinessProfile ORM model (事業所の情報・mig 0089).

別の事業所へ提供する準備 (``docs/plans/multi-office-readiness-audit-2026-10-01.md`` #1)。
患者 QR カード (A5) に刷り込む「お問い合わせ先」(事業所名・電話・対応時間・対応日) と
ロゴを、コードではなくこの設定から取る。

* シングルトン: ``checkin_settings`` と同じく ``is_singleton = true`` の行を 1 行だけ
  持つ (部分 UNIQUE)。行が無ければ全項目が未設定として扱う。
* 各列は nullable。未設定の項目はカードに載せない (ロゴも出さない)。
* ロゴはファイルのアップロードは持たず、画像のパスまたは URL を選んで保存する
  (``/brand/...`` の同梱画像、または ``https://`` の URL)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BusinessProfile(Base):
    """事業所の情報 (business_profile テーブル, シングルトン 1 行)."""

    __tablename__ = "business_profile"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    is_singleton: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=func.true()
    )

    # 事業所名 (カード下部の署名。例「訪問看護ステーション ○○」)。
    station_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # お問い合わせの電話番号。
    contact_tel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 電話の対応時間 (例「9:00〜18:00」)。
    contact_hours: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # 電話の対応日 (休業の注記込み)。
    contact_days: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # ロゴ画像のパス (``/brand/...``) または URL (``https://...``)。未設定はロゴなし。
    logo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "uq_business_profile_singleton",
            "is_singleton",
            unique=True,
            postgresql_where=text("is_singleton = true"),
            sqlite_where=text("is_singleton = 1"),
        ),
    )
