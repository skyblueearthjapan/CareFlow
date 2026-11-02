"""事務所からのやること (看護師へのタスク) — mig 0103.

正典 = ``docs/plans/patient-tasks-design-2026-11-01.md`` (PO 決定は §2)。

* ``task_item_types``: やることの項目 (名前・並び順・使う/使わない)。事業所の情報を
  コードに書かないため、項目は管理者が画面で足す。初期データは入れない。
* ``patient_tasks``: 患者 + 日付 (この日から出す) で持つ。**出す訪問は保存しない** —
  見るたびに「その患者の、日付以降の、取消・削除でない最初の訪問」を決める
  (``app.services.patient_tasks``)。完了したときだけ、その訪問 (``done_visit_id``) と
  人と時刻を残す。``item_type_id`` が NULL なら伝言 (``memo`` が要る)。
* ``recurring_task_rules``・``recurring_task_exclusions`` (mig 0105): 毎月のやることの設定と、
  設定ごとに個別に外す方。毎月その日に、対象の方へ ``patient_tasks`` を 1 件ずつ作る
  (``app.services.recurring_tasks``・正典 ``docs/plans/patient-tasks-recurring-design-2026-11-02.md``)。
  作ったやることは ``recurring_rule_id``・``recurring_month`` を持つほかは手で付けたものと同じ。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 本番は JSONB・試験 (SQLite) は JSON。
_JSONBish = JSONB().with_variant(JSON(), "sqlite")


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

    #: 毎月の設定から作ったときだけ: どの設定から (手で付けたものは NULL)。mig 0105。
    recurring_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recurring_task_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 毎月の設定から作ったときだけ: 何月分か (その月の 1 日)。
    recurring_month: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        Index("ix_patient_tasks_patient_start", "patient_id", "start_date"),
        # 同じ設定・同じ方・同じ月は 1 件だけ (見回りを何回流しても 2 件にしない)。
        Index(
            "uq_patient_tasks_recurring_once",
            "recurring_rule_id",
            "patient_id",
            "recurring_month",
            unique=True,
            postgresql_where=text("recurring_rule_id IS NOT NULL"),
            sqlite_where=text("recurring_rule_id IS NOT NULL"),
        ),
        Index("ix_patient_tasks_done_visit", "done_visit_id"),
        # 項目も伝言も空は不可 (空白だけの伝言は API で弾く)。
        CheckConstraint(
            "item_type_id IS NOT NULL OR memo IS NOT NULL",
            name="item_or_memo",
        ),
    )


class RecurringTaskRule(Base, TimestampMixin):
    """毎月のやることの設定 (どの項目を・どの区分の方に・毎月何日から)."""

    __tablename__ = "recurring_task_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: NULL なら伝言だけ (``memo`` が要る)。
    item_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_item_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 毎月何日から (1〜28)。
    day_of_month: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: 対象の訪問の区分 (``patients.visit_category`` の値の配列)。
    target_categories: Mapped[list[str]] = mapped_column(_JSONBish, nullable=False)
    #: 最初に作る月 (その月の 1 日)。
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("item_type_id IS NOT NULL OR memo IS NOT NULL", name="item_or_memo"),
        CheckConstraint("day_of_month BETWEEN 1 AND 28", name="day_of_month_range"),
    )


class RecurringTaskExclusion(Base):
    """毎月のやることの設定ごとに、個別に外す方."""

    __tablename__ = "recurring_task_exclusions"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recurring_task_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (PrimaryKeyConstraint("rule_id", "patient_id"),)
