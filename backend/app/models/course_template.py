"""CourseTemplate (永続コーステンプレート) — Wave 15 Phase 1 (W15-BE1).

`courses` テーブルが「週次インスタンス」(iso_year, iso_week, weekday, code) を
表すのに対し、本テーブルは拠点単位の **永続テンプレート** を表す。

## 概念

- 拠点 (office) ごとに A/B/C/D/E などの「曜日別定員」を持つ。
- 週次の `courses` 行は本テンプレートからインスタンス化される
  (`courses.template_id` で源泉を辿れる)。
- 同一拠点で同一ラベルは禁止 (UNIQUE office_id, label)。
- 論理削除 (`deleted_at`) を採用。

## CHECK 制約

各曜日の capacity は 0..50 の整数 (運用上 1 コース最大 6 名 × 余裕分の上限)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CourseTemplate(Base, TimestampMixin):
    """永続コーステンプレート (course_templates テーブル)."""

    __tablename__ = "course_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(8), nullable=False)

    capacity_mon: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_tue: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_wed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_thu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_fri: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_sat: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacity_sun: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # W15-codex-fix (3): 論理削除を考慮した partial UNIQUE INDEX.
        # PostgreSQL では deleted_at IS NULL のみを UNIQUE 対象にする partial
        # index になる (= 論理削除済み label を再活性化可能)。SQLite (テスト)
        # では partial WHERE が無視されてフル UNIQUE INDEX として動作する。
        # migration 0021 が production 側のインデックスを担保する。
        Index(
            "uq_course_templates_office_label_active",
            "office_id",
            "label",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "capacity_mon BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_mon",
        ),
        CheckConstraint(
            "capacity_tue BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_tue",
        ),
        CheckConstraint(
            "capacity_wed BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_wed",
        ),
        CheckConstraint(
            "capacity_thu BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_thu",
        ),
        CheckConstraint(
            "capacity_fri BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_fri",
        ),
        CheckConstraint(
            "capacity_sat BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_sat",
        ),
        CheckConstraint(
            "capacity_sun BETWEEN 0 AND 50",
            name="ck_course_templates_capacity_sun",
        ),
        Index("ix_course_templates_office", "office_id"),
    )
