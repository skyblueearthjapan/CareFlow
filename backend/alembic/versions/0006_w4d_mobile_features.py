"""W4-D mobile features: visit_photos + notifications + shift_requests

Revision ID: 0006_w4d_mobile_features
Revises: 0005_special_weeks_unique
Create Date: 2026-05-05

Wave 4-D (mobile UI "準備中" 全消し) ships three independent features that
each need a single new table. Bundling them in one migration keeps the
revision graph linear and avoids three separate downtime windows on
production:

  1. ``visit_photos``     — staff-uploaded photo records for a visit
                            (file binary lives on disk under
                            ``/opt/carelink/data/visit_photos``; this row
                            only stores the metadata).
  2. ``notifications``    — per-user notification inbox surfaced by the
                            mobile bell icon. Producers (W6) will write
                            rows; for now an admin-only manual create is
                            available.
  3. ``shift_requests``   — staff自由入力 シフト希望 (1 row per submission,
                            free-text content + status workflow).

## Preflight (production)

Pure additive migration: 3 new tables + 6 indexes, no existing column
touched. Safe to apply on a populated DB. Downgrade drops the tables
which destroys data — same caveat as 0004.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_w4d_mobile_features"
down_revision: Union[str, None] = "0005_special_weeks_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- visit_photos -----
    op.create_table(
        "visit_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("caption", sa.String(length=500), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
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
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
            ondelete="CASCADE",
            name="fk_visit_photos_visit_id_visits",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_visit_photos_uploaded_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visit_photos"),
    )
    op.create_index(
        "ix_visit_photos_visit_uploaded",
        "visit_photos",
        ["visit_id", "uploaded_at"],
    )

    # ----- notifications -----
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
            ondelete="CASCADE",
            name="fk_notifications_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "read_at"],
    )

    # ----- shift_requests -----
    op.create_table(
        "shift_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_month", sa.Date(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="submitted",
        ),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["staff_id"],
            ["staff.id"],
            ondelete="CASCADE",
            name="fk_shift_requests_staff_id_staff",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_shift_requests_reviewed_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shift_requests"),
    )
    op.create_index(
        "ix_shift_requests_staff_month",
        "shift_requests",
        ["staff_id", "target_month"],
    )
    op.create_index(
        "ix_shift_requests_status",
        "shift_requests",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_shift_requests_status", table_name="shift_requests")
    op.drop_index("ix_shift_requests_staff_month", table_name="shift_requests")
    op.drop_table("shift_requests")

    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_visit_photos_visit_uploaded", table_name="visit_photos")
    op.drop_table("visit_photos")
