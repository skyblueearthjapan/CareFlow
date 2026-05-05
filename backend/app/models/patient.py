"""Patient (利用者) and patient<->office many-to-many."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kana: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)  # male/female/unknown
    age: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    insurance: Mapped[str | None] = mapped_column(String(16), nullable=True)  # medical/care

    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lat: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    lng: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)

    primary_office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="SET NULL"),
        nullable=True,
    )

    required_staff_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    sex_restriction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ng_time_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    ng_time_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    weekly_pattern: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    special_week: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # W3-A additions
    area: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ng_staff_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=True, default=list
    )
    preferred_staff_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=True, default=list
    )
    specified_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    continuous_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    allowed_offices: Mapped[list["PatientAllowedOffice"]] = relationship(
        "PatientAllowedOffice",
        back_populates="patient",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_patients_status_office", "status", "primary_office_id"),
        Index("ix_patients_kana", "kana"),
    )


class PatientAllowedOffice(Base):
    __tablename__ = "patient_allowed_offices"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        primary_key=True,
    )

    patient: Mapped["Patient"] = relationship("Patient", back_populates="allowed_offices")
