"""event_templates (イベントひな形・共通 + 個人) 追加.

Revision ID: 0079_event_templates
Revises: 0078_visits_kaipoke_service_override
Create Date: 2026-08-24

## このマイグレーションの責務

正典設計書 ``docs/plans/staff-event-history-design.md`` §2 Phase 2 のデータ層。
「よく使うイベント」を 1 行 = 1 ひな形として持つ 1 テーブル。

* ``staff_id IS NULL`` = **事業所共通** (全スタッフのプルダウンに出る)。
  値あり = **そのスタッフ個人**のひな形 (対象スタッフ選択時だけ出る)。
* ``start_time`` / ``end_time`` は **両方 NULL 可** (=「時間はその場で入力」)。
  片方だけ NULL は API 層で 422 (DB CHECK は貼らない — 既存の作法に合わせる)。
* ``sort_order`` はスコープ (共通 / 各スタッフ) 内の表示順。``is_active=false``
  は「無効化」= プルダウンから消えるが履歴として行は残す (物理削除は別 API)。

このテーブルは **ひな形 (型)** のみを持ち、実イベント (staff_events) とは
FK で繋がない。ひな形を選んで作られたイベントはこれまで通り staff_events へ
普通に insert される (ひな形の後からの変更は既存イベントに波及しない)。

朝会など「毎週の固定イベント」は別テーブル ``staff_event_defaults`` (mig 0074)
の役割。ここでは扱わない (PO決定: 朝会はデータでありコードではない)。

## downgrade

テーブル drop のみ (staff_events 側には一切影響しない)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0079_event_templates"
down_revision: str | Sequence[str] | None = "0078_visits_kaipoke_service_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    false_lit = sa.text("false" if is_pg else "0")
    true_lit = sa.text("true" if is_pg else "1")

    op.create_table(
        "event_templates",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=True,
            comment="NULL=事業所共通 / 値あり=そのスタッフ個人のひな形",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "event_type",
            sa.String(16),
            nullable=False,
            server_default="event",
            comment="'event' (イベント) / 'training' (研修)",
        ),
        sa.Column(
            "start_time",
            sa.Time(),
            nullable=True,
            comment="NULL=時間はその場で入力 (end_time と同時に NULL)",
        ),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=false_lit),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=true_lit),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
    )
    op.create_index("ix_event_templates_staff", "event_templates", ["staff_id"])


def downgrade() -> None:
    op.drop_index("ix_event_templates_staff", table_name="event_templates")
    op.drop_table("event_templates")
