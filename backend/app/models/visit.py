"""Visit (訪問) — single row per scheduled visit.

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.3 / §4.5):
  - ``course_id`` FK NULL — コース所属 (Layer 2 で決定)
  - ``required_staff_count`` (1 or 2) — 2 名体制対応
  - ``visit_group_id`` UUID NULL — 2 名体制の訪問グルーピング

2 名体制の場合は **同じ ``visit_group_id`` を持つ visit が 2 行存在** し、
``visit_staff_assignments`` 経由で計 2 スタッフが割り当てられる
(各 visit 行に 1 人ずつ)。

primary_staff_id / secondary_staff_id は v1 互換のため残置 (Wave 6 まで併用)。
v2 正規のスタッフ割当は ``app.models.visit_staff_assignment.VisitStaffAssignment``。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    false,
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

# Source constant (Wave U-0 / 変更反映先の統一 §2.2).
# 「この週だけ反映」(B 選択) で書いた visit を示す source 値.
# 保護規則:
#   - Layer1 generate-week-only は ``source == 'auto'`` のみ削除 → ``manual_week`` は残る.
#   - reset-to-fixed は ``_RESET_DELETABLE_SOURCES`` 列挙式の whitelist で削除 →
#     ``manual_week`` は列挙外なので残る.
# よって週生成・固定枠戻の両方でこの source は保護される (回帰テストで固定).
# NOTE: Wave U-0 では値の定義と保護保証のみ. この source で **書く** 経路は U-1 で追加.
# 長さは ``Visit.source`` の String(16) に収まる (= 11 文字).
VISIT_SOURCE_MANUAL_WEEK: str = "manual_week"


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
    patient: Mapped[Patient] = relationship("Patient", foreign_keys=[patient_id], lazy="noload")
    primary_staff: Mapped[Staff | None] = relationship(
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

    # ---- W2-BE4 v2 additions (§3.3 / §4.5) ----------------------------------
    # 所属コース (Layer 2 で決定; CRUD 段階では NULL のまま運用しても可)
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 必要スタッフ数 (1 or 2). DB 側 CHECK で範囲を担保
    required_staff_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    # 2 名体制グルーピングキー (§3.3).
    # 通常 (required_staff_count=1) は NULL.
    # 2 名体制 (required_staff_count=2) では同じ UUID を持つ visit が 2 行。
    visit_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # ---- P3-① 当日欠勤の代替スタッフ提案 (migration 0048) --------------------
    # 当日欠勤対応で手動差替えした visit の保護フラグ. True の visit は
    # layer3 ``_persist`` (assign-staff-only / 自動割当) の VSA DELETE/INSERT
    # 対象から除外される — これにより手動差替えが自動割当で上書きされない.
    # (除外実装自体は Commit 2. 本カラムは Commit 1 で追加のみ.)
    manual_staff_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_visits_date", "visit_date"),
        Index("ix_visits_patient_date", "patient_id", "visit_date"),
        Index("ix_visits_primary_date", "primary_staff_id", "visit_date"),
        Index("ix_visits_status", "status"),
        # W2-BE4: コース別検索 / 2 名体制ペア検索
        Index("ix_visits_course", "course_id"),
        Index("ix_visits_group", "visit_group_id"),
        # required_staff_count は 1 or 2 のみ受け付ける (§3.3)
        CheckConstraint(
            "required_staff_count IN (1, 2)",
            name="ck_visits_required_staff_count_v2",
        ),
    )
