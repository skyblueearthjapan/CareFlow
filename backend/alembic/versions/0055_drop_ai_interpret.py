"""AI機能撤去: pending_requests.ai_interpret_log_id 列と ai_interpret_logs テーブルを DROP.

Revision ID: 0055_drop_ai_interpret
Revises: 0054_kaipoke_csv_fields
Create Date: 2026-07-05

## このマイグレーションの責務

AI 入力機能 (Gemini 自然言語→構造化・/api/v1/ai/*) の全撤去に伴い、
AI 専用の DB 資産を削除する:

1. `pending_requests.ai_interpret_log_id` — AI 解釈ログへの FK 列。
   申請・承認基盤 (pending_requests) 自体は手動申請 (現場ボード) が
   使用中のため存続。AI 由来の列のみ落とす。
2. `ai_interpret_logs` テーブル — Gemini prompt/response 監査ログ
   (0003 で作成)。本番は 0 行を確認済み (2026-07-05)。

## downgrade

列とテーブルを 0013 / 0003 の定義どおり再作成する (データは戻らない)。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0055_drop_ai_interpret"
down_revision: Union[str, Sequence[str], None] = "0054_kaipoke_csv_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.drop_constraint(
            "fk_pending_requests_ai_interpret_log_id_ai_interpret_logs",
            "pending_requests",
            type_="foreignkey",
        )
        op.drop_column("pending_requests", "ai_interpret_log_id")
        op.drop_index("ix_ai_interpret_logs_user_id", table_name="ai_interpret_logs")
        op.drop_index("ix_ai_interpret_logs_created_at", table_name="ai_interpret_logs")
        op.drop_table("ai_interpret_logs")
    else:
        # SQLite (テスト): batch mode で列 DROP (FK はテーブル再構築で消える)。
        with op.batch_alter_table("pending_requests") as batch:
            batch.drop_column("ai_interpret_log_id")
        op.drop_index("ix_ai_interpret_logs_user_id", table_name="ai_interpret_logs")
        op.drop_index("ix_ai_interpret_logs_created_at", table_name="ai_interpret_logs")
        op.drop_table("ai_interpret_logs")


def downgrade() -> None:
    if _is_postgres():
        # ai_interpret_logs を 0003 の定義どおり再作成。
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
        op.create_index("ix_ai_interpret_logs_created_at", "ai_interpret_logs", ["created_at"])
        op.create_index("ix_ai_interpret_logs_user_id", "ai_interpret_logs", ["user_id"])
        op.add_column(
            "pending_requests",
            sa.Column("ai_interpret_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_pending_requests_ai_interpret_log_id_ai_interpret_logs",
            "pending_requests",
            "ai_interpret_logs",
            ["ai_interpret_log_id"],
            ["id"],
            ondelete="SET NULL",
        )
    else:
        op.create_table(
            "ai_interpret_logs",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("response", sa.JSON(), nullable=False),
            sa.Column("model", sa.String(length=64), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
        )
        op.create_index("ix_ai_interpret_logs_created_at", "ai_interpret_logs", ["created_at"])
        op.create_index("ix_ai_interpret_logs_user_id", "ai_interpret_logs", ["user_id"])
        with op.batch_alter_table("pending_requests") as batch:
            batch.add_column(
                sa.Column(
                    "ai_interpret_log_id",
                    sa.String(length=36),
                    sa.ForeignKey("ai_interpret_logs.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
