"""型の作業場 (template workspace) の下書き: 下書き・枠・編集の履歴.

設計: docs/plans/agent-skills/template-workspace-design-2026-10-12.md
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

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
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.patient import JSONBish


class TemplateDraft(Base):
    __tablename__ = "template_drafts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = 全拠点の下書き (拠点は params.offices・拠点ごとの対象の方は params.scopes)。0093 から
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("offices.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_draft_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    # 楽観ロック: 変更のたびに +1. 変更 API は expected_version を送り、違えば 409.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # もしもの条件 (red/no_pref/cap/staff_count/drop_staff/add_patients/prefs) + scope + basis(患者ごとの指紋)
    params: Mapped[dict] = mapped_column(JSONBish, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONBish, nullable=False, default=dict)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_template_drafts_office", "office_id", "status"),)


class TemplateDraftSlot(Base):
    __tablename__ = "template_draft_slots"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("template_drafts.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    course_template_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("course_templates.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="master")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("draft_id", "patient_id", "weekday", "slot_index", name="uq_template_draft_slots_key"),
        Index("ix_template_draft_slots_draft", "draft_id", "weekday"),
    )


class TemplateDraftStep(Base):
    """下書きの編集の履歴 1 件. actor = human | agent (らく助エージェント)."""

    __tablename__ = "template_draft_steps"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("template_drafts.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # move/optimise/whatif/add/remove
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{key, patient_id, weekday, slot_index, before: {course,start,source}|None, after: {...}|None}]
    changes: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    params_before: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    params_after: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 最適化の報告 (何をどう変えたか・曜日ごとの物差し・移動の増減の内訳・説明文)。kind=optimise だけ。0093 から
    summary: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("draft_id", "seq", name="uq_template_draft_steps_seq"),)
