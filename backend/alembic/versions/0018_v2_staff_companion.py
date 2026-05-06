"""W10-BE1: staff_companion_assignments + is_trainee + 旧 mentor 廃止.

Revision ID: 0018_v2_staff_companion
Revises: 0017_v2_patient_fixed_visits
Create Date: 2026-05-06

## このマイグレーションの責務

1. staff.is_trainee 列追加 (Boolean, NOT NULL, DEFAULT false)
2. staff_companion_assignments テーブル新設
   - trainee_staff_id / weekday / part / companion_staff_id
   - weekday: 0=Mon ... 6=Sun
   - part: 'am' / 'pm' / 'full'
   - UNIQUE (trainee_staff_id, weekday, part)
   - CHECK (weekday BETWEEN 0 AND 6)
   - CHECK (part IN ('am','pm','full'))
   - CHECK (trainee_staff_id != companion_staff_id)
3. 旧 mentor_assignments テーブル削除
4. staff.mentor_id 列削除

## SQLite 互換

postgresql.UUID → sa.String(36) のフォールバック分岐 (0017 のパターン参考)

## Alembic chain

down_revision = "0017_v2_patient_fixed_visits"
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_v2_staff_companion"
down_revision: Union[str, Sequence[str], None] = "0017_v2_patient_fixed_visits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1) staff.is_trainee 列追加
    op.add_column(
        "staff",
        sa.Column(
            "is_trainee",
            sa.Boolean(),
            nullable=False,
            server_default="false" if is_pg else "0",
        ),
    )

    # 2) staff_companion_assignments テーブル新設
    if is_pg:
        op.create_table(
            "staff_companion_assignments",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "trainee_staff_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("part", sa.String(8), nullable=False),
            sa.Column(
                "companion_staff_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
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
            sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_sca_weekday"),
            sa.CheckConstraint("part IN ('am','pm','full')", name="ck_sca_part"),
            sa.CheckConstraint(
                "trainee_staff_id != companion_staff_id", name="ck_sca_self_companion"
            ),
        )
    else:
        # SQLite (テスト) フォールバック: UUID → String(36)
        op.create_table(
            "staff_companion_assignments",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column(
                "trainee_staff_id",
                sa.String(36),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("part", sa.String(8), nullable=False),
            sa.Column(
                "companion_staff_id",
                sa.String(36),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
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
            sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_sca_weekday"),
            sa.CheckConstraint("part IN ('am','pm','full')", name="ck_sca_part"),
            sa.CheckConstraint(
                "trainee_staff_id != companion_staff_id", name="ck_sca_self_companion"
            ),
        )

    op.create_unique_constraint(
        "uq_sca_trainee_weekday_part",
        "staff_companion_assignments",
        ["trainee_staff_id", "weekday", "part"],
    )
    op.create_index(
        "ix_sca_trainee",
        "staff_companion_assignments",
        ["trainee_staff_id"],
    )
    op.create_index(
        "ix_sca_companion",
        "staff_companion_assignments",
        ["companion_staff_id"],
    )

    # 3) 旧 mentor_assignments テーブル削除
    op.drop_table("mentor_assignments")

    # 4) staff.mentor_id 列削除
    op.drop_column("staff", "mentor_id")


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Reverse: restore mentor_id on staff
    op.add_column(
        "staff",
        sa.Column(
            "mentor_id",
            postgresql.UUID(as_uuid=True) if is_pg else sa.String(36),
            sa.ForeignKey("staff.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Restore mentor_assignments table
    if is_pg:
        op.create_table(
            "mentor_assignments",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "mentor_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "mentee_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.UniqueConstraint(
                "mentor_id", "mentee_id", "start_date", name="uq_mentor_pair_start"
            ),
        )
    else:
        op.create_table(
            "mentor_assignments",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column(
                "mentor_id",
                sa.String(36),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "mentee_id",
                sa.String(36),
                sa.ForeignKey("staff.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=True),
        )

    # Drop companion assignments
    op.drop_index("ix_sca_companion", table_name="staff_companion_assignments")
    op.drop_index("ix_sca_trainee", table_name="staff_companion_assignments")
    op.drop_constraint(
        "uq_sca_trainee_weekday_part",
        "staff_companion_assignments",
        type_="unique",
    )
    op.drop_table("staff_companion_assignments")

    # Drop is_trainee
    op.drop_column("staff", "is_trainee")
