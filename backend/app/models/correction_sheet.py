"""Correction sheets (連携センター差分シート) and their items."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# 差分の向き: outbound = CareFlow→カイポケ (既存の週次反映) /
# inbound = カイポケ→CareFlow (逆反映・提供中の週の追いかけ)。
CORRECTION_DIRECTIONS = ("outbound", "inbound")


class CorrectionSheet(Base, TimestampMixin):
    __tablename__ = "correction_sheets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    target_month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    direction: Mapped[str] = mapped_column(String(8), nullable=False, default="outbound")
    # 差分計算時の週レンジ (apply実績ゲートの判定材料。月スコープ計算時は NULL)。
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    week_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # シートの出所 (mig 0076・week-cockpit §2-4/§2-5)。
    # NULL = 通常の差分計算 (diff-local / diff-inbound / kaipoke diff)
    # 'cached'  = 保存済み現況CSVからのローカル再計算 (●未送信・RPA なし)
    # 'reverse' = inbound シートを反転して作った上書き送信シート (⇧上書き)
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)

    items: Mapped[list[CorrectionSheetItem]] = relationship(
        "CorrectionSheetItem",
        back_populates="sheet",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_correction_sheets_month", "target_month"),
        Index("ix_correction_sheets_status", "status"),
        Index("ix_correction_sheets_direction_week", "direction", "week_start"),
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

    sheet: Mapped[CorrectionSheet] = relationship("CorrectionSheet", back_populates="items")

    __table_args__ = (
        Index("ix_correction_items_sheet_action", "sheet_id", "action"),
        Index("ix_correction_items_sheet_include", "sheet_id", "include"),
    )
