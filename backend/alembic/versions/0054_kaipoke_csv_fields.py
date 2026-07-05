"""K-1b カイポケ18列CSV生成用フィールド: staff.qualification / office.kaipoke_name / patient.kaipoke_service_content.

Revision ID: 0054_kaipoke_csv_fields
Revises: 0053_office_area_prompt_dismissals
Create Date: 2026-07-05

## このマイグレーションの責務 (K-1b)

CareFlow DB からカイポケ18列CSVを生成する (差分適用の心臓部) ために、
既存カラムから導出できない3つの値を保持する列を追加する
(設計書 `docs/plans/kaipoke-csv-generation-design.md` §2)。

### 追加カラム (すべて nullable・後方互換)

- staff.qualification            String(16)  — CSV「職種」列 (看護師/准看護師…)。role とは独立
- offices.kaipoke_name           String(120) — CSV「事業所名」列の正式名。NULL時は name で代替
- patients.kaipoke_service_content String(64) — CSV「サービス内容」列。NULL時は事業所既定へフォールバック

いずれも nullable のため既存行への影響なし・データ移行不要 (seed は別途)。

## downgrade

3列を DROP。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0054_kaipoke_csv_fields"
down_revision: Union[str, Sequence[str], None] = "0053_office_area_prompt_dismissals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("staff", sa.Column("qualification", sa.String(length=16), nullable=True))
    op.add_column("offices", sa.Column("kaipoke_name", sa.String(length=120), nullable=True))
    op.add_column(
        "patients",
        sa.Column("kaipoke_service_content", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("patients", "kaipoke_service_content")
    op.drop_column("offices", "kaipoke_name")
    op.drop_column("staff", "qualification")
