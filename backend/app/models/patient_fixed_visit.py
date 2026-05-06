"""PatientFixedVisit ORM model (W9-BE1).

週間訪問パターン (固定枠) を表す行単位テーブル。
スケジュール確定後に book-back され、翌週以降は固定枠から visits を自動生成する。

設計上の制約:
  - mode: 'normal' (通常週) / 'special' (特別週)
  - 同一 (patient_id, mode, weekday) は 1 行のみ (UNIQUE 制約)
  - 1 日複数訪問なし; 2 名体制は patients.required_staff_count で対応
  - 指名スタッフなし (ローテ前提)
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PatientFixedVisit(Base):
    """患者固定訪問パターン (1 行 = 1 曜日 × 1 mode)."""

    __tablename__ = "patient_fixed_visits"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # 'normal' or 'special'
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Mon … 6=Sun
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

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
        UniqueConstraint(
            "patient_id",
            "mode",
            "weekday",
            name="uq_pfv_patient_mode_weekday",
        ),
        CheckConstraint(
            "mode IN ('normal','special')",
            name="ck_pfv_mode",
        ),
        CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_pfv_weekday",
        ),
        CheckConstraint(
            "duration_min > 0 AND duration_min <= 480",
            name="ck_pfv_duration",
        ),
    )
