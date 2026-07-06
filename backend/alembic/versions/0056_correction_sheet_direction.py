"""correction_sheets: direction (outbound/inbound) + week range (R-1 逆反映).

カイポケ→CareFlow 逆反映 (docs/plans/kaipoke-reverse-sync-design.md):
  * direction — 'outbound' (CareFlow→カイポケ・既存) / 'inbound' (カイポケ→CareFlow・新設)
  * week_start / week_end — 差分を計算した週レンジ。apply実績ゲート
    (「実applyした週だけ取り込み可」) の判定材料として以後のシートに刻む。

Revision ID: 0056_correction_sheet_direction
Revises: 0055_drop_ai_interpret
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_correction_sheet_direction"
down_revision: Union[str, Sequence[str], None] = "0055_drop_ai_interpret"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "correction_sheets",
        sa.Column(
            "direction",
            sa.String(length=8),
            nullable=False,
            server_default="outbound",
        ),
    )
    op.add_column("correction_sheets", sa.Column("week_start", sa.Date(), nullable=True))
    op.add_column("correction_sheets", sa.Column("week_end", sa.Date(), nullable=True))
    op.create_index(
        "ix_correction_sheets_direction_week",
        "correction_sheets",
        ["direction", "week_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_correction_sheets_direction_week", table_name="correction_sheets")
    op.drop_column("correction_sheets", "week_end")
    op.drop_column("correction_sheets", "week_start")
    op.drop_column("correction_sheets", "direction")
