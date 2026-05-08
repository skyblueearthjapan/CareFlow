"""W37 Phase 1: 複数スタッフ対応 — slot_index 列 + visits partial UNIQUE 拡張.

Revision ID: 0027_v2_multi_staff_slot_index
Revises: 0026_v2_visits_unique_pds
Create Date: 2026-05-09

## このマイグレーションの責務 (W37 Phase 1)

「同一患者・同曜日・同時刻に 2 コースを固定する」ためのデータモデル基盤を整える.

(a) ``patient_fixed_visits``
  - 列追加: ``slot_index SMALLINT NOT NULL DEFAULT 0`` (CHECK 0 <= slot_index <= 1)
  - 既存 UNIQUE ``(patient_id, mode, weekday)`` を DROP し,
    新 UNIQUE ``(patient_id, mode, weekday, slot_index)`` を CREATE.
  - 既存全行は default 値の slot_index=0 に正規化される (案 P).

(b) ``visits`` partial UNIQUE 改修
  - 0026 で追加した partial UNIQUE INDEX
    ``uq_visits_pds_active`` (patient_id, visit_date, start_time)
    WHERE deleted_at IS NULL を DROP する.
  - 新 partial UNIQUE INDEX
    ``uq_visits_pds_group_active``
    (patient_id, visit_date, start_time,
     COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE deleted_at IS NULL を CREATE する.
  - これにより 2 名対応の partner visit (visit_group_id が 2 行で同一 / 一致しない)
    では同時刻 2 行を許容しつつ, 通常運用 (visit_group_id = NULL) では
    同時刻 2 行は引き続き禁止される.

(c) downgrade は対称に書く.

## SQLite 互換 (テスト)

- COALESCE を含む partial UNIQUE INDEX も SQLite 3.8.0+ でサポートされる.
- ただし SQLite には ``::uuid`` cast がないので, 方言ごとに分岐する:
  - PostgreSQL: ``COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000'::uuid)``
  - SQLite (テスト): ``COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000')``

## Alembic chain

- down_revision = "0026_v2_visits_unique_pds"
- 単一 head: ``0027_v2_multi_staff_slot_index``
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0027_v2_multi_staff_slot_index"
down_revision: Union[str, Sequence[str], None] = "0026_v2_visits_unique_pds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """(a) patient_fixed_visits.slot_index 追加 + UNIQUE 差し替え,
    (b) visits partial UNIQUE を visit_group_id 込みに拡張.

    ## 方言別の事情

    SQLite は ALTER TABLE で CHECK / UNIQUE を直接追加/削除できないため,
    PostgreSQL では通常の op.* / SQLite では op.batch_alter_table (copy-and-move)
    に分岐する.
    """
    # ----- (a) patient_fixed_visits.slot_index 列追加 + UNIQUE 差し替え -----
    if _is_postgres():
        op.add_column(
            "patient_fixed_visits",
            sa.Column(
                "slot_index",
                sa.SmallInteger(),
                nullable=False,
                server_default="0",
            ),
        )
        op.create_check_constraint(
            "ck_pfv_slot_index",
            "patient_fixed_visits",
            "slot_index >= 0 AND slot_index <= 1",
        )
        op.drop_constraint(
            "uq_pfv_patient_mode_weekday",
            "patient_fixed_visits",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_pfv_patient_mode_weekday_slot",
            "patient_fixed_visits",
            ["patient_id", "mode", "weekday", "slot_index"],
        )
    else:
        # SQLite: batch (copy-and-move) で ALTER を再構築する.
        # 既存 UNIQUE 制約を copy_from で明示するために最小 Table を組む.
        legacy_table = sa.Table(
            "patient_fixed_visits",
            sa.MetaData(),
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("patient_id", sa.String(36), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column(
                "duration_min",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column("course_template_id", sa.String(36), nullable=True),
            sa.UniqueConstraint(
                "patient_id",
                "mode",
                "weekday",
                name="uq_pfv_patient_mode_weekday",
            ),
        )
        with op.batch_alter_table(
            "patient_fixed_visits",
            copy_from=legacy_table,
        ) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "slot_index",
                    sa.SmallInteger(),
                    nullable=False,
                    server_default="0",
                )
            )
            batch_op.drop_constraint(
                "uq_pfv_patient_mode_weekday",
                type_="unique",
            )
            batch_op.create_unique_constraint(
                "uq_pfv_patient_mode_weekday_slot",
                ["patient_id", "mode", "weekday", "slot_index"],
            )
            batch_op.create_check_constraint(
                "ck_pfv_slot_index",
                "slot_index >= 0 AND slot_index <= 1",
            )

    # ----- (b) visits partial UNIQUE 改修 -----
    # 0026 で追加した index を DROP
    op.execute("DROP INDEX IF EXISTS uq_visits_pds_active")

    # 方言別に COALESCE 式を変えて新 partial UNIQUE INDEX を作成
    if _is_postgres():
        op.execute(
            f"""
            CREATE UNIQUE INDEX uq_visits_pds_group_active
            ON visits (
                patient_id,
                visit_date,
                start_time,
                COALESCE(visit_group_id, '{_NIL_UUID}'::uuid)
            )
            WHERE deleted_at IS NULL
            """
        )
    else:
        # SQLite (テスト): visit_group_id は TEXT(36) として扱われるので cast 不要
        op.execute(
            f"""
            CREATE UNIQUE INDEX uq_visits_pds_group_active
            ON visits (
                patient_id,
                visit_date,
                start_time,
                COALESCE(visit_group_id, '{_NIL_UUID}')
            )
            WHERE deleted_at IS NULL
            """
        )


def downgrade() -> None:
    """upgrade と対称: visits index を 0026 形に戻し,
    patient_fixed_visits の UNIQUE / 列を元に戻す.
    """
    # ----- (b) visits partial UNIQUE を 0026 の形に戻す -----
    op.execute("DROP INDEX IF EXISTS uq_visits_pds_group_active")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visits_pds_active
        ON visits (patient_id, visit_date, start_time)
        WHERE deleted_at IS NULL
        """
    )

    # ----- (a) patient_fixed_visits の UNIQUE / 列 / CHECK を元に戻す -----
    if _is_postgres():
        op.drop_constraint(
            "uq_pfv_patient_mode_weekday_slot",
            "patient_fixed_visits",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_pfv_patient_mode_weekday",
            "patient_fixed_visits",
            ["patient_id", "mode", "weekday"],
        )
        op.drop_constraint(
            "ck_pfv_slot_index",
            "patient_fixed_visits",
            type_="check",
        )
        op.drop_column("patient_fixed_visits", "slot_index")
    else:
        # SQLite: batch で再構築. upgrade 後の形 (slot_index + 新 UNIQUE + CHECK)
        # を copy_from に渡して, downgrade で 0026 状態に戻す.
        upgraded_table = sa.Table(
            "patient_fixed_visits",
            sa.MetaData(),
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("patient_id", sa.String(36), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column(
                "duration_min",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column("course_template_id", sa.String(36), nullable=True),
            sa.Column(
                "slot_index",
                sa.SmallInteger(),
                nullable=False,
                server_default="0",
            ),
            sa.UniqueConstraint(
                "patient_id",
                "mode",
                "weekday",
                "slot_index",
                name="uq_pfv_patient_mode_weekday_slot",
            ),
            sa.CheckConstraint(
                "slot_index >= 0 AND slot_index <= 1",
                name="ck_pfv_slot_index",
            ),
        )
        with op.batch_alter_table(
            "patient_fixed_visits",
            copy_from=upgraded_table,
        ) as batch_op:
            batch_op.drop_constraint("ck_pfv_slot_index", type_="check")
            batch_op.drop_constraint(
                "uq_pfv_patient_mode_weekday_slot",
                type_="unique",
            )
            batch_op.create_unique_constraint(
                "uq_pfv_patient_mode_weekday",
                ["patient_id", "mode", "weekday"],
            )
            batch_op.drop_column("slot_index")
