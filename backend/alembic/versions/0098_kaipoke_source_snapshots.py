"""kaipoke_source_snapshots — 休みと受け入れ枠が読むカイポケの材料.

Revision ID: 0098_kaipoke_source_snapshots
Revises: 0097_checkin_patient_idx
Create Date: 2026-10-20

共通の土台 (``docs/plans/shared-foundation-leave-acceptance-2026-10-20.md`` §0-2):
予定 CSV・実績 CSV (月の 1 日)・個別業務 (週の月曜) を ``(kind, period_start)`` ごとに
1 行で置く。``kaipoke_csv_snapshots`` には触らない。

downgrade は表を drop する (取り直せば戻る材料だけで、業務データは無い)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0098_kaipoke_source_snapshots"
down_revision: str | Sequence[str] | None = "0097_checkin_patient_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine:
    return (
        postgresql.JSONB(astext_type=sa.Text())
        if op.get_bind().dialect.name == "postgresql"
        else sa.JSON()
    )


def _uuid() -> sa.types.TypeEngine:
    return (
        postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()
    )


def upgrade() -> None:
    op.create_table(
        "kaipoke_source_snapshots",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "office_id", _uuid(), sa.ForeignKey("offices.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("rows", _json(), nullable=False),
        sa.Column("covered_dates", _json(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fetched_at", _TS, nullable=False),
        sa.Column(
            "job_id", _uuid(), sa.ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.CheckConstraint(
            "kind IN ('plan_csv','actual_csv','tasks')", name="ck_kaipoke_source_snapshots_kind"
        ),
        sa.CheckConstraint(
            "status IN ('ok','empty','failed')", name="ck_kaipoke_source_snapshots_status"
        ),
        sa.UniqueConstraint("kind", "period_start", name="uq_kaipoke_source_snapshots_kind_period"),
    )


def downgrade() -> None:
    op.drop_table("kaipoke_source_snapshots")
