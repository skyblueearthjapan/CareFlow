"""Kaipoke fetch/push background job + per-row items.

Phase 5-1 Wave 2-B: schema + UI scaffolding only. The actual Playwright
execution loop (fetch/push) lands in Phase 5-2/5-3/5-4.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


# Allowed enum values (validated at the Pydantic layer; stored as strings).
KAIPOKE_JOB_TYPES = ("fetch", "push")
KAIPOKE_JOB_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
KAIPOKE_JOB_ITEM_STATUSES = ("pending", "running", "completed", "failed", "skipped")


class KaipokeJob(Base, TimestampMixin):
    __tablename__ = "kaipoke_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)

    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    items: Mapped[list["KaipokeJobItem"]] = relationship(
        "KaipokeJobItem",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="KaipokeJobItem.seq",
    )

    __table_args__ = (
        Index("ix_kaipoke_jobs_week_start", "week_start"),
        Index("ix_kaipoke_jobs_status", "status"),
        Index("ix_kaipoke_jobs_type_week", "job_type", "week_start"),
    )


class KaipokeJobItem(Base, TimestampMixin):
    __tablename__ = "kaipoke_job_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("kaipoke_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["KaipokeJob"] = relationship("KaipokeJob", back_populates="items")

    __table_args__ = (
        Index("ix_kaipoke_job_items_job_seq", "job_id", "seq"),
    )
