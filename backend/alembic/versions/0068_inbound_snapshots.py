"""inbound_snapshots — カイポケ取り込み直前の週バックアップ (PO 決定 2026-08-09).

Revision ID: 0068_inbound_snapshots
Revises: 0067_pin_mirrors_locked
Create Date: 2026-08-09

## このマイグレーションの責務

未来週の取り込み開放 (時間ゲート撤廃) とセットで、「間違えて取り込んでも
取り込む前に戻せる」を成立させる保存先テーブルを作る。

実適用 (dry_run=false) の直前に、対象週の visits + スタッフ割当 + コース担当 +
同行リンク (訪問単位) を JSONB へ丸ごと保存する。保存は取り込みと同一
トランザクション。週ごとに直近 5 世代のみ保持 (剪定はアプリ側)。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068_inbound_snapshots"
down_revision = "0067_pin_mirrors_locked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("visits_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_inbound_snapshots_week_start", "inbound_snapshots", ["week_start"])


def downgrade() -> None:
    op.drop_index("ix_inbound_snapshots_week_start", table_name="inbound_snapshots")
    op.drop_table("inbound_snapshots")
