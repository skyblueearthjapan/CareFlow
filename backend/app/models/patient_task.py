"""事務所からのやること (看護師へのタスク) — mig 0103.

正典 = ``docs/plans/patient-tasks-design-2026-11-01.md`` (PO 決定は §2)。

* ``task_item_types``: やることの項目 (名前・並び順・使う/使わない)。事業所の情報を
  コードに書かないため、項目は管理者が画面で足す。初期データは入れない。
* ``patient_tasks``: 患者 + 日付 (この日から出す) で持つ。**出す訪問は保存しない** —
  見るたびに「その患者の、日付以降の、取消・削除でない最初の訪問」を決める
  (``app.services.patient_tasks``)。完了したときだけ、その訪問 (``done_visit_id``) と
  人と時刻を残す。``item_type_id`` が NULL なら伝言 (``memo`` が要る)。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TaskItemType(Base, TimestampMixin):
    """やることの項目 (管理者が足す・名前を変える・使わないにする・並べ替える)."""

    __tablename__ = "task_item_types"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class PatientTask(Base, TimestampMixin):
    """患者 + 日付で持つやること 1 件 (項目 1 つ、または伝言)."""

    __tablename__ = "patient_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: この日から出す (この日以降の最初の訪問に出る)。
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL なら伝言。項目は消さず「使わない」にするので RESTRICT。
    item_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_item_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    done_by_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    done_visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_patient_tasks_patient_start", "patient_id", "start_date"),
        Index("ix_patient_tasks_done_visit", "done_visit_id"),
        # 項目も伝言も空は不可 (空白だけの伝言は API で弾く)。
        CheckConstraint(
            "item_type_id IS NOT NULL OR memo IS NOT NULL",
            name="item_or_memo",
        ),
    )
