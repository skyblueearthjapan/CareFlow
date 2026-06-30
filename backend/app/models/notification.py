"""Notification — per-user inbox row surfaced by the mobile bell icon.

W4-D ships only the storage + the read-side endpoints. The producer side
(event listeners that fire when e.g. a visit is reassigned) lands in W6;
admins can manually create rows for testing via
``POST /api/v1/notifications/admin-create``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Free-form short identifier for the producer ("visit_assigned",
    # "shift_request_reviewed", ...). Frontend uses it for icon selection.
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 冪等キー (Phase 5-2 / migration 0045): 通知の発生源種別 + 紐づくドメイン行 ID。
    # チェックイン通知 producer は (reference_type, reference_id=visit_id) で
    # 「同一 visit の同種通知は 1 ユーザー 1 行」を担保する。NULL は重複制約対象外。
    reference_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        # 冪等化の安全網 (部分 UNIQUE)。reference_id IS NULL の行は対象外。
        Index(
            "uq_notifications_reference",
            "user_id",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text("reference_id IS NOT NULL"),
            sqlite_where=text("reference_id IS NOT NULL"),
        ),
    )
