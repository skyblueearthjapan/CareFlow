"""OfficeFeatureFlag ORM model (Phase G-21).

拠点単位の feature flag (canary 3 phase 用).

* (office_id, feature_key) UNIQUE.
* feature_key で索引 (例: ``'g21_new_algorithm'``).
* ``enabled_at`` が NULL の場合は "未有効化" として扱う運用.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OfficeFeatureFlag(Base):
    """拠点単位の feature flag (office_feature_flags テーブル)."""

    __tablename__ = "office_feature_flags"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    enabled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("office_id", "feature_key", name="uq_office_feature_flags"),
        Index("ix_off_flag_key", "feature_key"),
    )
