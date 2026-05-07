"""AcceptanceCalendar (受入カレンダー) — Wave 15 Phase 1 (W15-BE1).

拠点単位で曜日 × 時間帯ごとの受入可否を管理する。

## 概念

- 1 行 = (office_id, weekday, time_slot) の単位
- weekday: 0=Mon ... 6=Sun
- time_slot: TIME 型 (1 時間刻み運用想定 e.g. 10:00, 11:00, ..., 17:00)
- status: 'available' (○) / 'consult' (△) / 'unavailable' (×)
- (office_id, weekday, time_slot) UNIQUE
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AcceptanceCalendar(Base, TimestampMixin):
    """受入カレンダー (acceptance_calendar テーブル)."""

    __tablename__ = "acceptance_calendar"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="0=Mon ... 6=Sun",
    )
    time_slot: Mapped[time] = mapped_column(Time, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="'available' / 'consult' / 'unavailable'",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "office_id",
            "weekday",
            "time_slot",
            name="uq_acceptance_calendar_office_weekday_slot",
        ),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_acceptance_calendar_weekday",
        ),
        CheckConstraint(
            "status IN ('available','consult','unavailable')",
            name="ck_acceptance_calendar_status",
        ),
        Index("ix_acceptance_calendar_office_weekday", "office_id", "weekday"),
    )
