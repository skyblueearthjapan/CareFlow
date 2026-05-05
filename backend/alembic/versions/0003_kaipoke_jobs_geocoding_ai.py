"""kaipoke jobs + items, geocoding cache, ai interpret logs (Phase 5-1, P-06)

Revision ID: 0003_kaipoke_jobs_geocoding_ai
Revises: 0002_add_office_prefecture_code
Create Date: 2026-05-05

Wave 2-B (連携センター + 関連テーブル): adds the 4 tables that back the
Kaipoke Playwright job runner, the geocoding lookup cache, and the LLM
interpret audit log. Execution logic itself is NOT in this wave — only the
schema + REST surface so the UI can be wired up.

## Preflight (production)

This migration only adds new tables — no destructive changes — so it is
safe to run on a populated DB. The geocoding_cache.address_hash column is
declared UNIQUE; populate via insert/upsert keyed on sha256(address).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_kaipoke_jobs_geocoding_ai"
down_revision: Union[str, None] = "0002_add_office_prefecture_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- kaipoke_jobs -----
    op.create_table(
        "kaipoke_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_kaipoke_jobs_created_by_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kaipoke_jobs"),
    )
    op.create_index("ix_kaipoke_jobs_week_start", "kaipoke_jobs", ["week_start"])
    op.create_index("ix_kaipoke_jobs_status", "kaipoke_jobs", ["status"])
    op.create_index(
        "ix_kaipoke_jobs_type_week", "kaipoke_jobs", ["job_type", "week_start"]
    )

    # ----- kaipoke_job_items -----
    op.create_table(
        "kaipoke_job_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["kaipoke_jobs.id"],
            ondelete="CASCADE",
            name="fk_kaipoke_job_items_job_id_kaipoke_jobs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kaipoke_job_items"),
    )
    op.create_index(
        "ix_kaipoke_job_items_job_seq", "kaipoke_job_items", ["job_id", "seq"]
    )

    # ----- geocoding_cache -----
    op.create_table(
        "geocoding_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address_hash", sa.String(length=64), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("lat", sa.Numeric(10, 7), nullable=False),
        sa.Column("lng", sa.Numeric(10, 7), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="google"),
        sa.Column(
            "looked_up_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_geocoding_cache"),
        sa.UniqueConstraint("address_hash", name="uq_geocoding_cache_address_hash"),
    )

    # ----- ai_interpret_logs -----
    op.create_table(
        "ai_interpret_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_ai_interpret_logs_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_interpret_logs"),
    )
    op.create_index(
        "ix_ai_interpret_logs_created_at", "ai_interpret_logs", ["created_at"]
    )
    op.create_index("ix_ai_interpret_logs_user_id", "ai_interpret_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_interpret_logs_user_id", table_name="ai_interpret_logs")
    op.drop_index("ix_ai_interpret_logs_created_at", table_name="ai_interpret_logs")
    op.drop_table("ai_interpret_logs")

    op.drop_table("geocoding_cache")

    op.drop_index("ix_kaipoke_job_items_job_seq", table_name="kaipoke_job_items")
    op.drop_table("kaipoke_job_items")

    op.drop_index("ix_kaipoke_jobs_type_week", table_name="kaipoke_jobs")
    op.drop_index("ix_kaipoke_jobs_status", table_name="kaipoke_jobs")
    op.drop_index("ix_kaipoke_jobs_week_start", table_name="kaipoke_jobs")
    op.drop_table("kaipoke_jobs")
