"""offices.sort_order / offices.short_label — 拠点マスタ駆動化 (PO決定「コードが事業所を特定しない」).

現場ボードの表示順 (FieldBoard OFFICE_ORDER) と拠点付きコーストークンの短縮名
(patient_excel OFFICE_SHORT_TO_CODE) をコード直書きから offices マスタ駆動へ移す。

  - offices.sort_order (Integer, nullable): 表示順。NULL は名前順で末尾。
  - offices.short_label (String(8), nullable): 短縮バッジ。NULL は name 先頭 1 文字。

データ backfill: name='稲毛' → sort_order=1, short_label='稲' /
name='都賀' → sort_order=2, short_label='津' (存在すれば。無くてもエラーにしない)。

Revision ID: 0059_office_sort_order_short_label
Revises: 0058_kaipoke_credentials
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_office_sort_order_short_label"
down_revision: Union[str, Sequence[str], None] = "0058_kaipoke_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("offices", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.add_column("offices", sa.Column("short_label", sa.String(length=8), nullable=True))

    # データ backfill (存在すれば設定。無くてもエラーにしない = 条件付き UPDATE).
    offices = sa.table(
        "offices",
        sa.column("name", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("short_label", sa.String()),
    )
    op.execute(
        offices.update()
        .where(offices.c.name == "稲毛")
        .values(sort_order=1, short_label="稲")
    )
    op.execute(
        offices.update()
        .where(offices.c.name == "都賀")
        .values(sort_order=2, short_label="津")
    )


def downgrade() -> None:
    op.drop_column("offices", "short_label")
    op.drop_column("offices", "sort_order")
