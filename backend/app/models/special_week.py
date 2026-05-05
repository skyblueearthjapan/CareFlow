"""Special-week (一時的な訪問パターン変更) header + items.

W3-A: 利用者ごとに「特定週」だけ訪問パターンを差し替える / 追加するための
親子テーブル。SpecialWeek = ヘッダ (week_start..week_end), SpecialWeekItem
= 個別の訪問日明細。`apply_mode` が ADD なら通常パターンに上乗せ、
REPLACE なら通常パターンを丸ごと置換する。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Date,
    DateTime,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SpecialWeek(Base, TimestampMixin):
    __tablename__ = "special_weeks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    apply_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ADD"
    )  # ADD or REPLACE
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )  # draft / applied / cancelled
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    items: Mapped[list["SpecialWeekItem"]] = relationship(
        "SpecialWeekItem",
        back_populates="special_week",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "week_start",
            name="uq_special_weeks_patient_week_start",
        ),
    )


class SpecialWeekItem(Base, TimestampMixin):
    __tablename__ = "special_week_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    special_week_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("special_weeks.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Mon..6=Sun
    row_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="時間帯"
    )
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    preferred_earliest: Mapped[time | None] = mapped_column(Time, nullable=True)
    preferred_latest: Mapped[time | None] = mapped_column(Time, nullable=True)
    service_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    required_staff_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    individual_change_handling: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # merge / override / ignore
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    special_week: Mapped["SpecialWeek"] = relationship(
        "SpecialWeek", back_populates="items"
    )

    __table_args__ = (
        Index("ix_special_week_items_week_date", "special_week_id", "visit_date"),
        Index("ix_special_week_items_patient_date", "patient_id", "visit_date"),
    )
