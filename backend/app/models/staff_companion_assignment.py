"""StaffCompanionAssignment モデル (W10-BE1).

曜日 × 午前/午後/終日 単位での同行スタッフ割当を管理する。
新人スタッフ (is_trainee=True) が独り立ち前に同行スタッフと一緒に訪問する運用を支援。

UNIQUE (trainee_staff_id, weekday, part) — 同一 trainee × 曜日 × part は 1 割当のみ。
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class StaffCompanionAssignment(Base, TimestampMixin):
    """同行スタッフ割当 (staff_companion_assignments テーブル)."""

    __tablename__ = "staff_companion_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trainee_staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="0=Mon ... 6=Sun",
    )
    part: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="'am' / 'pm' / 'full'",
    )
    companion_staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )

    # W15-BE1: 主担当 (primary) / 補佐 (support) のロール区分。NULL は未設定運用。
    pair_role: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="'primary' (主担当) / 'support' (補佐) / NULL",
    )

    # Relationships
    trainee: Mapped[Staff] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Staff",
        foreign_keys=[trainee_staff_id],
        back_populates="companion_assignments_as_trainee",
    )
    companion: Mapped[Staff] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Staff",
        foreign_keys=[companion_staff_id],
        back_populates="companion_assignments_as_companion",
    )

    __table_args__ = (
        UniqueConstraint(
            "trainee_staff_id",
            "weekday",
            "part",
            name="uq_sca_trainee_weekday_part",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_sca_weekday"),
        CheckConstraint("part IN ('am','pm','full')", name="ck_sca_part"),
        CheckConstraint("trainee_staff_id != companion_staff_id", name="ck_sca_self_companion"),
        CheckConstraint(
            "pair_role IS NULL OR pair_role IN ('primary','support')",
            name="ck_sca_pair_role",
        ),
        Index("ix_sca_trainee", "trainee_staff_id"),
        Index("ix_sca_companion", "companion_staff_id"),
    )
