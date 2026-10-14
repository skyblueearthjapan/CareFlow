"""RevokedQrToken — 再発行で失効した旧 QR トークンの記録 (Phase 5-1).

QR 再発行 (``POST /patients/{id}/qr/regenerate``) で patients.qr_token を新しい
値に置き換えるとき、旧トークンをこのテーブルに退避する。judge は未知トークンが
「ローテ済 (= revoked)」か「完全な未知」かをこの表で判別し、前者には 410 Gone を
返す (旧ステッカーで打刻したスタッフに「QR が更新された」と気づかせるため)。

* ``token`` は patients.qr_token と同じ値域 (``token_urlsafe(16)`` = String(32))。
  非 NULL の重複は禁止 (unique)。
* ``patient_id`` は監査証跡として **ON DELETE RESTRICT** (患者の物理削除を防ぐ。
  patients は soft delete 運用なので実害なし)。
* ``valid_until`` (mig 0096) は古い QR の猶予の期限。NULL = 即時に無効 (既存の行と
  理由なしの再発行)。``valid_until > now`` かつ患者が稼働中なら judge はその患者として
  通す。新しい QR が一度読めたら、猶予中の行は ``valid_until = now`` で打ち切る。
* ``reason`` (mig 0096) は再発行の理由 ``damaged`` / ``lost`` / ``misuse`` (NULL 可)。

SQLite 互換: ``patient.py`` / ``checkin_settings.py`` と同じ dialect 分岐方針。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedQrToken(Base):
    """失効した旧 QR トークン (revoked_qr_tokens テーブル)."""

    __tablename__ = "revoked_qr_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "patients.id",
            ondelete="RESTRICT",
            name="fk_revoked_qr_tokens_patient_id_patients",
        ),
        nullable=False,
        index=True,
    )
    # 旧トークン値 (token_urlsafe(16) = 最大 22 文字, 余裕を見て String(32))。
    token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # 古い QR の猶予の期限 (NULL = 即時に無効)。
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 再発行の理由 (damaged / lost / misuse)。
    reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
