"""W9-BE1: patient_fixed_visits テーブル追加 (週間訪問パターン固定枠).

Revision ID: 0017_v2_patient_fixed_visits
Revises: 0016_v2_pending_requests_applied_at
Create Date: 2026-05-06

患者マスタに「週間訪問パターン (固定)」を持たせる Phase 1 (BE データ層)。

## このマイグレーションの責務

- ``patient_fixed_visits`` テーブル新設
- mode: 'normal' (通常) / 'special' (特別週) の 2 セット
- UNIQUE 制約: (patient_id, mode, weekday) — 同一患者×同一 mode×同一曜日は 1 訪問のみ
- CHECK 制約: mode 値域, weekday 0-6, duration_min 1-480
- インデックス: ix_pfv_patient_id, ix_pfv_patient_mode (patient_id + mode 複合)

## SQLite 互換

PostgreSQL 以外では postgresql.UUID を sa.String(36) に置き換える
(0013_v2_pending_requests.py のパターンと同じ分岐)。

## Alembic chain

down_revision = "0016_v2_pending_requests_applied_at"
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0017_v2_patient_fixed_visits"
down_revision: Union[str, Sequence[str], None] = "0016_v2_pending_requests_applied_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.create_table(
            "patient_fixed_visits",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "patient_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("patients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column(
                "duration_min",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "mode IN ('normal','special')",
                name="ck_pfv_mode",
            ),
            sa.CheckConstraint(
                "weekday BETWEEN 0 AND 6",
                name="ck_pfv_weekday",
            ),
            sa.CheckConstraint(
                "duration_min > 0 AND duration_min <= 480",
                name="ck_pfv_duration",
            ),
        )
    else:
        # SQLite (テスト) フォールバック: UUID → String(36).
        op.create_table(
            "patient_fixed_visits",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column(
                "patient_id",
                sa.String(36),
                sa.ForeignKey("patients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column(
                "duration_min",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
        )

    op.create_unique_constraint(
        "uq_pfv_patient_mode_weekday",
        "patient_fixed_visits",
        ["patient_id", "mode", "weekday"],
    )
    op.create_index(
        "ix_pfv_patient_id",
        "patient_fixed_visits",
        ["patient_id"],
    )
    op.create_index(
        "ix_pfv_patient_mode",
        "patient_fixed_visits",
        ["patient_id", "mode"],
    )


def downgrade() -> None:
    op.drop_index("ix_pfv_patient_mode", table_name="patient_fixed_visits")
    op.drop_index("ix_pfv_patient_id", table_name="patient_fixed_visits")
    op.drop_constraint(
        "uq_pfv_patient_mode_weekday",
        "patient_fixed_visits",
        type_="unique",
    )
    op.drop_table("patient_fixed_visits")
