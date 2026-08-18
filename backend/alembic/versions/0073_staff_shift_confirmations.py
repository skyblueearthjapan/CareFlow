"""staff_shift_confirmations (月次出勤カレンダー確定) 追加.

Revision ID: 0073_staff_shift_confirmations
Revises: 0072_generalize_accompaniments
Create Date: 2026-08-18

## このマイグレーションの責務

正典設計書 ``docs/plans/staff-shift-confirmation-design.md`` §1 のデータ層。
「このスタッフのこの月の出勤/休みを確定として本人へ通知した」記録 1 テーブル。

* month = 月初日 (YYYY-MM-01)。day==1 の検証は API 層 (DB CHECK は貼らない)。
* (staff_id, month) UNIQUE — 再確定は同一行の confirmed_at/confirmed_by 更新
  + 再通知で表現する (確定履歴は持たない)。
* PC「スタッフ休み・月確定」画面の確定ボタンが upsert し、モバイル
  「出勤カレンダー」が確定バッジ表示に読む。

## downgrade

テーブル drop のみ (確定の事実が失われるだけで業務データは無傷)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0073_staff_shift_confirmations"
down_revision: str | Sequence[str] | None = "0072_generalize_accompaniments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()

    op.create_table(
        "staff_shift_confirmations",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "month",
            sa.Date(),
            nullable=False,
            comment="確定対象の月 (月初日 YYYY-MM-01)",
        ),
        sa.Column(
            "confirmed_by",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.UniqueConstraint("staff_id", "month", name="uq_staff_shift_confirmation_month"),
    )


def downgrade() -> None:
    op.drop_table("staff_shift_confirmations")
