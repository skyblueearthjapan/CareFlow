"""Correction sheets (連携センター差分シート) and their items."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class CorrectionSheet(Base, TimestampMixin):
    __tablename__ = "correction_sheets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    target_month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    items: Mapped[list["CorrectionSheetItem"]] = relationship(
        "CorrectionSheetItem",
        back_populates="sheet",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_correction_sheets_month", "target_month"),
        Index("ix_correction_sheets_status", "status"),
    )


class CorrectionSheetItem(Base, TimestampMixin):
    __tablename__ = "correction_sheet_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("correction_sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
    )
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    include: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    sheet: Mapped["CorrectionSheet"] = relationship("CorrectionSheet", back_populates="items")

    __table_args__ = (
        Index("ix_correction_items_sheet_action", "sheet_id", "action"),
        Index("ix_correction_items_sheet_include", "sheet_id", "include"),
    )
