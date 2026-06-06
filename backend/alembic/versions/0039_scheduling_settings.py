"""Phase G-88 Step2: scheduling_settings テーブル新設 (最適化設定の箱).

Revision ID: 0039_scheduling_settings
Revises: 0038_office_operating_weekdays
Create Date: 2026-06-06

## このマイグレーションの責務

自動最適化ルールの事業所別設定 (事業所 = DB 単位の単一行) を保持する
``scheduling_settings`` テーブルを新設する.

* シングルトン: ``is_singleton`` 部分 UNIQUE 制約で ``is_singleton = true`` の行を
  1 行のみに制限する (= 事業所 = DB 単位の単一設定).
* 各設定列は **nullable** (NULL = コード既定値 fallback). 既定値の合成は
  ``services.scheduling.config.build_scheduling_config`` が行う.
* CHECK 制約で範囲を担保:
    - visit_buffer_min     : 0..30
    - travel_speed_kmh     : 10..40 (Numeric(4,1))
    - lunch_duration_min   : 30..90
    - max_patients_per_course : 1..10
    - lunch_window_start < lunch_window_end (両方非 NULL 時)
    - business_start      < business_end       (両方非 NULL 時)

⚠️ この Step では最適化挙動は変えない (Step3 で SchedulingConfig を注入).
本テーブルは設定値を保存・読み出す箱にすぎない. シード行は作成しない
(行が無ければローダーが全既定を返す = 挙動不変).

## SQLite 互換

* PG: ``postgresql.UUID``, ``server_default=gen_random_uuid()``,
  ``postgresql_where`` 付き部分 UNIQUE index.
* SQLite: ``String(36)``, ``sqlite_where`` 付き部分 UNIQUE index.

## downgrade

* scheduling_settings テーブルを drop する (保存済み設定値は失われる).
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0039_scheduling_settings"
down_revision: Union[str, Sequence[str], None] = "0038_office_operating_weekdays"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CHECK_CONSTRAINTS: list[sa.schema.SchemaItem] = [
    sa.CheckConstraint(
        "visit_buffer_min IS NULL OR (visit_buffer_min >= 0 AND visit_buffer_min <= 30)",
        name="ck_scheduling_settings_buffer_range",
    ),
    sa.CheckConstraint(
        "travel_speed_kmh IS NULL OR (travel_speed_kmh >= 10 AND travel_speed_kmh <= 40)",
        name="ck_scheduling_settings_speed_range",
    ),
    sa.CheckConstraint(
        "lunch_duration_min IS NULL OR (lunch_duration_min >= 30 AND lunch_duration_min <= 90)",
        name="ck_scheduling_settings_lunch_duration_range",
    ),
    sa.CheckConstraint(
        "max_patients_per_course IS NULL "
        "OR (max_patients_per_course >= 1 AND max_patients_per_course <= 10)",
        name="ck_scheduling_settings_capacity_range",
    ),
    sa.CheckConstraint(
        "lunch_window_start IS NULL OR lunch_window_end IS NULL "
        "OR lunch_window_start < lunch_window_end",
        name="ck_scheduling_settings_lunch_window_order",
    ),
    sa.CheckConstraint(
        "business_start IS NULL OR business_end IS NULL OR business_start < business_end",
        name="ck_scheduling_settings_business_hours_order",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        uuid_type: sa.types.TypeEngine = postgresql.UUID(as_uuid=True)
        id_server_default = sa.text("gen_random_uuid()")
        now_default = sa.func.now()
        singleton_default = sa.text("true")
    else:
        uuid_type = sa.String(36)
        id_server_default = None
        now_default = sa.func.current_timestamp()
        singleton_default = sa.text("1")

    id_column = (
        sa.Column("id", uuid_type, primary_key=True, server_default=id_server_default)
        if is_pg
        else sa.Column("id", uuid_type, primary_key=True)
    )

    op.create_table(
        "scheduling_settings",
        id_column,
        sa.Column(
            "is_singleton",
            sa.Boolean(),
            nullable=False,
            server_default=singleton_default,
        ),
        sa.Column("visit_buffer_min", sa.Integer(), nullable=True),
        sa.Column("travel_speed_kmh", sa.Numeric(4, 1), nullable=True),
        sa.Column("lunch_duration_min", sa.Integer(), nullable=True),
        sa.Column("lunch_window_start", sa.Time(), nullable=True),
        sa.Column("lunch_window_end", sa.Time(), nullable=True),
        sa.Column("business_start", sa.Time(), nullable=True),
        sa.Column("business_end", sa.Time(), nullable=True),
        sa.Column("max_patients_per_course", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        *_CHECK_CONSTRAINTS,
    )

    # シングルトン部分 UNIQUE: is_singleton = true の行は 1 行のみ.
    if is_pg:
        op.create_index(
            "uq_scheduling_settings_singleton",
            "scheduling_settings",
            ["is_singleton"],
            unique=True,
            postgresql_where=sa.text("is_singleton = true"),
        )
    else:
        op.create_index(
            "uq_scheduling_settings_singleton",
            "scheduling_settings",
            ["is_singleton"],
            unique=True,
            sqlite_where=sa.text("is_singleton = 1"),
        )


def downgrade() -> None:
    """注意: 本番でこの migration を巻き戻すと保存済み最適化設定は失われる.

    本番運用では downgrade 前に必ず ``pg_dump`` で scheduling_settings の
    バックアップを取得すること.
    """
    op.drop_index("uq_scheduling_settings_singleton", table_name="scheduling_settings")
    op.drop_table("scheduling_settings")
