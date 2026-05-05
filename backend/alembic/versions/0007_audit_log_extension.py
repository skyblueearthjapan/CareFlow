"""audit_logs extension + users.deleted_at / users.must_change_password

Revision ID: 0007_audit_log_extension
Revises: 0005_special_weeks_unique
Create Date: 2026-05-05

Wave 4-F: brings the audit_logs table up to the shape the new HTTP middleware
writes (path / method / status / latency / IP / user-agent / redacted body)
and adds the user-management columns (`deleted_at`, `must_change_password`)
required by the freshly implemented `/admin/users` CRUD endpoints.

This migration is idempotent for existing rows: every new column is nullable
(or has a server default) so back-filling is not required.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_audit_log_extension"
down_revision: Union[str, None] = "0005_special_weeks_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------- audit_logs: HTTP / middleware columns ----------------
    op.add_column(
        "audit_logs",
        sa.Column("role", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("method", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("path", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("status_code", sa.Integer(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("ip_address", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("user_agent", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_method_path",
        "audit_logs",
        ["method", "path"],
    )
    op.create_index(
        "ix_audit_logs_status_code",
        "audit_logs",
        ["status_code"],
    )

    # ---------------- users: soft-delete + must_change_password ----------------
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "deleted_at")

    op.drop_index("ix_audit_logs_status_code", table_name="audit_logs")
    op.drop_index("ix_audit_logs_method_path", table_name="audit_logs")
    op.drop_column("audit_logs", "request_body")
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip_address")
    op.drop_column("audit_logs", "latency_ms")
    op.drop_column("audit_logs", "status_code")
    op.drop_column("audit_logs", "path")
    op.drop_column("audit_logs", "method")
    op.drop_column("audit_logs", "role")
