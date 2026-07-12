"""Phase 2: 旧 staff_companion_assignments 撤去 (新人同行 v1.1 §3).

docs/plans/trainee-accompaniment-design.md v1.1 §3:
旧「曜日×午前/午後×同行者」機構 (向きが逆・新モデルと矛盾) を撤去する。
新モデル (trainee_accompaniments/defaults・mig 0060) が唯一の正典。

- upgrade  : staff_companion_assignments テーブルを DROP。
- downgrade: mig 0018 + 0019 相当の構造 (pair_role 込み) で再作成。

既存データは新モデルへ移行しない (向きが逆で機械変換不能・§12-4)。
staff.is_trainee 列は存続 (本機能の起点・§3)。

Revision ID: 0061_drop_staff_companion
Revises: 0060_trainee_accompaniment
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_drop_staff_companion"
down_revision: Union[str, Sequence[str], None] = "0060_trainee_accompaniment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # インデックス / UNIQUE を落としてからテーブル DROP (0018 downgrade と同順)。
    op.drop_index("ix_sca_companion", table_name="staff_companion_assignments")
    op.drop_index("ix_sca_trainee", table_name="staff_companion_assignments")
    op.drop_constraint(
        "uq_sca_trainee_weekday_part",
        "staff_companion_assignments",
        type_="unique",
    )
    op.drop_table("staff_companion_assignments")


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # mig 0018 upgrade + 0019 の pair_role を合成した構造で再作成。
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
            sa.Column("pair_role", sa.String(16), nullable=True),
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
            sa.CheckConstraint(
                "pair_role IS NULL OR pair_role IN ('primary','support')",
                name="ck_sca_pair_role",
            ),
        )
    else:
        # SQLite フォールバック: UUID → String(36)
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
            sa.Column("pair_role", sa.String(16), nullable=True),
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
            sa.CheckConstraint(
                "pair_role IS NULL OR pair_role IN ('primary','support')",
                name="ck_sca_pair_role",
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
