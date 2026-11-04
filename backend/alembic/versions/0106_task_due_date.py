"""やることの期日 (任意) と、毎月のやることの「毎月何日まで」.

Revision ID: 0106_task_due_date
Revises: 0105_recurring_tasks
Create Date: 2026-11-04

正典: ``docs/plans/patient-tasks-due-design-2026-11-04.md`` §3 (PO 決定 2026-11-04)。

* ``patient_tasks.due_date`` (NULL 可): いつまでに。NULL は期日なし。
* ``recurring_task_rules.due_day`` (NULL 可): 毎月何日まで。1〜28 日、0 は末日。
  CHECK で範囲の外を弾く。

既存の行は NULL (期日なし) のまま。downgrade は列 2 つを drop する (期日は失われる)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0106_task_due_date"
down_revision: str | Sequence[str] | None = "0105_recurring_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("patient_tasks") as batch:
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
    with op.batch_alter_table("recurring_task_rules") as batch:
        batch.add_column(sa.Column("due_day", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_recurring_task_rules_due_day_range"),
            "due_day IS NULL OR due_day BETWEEN 0 AND 28",
        )


def downgrade() -> None:
    with op.batch_alter_table("recurring_task_rules") as batch:
        batch.drop_constraint(op.f("ck_recurring_task_rules_due_day_range"), type_="check")
        batch.drop_column("due_day")
    with op.batch_alter_table("patient_tasks") as batch:
        batch.drop_column("due_date")
