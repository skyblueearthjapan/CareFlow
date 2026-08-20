"""staff_event_defaults (毎週の固定イベント既定・朝会など) 追加.

Revision ID: 0074_staff_event_defaults
Revises: 0073_staff_shift_confirmations
Create Date: 2026-08-20

## このマイグレーションの責務

正典設計書 ``docs/plans/kaipoke-event-two-way-design.md`` §3-② のデータ層。
スタッフ×曜日 (0=月〜5=土)×時間帯×名称の「毎週の固定イベント」定義 1 テーブル。

* 週生成系 3 地点から ``expand_staff_event_defaults`` が当該週の
  staff_events (source='fixed'・external_id='{default_id}:{YYYY-MM-DD}') へ
  冪等展開する (同行既定 accompaniment_defaults と同じ作法)。
* staff_events 側の変更は不要 (source は自由文字列・部分 UNIQUE
  (source, external_id) をそのまま冪等キーに流用)。

## downgrade

テーブル drop のみ (展開済みの staff_events 行はそのまま残る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0074_staff_event_defaults"
down_revision: str | Sequence[str] | None = "0073_staff_shift_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    false_lit = sa.text("false" if is_pg else "0")

    op.create_table(
        "staff_event_defaults",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False, comment="0=月〜5=土"),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=false_lit),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
    )
    op.create_index(
        "ix_staff_event_defaults_staff", "staff_event_defaults", ["staff_id", "weekday"]
    )


def downgrade() -> None:
    op.drop_index("ix_staff_event_defaults_staff", table_name="staff_event_defaults")
    op.drop_table("staff_event_defaults")
