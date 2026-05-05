"""Visit (訪問) — single row per scheduled visit."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.patient import Patient
    from app.models.staff import Staff


# Allowed values for `Visit.status`. The DB column is `String(16)` without a
# CHECK constraint at the DB level; tightening that is deferred to a separate
# migration. SQLAlchemy 2.0 supports `Mapped[Literal[...]]`, so the column
# below is typed as `Mapped[VisitStatus]` for static-type enforcement, while
# routers/services compare against the constants exported here instead of
# hard-coding strings.
VisitStatus = Literal["planned", "in_progress", "completed", "cancelled"]

# Status constants — a single source of truth for status string comparisons.
VISIT_STATUS_PLANNED: VisitStatus = "planned"
VISIT_STATUS_IN_PROGRESS: VisitStatus = "in_progress"
VISIT_STATUS_COMPLETED: VisitStatus = "completed"
VISIT_STATUS_CANCELLED: VisitStatus = "cancelled"


class Visit(Base, TimestampMixin):
    __tablename__ = "visits"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    primary_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    secondary_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    mentor_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships used for denormalized name fields in VisitRead.
    # `lazy="raise_on_sql"` on async sessions would be safer; we explicitly
    # use selectinload() in the routers to avoid lazy-load surprises.
    patient: Mapped["Patient"] = relationship("Patient", foreign_keys=[patient_id], lazy="noload")
    primary_staff: Mapped["Staff | None"] = relationship(
        "Staff", foreign_keys=[primary_staff_id], lazy="noload"
    )

    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[VisitStatus] = mapped_column(
        String(16), nullable=False, default=VISIT_STATUS_PLANNED
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    kaipoke_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_visits_date", "visit_date"),
        Index("ix_visits_patient_date", "patient_id", "visit_date"),
        Index("ix_visits_primary_date", "primary_staff_id", "visit_date"),
        Index("ix_visits_status", "status"),
    )
