"""staff_events に cancelled_at (今週だけ外す) を追加.

Revision ID: 0075_staff_events_cancelled_at
Revises: 0074_staff_event_defaults
Create Date: 2026-08-22

## このマイグレーションの責務

正典設計書 ``docs/plans/week-cockpit-design.md`` D2 のデータ層。
固定イベント (朝会など) を「今週だけ外す」ための取消印 1 列。

* **行は消さない**: 削除すると ``expand_staff_event_defaults`` の冪等キー
  (source='fixed' × external_id='{default_id}:{YYYY-MM-DD}') が空き、次の
  週生成で復活してしまう。行を残したまま ``cancelled_at`` を立てることで
  展開は skip されたままになる (= 今週だけ外れる)。
* 盤面 / ``GET /staff/{id}/events`` は cancelled 行も返す (FE が打消線)。
* ``events_outbound.build_outbound_plan`` / Layer3 の blocking・重なり判定 /
  提案エンジンのイベント収集は ``cancelled_at IS NULL`` のみを見る。

## downgrade

列 drop のみ (取消印は失われ、全イベントが有効に戻る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0075_staff_events_cancelled_at"
down_revision: str | Sequence[str] | None = "0074_staff_event_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_events",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("staff_events", "cancelled_at")
