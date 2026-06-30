"""QR 訪問チェックイン Phase 5-2: notifications 冪等キー (reference_type / reference_id).

Revision ID: 0045_notification_reference
Revises: 0044_visit_reviews
Create Date: 2026-07-01

## このマイグレーションの責務

アプリ内通知 producer (場所違い / 未訪問) を **冪等** に生成するため、``notifications``
に冪等キー列を追加する (additive):

* ``reference_type`` (String(32) NULL): 通知の発生源種別 ("checkin_mismatch" /
  "checkin_missing" 等)。
* ``reference_id`` (UUID NULL): 紐づくドメイン行 ID (チェックイン通知では visit_id)。

そして **部分 UNIQUE** ``uq_notifications_reference``
``(user_id, reference_type, reference_id) WHERE reference_id IS NOT NULL`` を張り、
「同一 visit の同種通知は 1 ユーザー 1 行」を DB レベルで保証する (冪等化の安全網)。
``reference_id IS NULL`` の既存 / 手動通知は重複制約の対象外。

## SQLite 互換

0041 の ``uq_patients_qr_token`` と同じく dialect 分岐: PG = ``postgresql.UUID`` /
``postgresql_where``、SQLite = ``String(36)`` / ``sqlite_where``。

## downgrade

部分 UNIQUE index と 2 列を drop する。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0045_notification_reference"
down_revision: Union[str, Sequence[str], None] = "0044_visit_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type: sa.types.TypeEngine = (
        postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    )

    op.add_column(
        "notifications",
        sa.Column("reference_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("reference_id", uuid_type, nullable=True),
    )

    if is_pg:
        op.create_index(
            "uq_notifications_reference",
            "notifications",
            ["user_id", "reference_type", "reference_id"],
            unique=True,
            postgresql_where=sa.text("reference_id IS NOT NULL"),
        )
    else:
        op.create_index(
            "uq_notifications_reference",
            "notifications",
            ["user_id", "reference_type", "reference_id"],
            unique=True,
            sqlite_where=sa.text("reference_id IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index("uq_notifications_reference", table_name="notifications")
    op.drop_column("notifications", "reference_id")
    op.drop_column("notifications", "reference_type")
