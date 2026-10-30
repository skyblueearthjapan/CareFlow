"""固定訪問の枠に「訪問する週」(毎回 / A の週 / B の週) の列を足す.

Revision ID: 0102_pfv_visit_week
Revises: 0101_voice_transcript_fixed
Create Date: 2026-10-30

正典: ``docs/plans/biweekly-monthly-design-2026-10-30.md`` §3-2 (PO 決定 2026-10-30)。

* ``patient_fixed_visits.visit_week`` VARCHAR(8) NOT NULL DEFAULT 'every'
  — ``'every'`` (毎回) / ``'A'`` (A の週) / ``'B'`` (B の週)。
  隔週 (``weekly_pattern.visit_frequency='biweekly'``) の患者のときだけ意味を持つ。
  A の週 = ``weekly_pattern.biweekly_anchor`` の週から 0・2・4… 週目。
* CHECK ``ck_pfv_visit_week`` で 3 値に限る。

データ: 既存の行はすべて 'every' (server_default)。今までと同じ動き。

downgrade は CHECK と列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0102_pfv_visit_week"
down_revision: str | Sequence[str] | None = "0101_voice_transcript_fixed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_pfv_visit_week"
_CHECK_COND = "visit_week IN ('every','A','B')"


def upgrade() -> None:
    op.add_column(
        "patient_fixed_visits",
        sa.Column(
            "visit_week",
            sa.String(length=8),
            nullable=False,
            server_default="every",
            comment="訪問する週: every=毎回 / A=A の週 / B=B の週 (隔週の患者だけ意味を持つ)",
        ),
    )
    # CHECK 制約: 3 値に拘束 (0047 ck_pfv_movability と同じ書き方).
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"ALTER TABLE patient_fixed_visits ADD CONSTRAINT {_CHECK_NAME} CHECK ({_CHECK_COND})"
        )
    else:
        # SQLite は ALTER TABLE ... ADD CONSTRAINT 不可のため batch 再構築.
        with op.batch_alter_table("patient_fixed_visits") as batch:
            batch.create_check_constraint(_CHECK_NAME, _CHECK_COND)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER TABLE patient_fixed_visits DROP CONSTRAINT IF EXISTS {_CHECK_NAME}")
        op.drop_column("patient_fixed_visits", "visit_week")
    else:
        with op.batch_alter_table("patient_fixed_visits") as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
            batch.drop_column("visit_week")
