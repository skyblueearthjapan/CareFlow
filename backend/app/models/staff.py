"""Staff (スタッフ) + secondary offices + shifts + weekly overrides + events + companion assignments.

W1-BE2 (v2 整理): can_double_team / home_address / home_lat / home_lng /
areas / max_per_day / skill_level / assignment_volume の 8 カラムを削除済み
(設計書 §4.2). 物理 DROP は migration 0010 で実施。

W10-BE1: mentor_id / MentorAssignment 廃止。is_trainee 追加。
同行スタッフ管理は staff_companion_assignments テーブルへ移行。
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.staff_companion_assignment import StaffCompanionAssignment


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kana: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="staff")
    # K-1b: カイポケ18列CSV「職種」列の値 (看護師/准看護師/理学療法士…)。
    # role (admin/manager/staff = システム権限) とは独立。カイポケ転記専用。
    qualification: Mapped[str | None] = mapped_column(String(16), nullable=True)

    primary_office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_trainee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    secondary_offices: Mapped[list[StaffSecondaryOffice]] = relationship(
        "StaffSecondaryOffice",
        back_populates="staff",
        cascade="all, delete-orphan",
    )
    shifts: Mapped[list[StaffShift]] = relationship(
        "StaffShift",
        back_populates="staff",
        cascade="all, delete-orphan",
    )
    companion_assignments_as_trainee: Mapped[list[StaffCompanionAssignment]] = relationship(
        "StaffCompanionAssignment",
        foreign_keys="StaffCompanionAssignment.trainee_staff_id",
        back_populates="trainee",
        cascade="all, delete-orphan",
    )
    companion_assignments_as_companion: Mapped[list[StaffCompanionAssignment]] = relationship(
        "StaffCompanionAssignment",
        foreign_keys="StaffCompanionAssignment.companion_staff_id",
        back_populates="companion",
        # FK ondelete=CASCADE 側に削除を任せる。companion 側で
        # delete-orphan を付けると削除が二重発火する恐れがあるため save-update のみ。
        cascade="save-update, merge",
    )

    __table_args__ = (
        Index("ix_staff_status_office", "status", "primary_office_id"),
        # W41 後続 cross-review HIGH#3: concurrent import で staff.code 重複が
        # silent に通る race を防ぐ partial UNIQUE INDEX. PostgreSQL では
        # ``deleted_at IS NULL AND code IS NOT NULL`` のみを対象にする
        # (soft-delete 済み / code NULL は除外). SQLite (テスト) でも
        # 同条件の partial UNIQUE が貼られる (SQLite 3.8+ サポート).
        # migration 0032_staff_code_unique_partial が production 側を担保する.
        Index(
            "ix_staff_code_unique_alive",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND code IS NOT NULL"),
            sqlite_where=text("deleted_at IS NULL AND code IS NOT NULL"),
        ),
    )


class StaffSecondaryOffice(Base):
    __tablename__ = "staff_secondary_offices"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        primary_key=True,
    )

    staff: Mapped[Staff] = relationship("Staff", back_populates="secondary_offices")


class StaffShift(Base):
    """Fixed weekly shift (7 rows per staff, weekday 0=Mon ... 6=Sun)."""

    __tablename__ = "staff_shifts"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    is_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    staff: Mapped[Staff] = relationship("Staff", back_populates="shifts")


class StaffWeeklyOverride(Base, TimestampMixin):
    """Per-week override (休み or 時間変更) for a single weekday."""

    __tablename__ = "staff_weekly_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    iso_year: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    override_type: Mapped[str] = mapped_column(String(16), nullable=False)  # off / custom_time
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "staff_id", "iso_year", "iso_week", "weekday", name="uq_staff_week_override"
        ),
        Index("ix_staff_overrides_lookup", "iso_year", "iso_week", "staff_id"),
    )


class StaffEvent(Base, TimestampMixin):
    __tablename__ = "staff_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_staff_events_when", "staff_id", "starts_at"),)
