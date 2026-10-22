"""患者様の住所を 2 つ持つ (保険証の住所・実際の訪問先)。

Revision ID: 0100_patient_insurance_address
Revises: 0099_leave_and_kinmu
Create Date: 2026-10-22

正典: ``docs/plans/patient-two-addresses-design-2026-10-22.md`` §3・§4-3。

* ``patients.insurance_address`` VARCHAR(255) NULL — 保険証の住所 (レセプト・カイポケと同じ)。
  座標は持たない。
* ``patients.visit_same_as_insurance`` BOOLEAN NOT NULL DEFAULT true — 実際の訪問先は
  保険証の住所と同じか。
* 既存の ``address`` / ``lat`` / ``lng`` は「実際の訪問先」の意味になる (列は変えない)。

データ: 全員 ``insurance_address = NULLIF(TRIM(address), '')``・``visit_same_as_insurance = true``
(見た目と動きは何も変わらない)。``address`` / ``lat`` / ``lng`` は触らない。

downgrade は 2 列を drop する (``address`` は元のまま)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0100_patient_insurance_address"
down_revision: str | Sequence[str] | None = "0099_leave_and_kinmu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("patients", sa.Column("insurance_address", sa.String(255), nullable=True))
    op.add_column(
        "patients",
        sa.Column(
            "visit_same_as_insurance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.execute(
        "UPDATE patients SET insurance_address = NULLIF(TRIM(address), ''), "
        "visit_same_as_insurance = true"
    )


def downgrade() -> None:
    op.drop_column("patients", "visit_same_as_insurance")
    op.drop_column("patients", "insurance_address")
