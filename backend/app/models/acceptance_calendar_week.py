"""AcceptanceCalendarWeek (週別 受入上書き) — P4.

受け入れ枠マトリックスの「週別上書き」層。常設の ``acceptance_calendar``
(毎週共通) に対し、**特定週だけ**の手動上書きを保持する。

## 概念

- 1 行 = (office_id, iso_year, iso_week, weekday, time_slot) の単位
- weekday: 0=Mon ... 6=Sun
- time_slot: TIME 型 (1 時間刻み運用想定)
- status: 'available' (○) / 'consult' (△) / 'unavailable' (×)
- (office_id, iso_year, iso_week, weekday, time_slot) UNIQUE

## 実効値の優先順位 (受け入れ枠マトリックス)

    effective = 週別上書き(本テーブル) ?? 常設上書き(acceptance_calendar) ?? 自動算出
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AcceptanceCalendarWeek(Base, TimestampMixin):
    """週別 受入上書き (acceptance_calendar_week テーブル)."""

    __tablename__ = "acceptance_calendar_week"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        nullable=False,
    )
    iso_year: Mapped[int] = mapped_column(Integer, nullable=False, comment="ISO 8601 year")
    iso_week: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="ISO 8601 week (1-53)"
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="0=Mon ... 6=Sun")
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
            "iso_year",
            "iso_week",
            "weekday",
            "time_slot",
            name="uq_acceptance_calendar_week_key",
        ),
        CheckConstraint(
            "iso_week >= 1 AND iso_week <= 53",
            name="ck_acceptance_calendar_week_iso_week",
        ),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_acceptance_calendar_week_weekday",
        ),
        CheckConstraint(
            "status IN ('available','consult','unavailable')",
            name="ck_acceptance_calendar_week_status",
        ),
        Index(
            "ix_acceptance_calendar_week_office_week",
            "office_id",
            "iso_year",
            "iso_week",
        ),
    )
