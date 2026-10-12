"""型の作業場 (template workspace) の下書き 3 表.

Revision ID: 0092_template_drafts
Revises: 0091_agent_schema
Create Date: 2026-10-12

* ``template_drafts``       下書き (日付の無い仮の 1 週間)。``version`` は楽観ロック (変更のたびに +1)。
* ``template_draft_slots``  下書きの枠 (マスターの固定訪問の枠の写し + 手/最適化で動かしたもの + 新しい枠)。
* ``template_draft_steps``  下書きの編集の履歴 (人 / らく助エージェント・取り消す/やり直す)。アプリの op-log とは別。

**デプロイ**: 0091_agent_schema (エージェントの中継) と 0092 は一緒にデプロイする
(0092 の down_revision が 0091)。機能は拠点の feature flag ``template_workspace`` が
既定 OFF (office_feature_flags に行が無い) なので、表を作っても画面・API は閉じたまま。

downgrade は 3 表を drop する (下書きは消える。マスターの固定訪問には触らない)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0092_template_drafts"
down_revision: str | Sequence[str] | None = "0091_agent_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()


def upgrade() -> None:
    op.create_table(
        "template_drafts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("office_id", _uuid(), sa.ForeignKey("offices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("base_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("origin_draft_id", _uuid(), nullable=True),
        sa.Column("created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("params", _json(), nullable=False),
        sa.Column("metrics", _json(), nullable=False),
        sa.Column("committed_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','committed','archived')", name="ck_template_drafts_status"),
    )
    op.create_index("ix_template_drafts_office", "template_drafts", ["office_id", "status"])

    op.create_table(
        "template_draft_slots",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("draft_id", _uuid(), sa.ForeignKey("template_drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", _uuid(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("slot_index", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column(
            "course_template_id",
            _uuid(),
            sa.ForeignKey("course_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="master"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint("source IN ('master','optimised','manual','new')", name="ck_template_draft_slots_source"),
        sa.UniqueConstraint("draft_id", "patient_id", "weekday", "slot_index", name="uq_template_draft_slots_key"),
    )
    op.create_index("ix_template_draft_slots_draft", "template_draft_slots", ["draft_id", "weekday"])

    op.create_table(
        "template_draft_steps",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("draft_id", _uuid(), sa.ForeignKey("template_drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(8), nullable=False),
        sa.Column("actor_user_id", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changes", _json(), nullable=False),
        sa.Column("params_before", _json(), nullable=True),
        sa.Column("params_after", _json(), nullable=True),
        sa.Column("undone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version_after", sa.Integer(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("actor IN ('human','agent')", name="ck_template_draft_steps_actor"),
        sa.UniqueConstraint("draft_id", "seq", name="uq_template_draft_steps_seq"),
    )


def downgrade() -> None:
    op.drop_table("template_draft_steps")
    op.drop_index("ix_template_draft_slots_draft", table_name="template_draft_slots")
    op.drop_table("template_draft_slots")
    op.drop_index("ix_template_drafts_office", table_name="template_drafts")
    op.drop_table("template_drafts")
