"""P4: acceptance_calendar_week テーブル新設 (週別 受入上書き).

Revision ID: 0040_acceptance_calendar_week
Revises: 0039_scheduling_settings
Create Date: 2026-06-21

## このマイグレーションの責務

受け入れ枠マトリックスの「週別上書き」層 ``acceptance_calendar_week`` を新設する。
常設層 (``acceptance_calendar``, 毎週共通) はそのまま残し、特定週だけの手動上書きを
本テーブルが保持する (additive。既存テーブルは無改変、backfill 不要)。

- (office_id, iso_year, iso_week, weekday, time_slot) UNIQUE
- status IN ('available','consult','unavailable')
- iso_week 1..53 / weekday 0..6 の CHECK

## SQLite 互換

postgresql.UUID → sa.String(36) フォールバック分岐 (0019/0039 のパターン参考)。

## downgrade

acceptance_calendar_week テーブルを drop する (週別上書きは失われる)。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0040_acceptance_calendar_week"
down_revision: Union[str, Sequence[str], None] = "0039_scheduling_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    server_now = sa.func.now() if is_pg else sa.func.current_timestamp()

    op.create_table(
        "acceptance_calendar_week",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column(
            "office_id",
            uuid_type,
            sa.ForeignKey(
                "offices.id",
                ondelete="CASCADE",
                name="fk_acceptance_calendar_week_office_id_offices",
            ),
            nullable=False,
        ),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.SmallInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("time_slot", sa.Time(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.UniqueConstraint(
            "office_id",
            "iso_year",
            "iso_week",
            "weekday",
            "time_slot",
            name="uq_acceptance_calendar_week_key",
        ),
        sa.CheckConstraint(
            "iso_week >= 1 AND iso_week <= 53",
            name="ck_acceptance_calendar_week_iso_week",
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_acceptance_calendar_week_weekday",
        ),
        sa.CheckConstraint(
            "status IN ('available','consult','unavailable')",
            name="ck_acceptance_calendar_week_status",
        ),
    )
    op.create_index(
        "ix_acceptance_calendar_week_office_week",
        "acceptance_calendar_week",
        ["office_id", "iso_year", "iso_week"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_acceptance_calendar_week_office_week",
        table_name="acceptance_calendar_week",
    )
    op.drop_table("acceptance_calendar_week")
