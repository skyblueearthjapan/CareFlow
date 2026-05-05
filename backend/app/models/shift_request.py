"""Shift request (シフト希望提出) — staff自由入力 per target month.

Staff users submit a free-form text describing their availability for a
specific month; admin/manager reviews and flips status to ``reviewed``.
"""

from __future__ import annotations

import uuid
from datetime import date as _Date
from datetime import datetime
from typing import Literal

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ShiftRequestStatus = Literal["submitted", "reviewed"]


class ShiftRequest(Base, TimestampMixin):
    __tablename__ = "shift_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Stored as the first day of the target month (YYYY-MM-01) so we can
    # use a Date type with cheap ordering instead of String month-strings.
    target_month: Mapped[_Date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[ShiftRequestStatus] = mapped_column(
        String(16), nullable=False, default="submitted"
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_shift_requests_staff_month", "staff_id", "target_month"),
        Index("ix_shift_requests_status", "status"),
    )
