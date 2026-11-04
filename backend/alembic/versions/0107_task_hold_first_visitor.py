"""やることの項目に「最初に訪問した看護師が預かる」の印.

Revision ID: 0107_task_hold_first_visitor
Revises: 0106_task_due_date
Create Date: 2026-11-04

正典: ``docs/plans/patient-tasks-hold-first-visitor-design-2026-11-04.md`` §4 (PO 決定 2026-11-04)。

* ``task_item_types.hold_by_first_visitor`` (NOT NULL・既定 false): 印の付いた項目の
  やることは、最初の訪問で済まなかったとき、その訪問の主担当が預かる
  (ほかの訪問へは持ち越さない)。預かる人は保存せず、毎回の計算で決める。

既存の項目は false (今までどおりの持ち越し) のまま。downgrade は列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0107_task_hold_first_visitor"
down_revision: str | Sequence[str] | None = "0106_task_due_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_item_types") as batch:
        batch.add_column(
            sa.Column(
                "hold_by_first_visitor",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("task_item_types") as batch:
        batch.drop_column("hold_by_first_visitor")
