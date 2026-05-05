"""Patient (利用者) and patient<->office many-to-many.

W1-BE1 (v2 patient master cleanup, §4.1):
  * Removed columns (drop in 0009_v2_patient_master_cleanup):
      age, ng_time_start, ng_time_end, specified_type, ng_staff_ids,
      preferred_staff_ids, continuous_request, required_staff_count, area
  * Added columns (add in 0009_v2_patient_master_cleanup):
      special_weekly_pattern (JSONB),
      special_week_active    (JSONB; ``[{"iso_year","iso_week"}, ...]``)

The legacy ``special_week`` JSONB column is kept for backward compatibility
with the existing ``special_weeks`` / ``special_week_items`` ad-hoc data,
which Wave 6 (W6-MIG2) will retire separately.

The v1 allocation engine (``app.services.allocation.engine``) and a few
import scripts still reference the dropped attributes; those code paths
are guarded by ``getattr(..., default)`` lookups (see ``allocate.py``)
or are CLI-only and will be retired in Wave 4-6 when the v1 engine is
frozen.

JSONB column rendering: production uses PostgreSQL where ``JSONB`` maps
directly. The pytest suite uses SQLite; ``JSONB().with_variant(JSON(),
'sqlite')`` substitutes the cross-dialect ``JSON`` type so
``Base.metadata.create_all`` succeeds without a custom compile hook.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# Cross-dialect JSONB: PostgreSQL gets native JSONB, SQLite (tests) gets JSON.
# Without ``with_variant``, ``Base.metadata.create_all`` on SQLite raises
# ``CompileError: Compiler ... can't render element of type JSONB``.
JSONBish = JSONB().with_variant(JSON(), "sqlite")


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kana: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)  # male/female/unknown
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

    sex_restriction: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # 通常パターン (§3.4 / §4.1). 既存。``staff_count`` キーは v2 schema 側で扱う。
    weekly_pattern: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    # 旧 v1 の "special_week" JSONB (per-patient hint). 旧 special_weeks/items の
    # 補助。Wave 6 (W6-MIG2) で正式廃止予定。
    special_week: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)

    # ---- W1-BE1 v2 additions ------------------------------------------------
    # 特別週パターン (§3.4 Option A 確定)
    special_weekly_pattern: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    # 適用週リスト [{iso_year:int, iso_week:int}, ...]
    # SQLAlchemy 上の型は dict だが、実用上は list[dict] として保持する。
    special_week_active: Mapped[list | None] = mapped_column(JSONBish, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    allowed_offices: Mapped[list[PatientAllowedOffice]] = relationship(
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

    patient: Mapped[Patient] = relationship("Patient", back_populates="allowed_offices")
