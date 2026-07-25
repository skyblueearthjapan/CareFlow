"""イベント取り込み: staff_events に source / external_id を追加 (E-1).

docs/plans/kaipoke-event-inbound-design.md §2:
カイポケ個別業務(イベント)取り込みの受け皿。
- source: 'manual'(手動登録・既存全行の既定) / 'kaipoke'(取り込みが管理する行)
- external_id: 複合冪等キー "{個別業務ID}:{職員内部ID}:{YYYY-MM-DD}"
  (個別業務IDは週パターン由来予定で複数日共有のため単独では一意にならない・
   2026-07-25 実データ検証)
- 部分ユニーク索引 (source, external_id) WHERE external_id IS NOT NULL
  → 再取り込みの冪等 upsert を DB レベルで担保

Revision ID: 0062_staff_event_source
Revises: 0061_drop_staff_companion
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062_staff_event_source"
down_revision: str | Sequence[str] | None = "0061_drop_staff_companion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_events",
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
    )
    op.add_column(
        "staff_events",
        sa.Column("external_id", sa.String(40), nullable=True),
    )
    op.create_index(
        "uq_staff_events_source_external",
        "staff_events",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_staff_events_source_external", table_name="staff_events")
    op.drop_column("staff_events", "external_id")
    op.drop_column("staff_events", "source")
