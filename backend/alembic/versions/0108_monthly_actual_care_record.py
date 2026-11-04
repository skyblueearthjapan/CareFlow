"""月の実績の行に、記録書Ⅱをそろえた結果の列.

Revision ID: 0108_monthly_actual_care_record
Revises: 0107_task_hold_first_visitor
Create Date: 2026-11-04

正典: ``docs/plans/kaipoke-kiroku2-time-design-2026-11-04.md`` §2-2・§3 の 3〜4 (PO 決定 2026-11-04)。
らく助の QR の実績をカイポケの実績へ反映するとき、同じ流れで記録書Ⅱの「実際の訪問時間」も
そろえる。結果は実績と別に行ごとに持つ。

* ``monthly_actual_rows.care_record_status`` (RPA の care_record_status・queued/unknown を含む)
* ``monthly_actual_rows.care_record_reason``
* ``monthly_actual_rows.care_record_message``
* ``monthly_actual_rows.care_record_dry_run``
* ``monthly_actual_rows.care_record_at``

すべて NULL 可 (既存の行は NULL = 記録書Ⅱはまだ送っていない)。downgrade は列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0108_monthly_actual_care_record"
down_revision: str | Sequence[str] | None = "0107_task_hold_first_visitor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monthly_actual_rows") as batch:
        batch.add_column(sa.Column("care_record_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("care_record_reason", sa.String(40), nullable=True))
        batch.add_column(sa.Column("care_record_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("care_record_dry_run", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("care_record_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("monthly_actual_rows") as batch:
        batch.drop_column("care_record_at")
        batch.drop_column("care_record_dry_run")
        batch.drop_column("care_record_message")
        batch.drop_column("care_record_reason")
        batch.drop_column("care_record_status")
