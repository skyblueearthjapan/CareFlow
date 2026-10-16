"""visit_checkins に (patient_id, scanned_at DESC) の index を足す。

Revision ID: 0097_checkin_patient_idx
Revises: 0096_qr_grace
Create Date: 2026-10-17

登録の位置のずれ (``docs/plans/location-drift-align-2026-10-17.md`` §3-4):
患者ごとに直近 60 日の QR 打刻を新しい順に集める (訪問モニターはその日の患者分を
1 回でまとめて引く)。既存の index は visit / staff 起点だけなので、患者起点を足す。

downgrade は index を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0097_checkin_patient_idx"
down_revision: str | Sequence[str] | None = "0096_qr_grace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_visit_checkins_patient_scanned",
        "visit_checkins",
        ["patient_id", sa.text("scanned_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_visit_checkins_patient_scanned", table_name="visit_checkins")
