"""Wave U-3: 操作ジャーナル schedule_op_log テーブル新設.

Revision ID: 0052_schedule_op_log
Revises: 0051_l3_fix_primary_staff_flag_seed
Create Date: 2026-07-04

## このマイグレーションの責務 (Wave U-3)

undo/redo 基盤となる ``schedule_op_log`` テーブルを新設する。

### カラム

- id         UUID PK (PG: gen_random_uuid(), SQLite: Python uuid4)
- user_id    UUID NOT NULL — 操作者（FKなし・ユーザー削除時も記録保持）
- iso_year   INTEGER NOT NULL — 対象 ISO 年
- iso_week   INTEGER NOT NULL — 対象 ISO 週
- op_group_id UUID NOT NULL — 1ユーザー操作 = 1グループ
- op_kind    VARCHAR(64) NOT NULL — 操作種別
- label      VARCHAR(256) NOT NULL — 日本語ラベル
- forward_payload JSONB NOT NULL DEFAULT '{}'
- inverse_payload JSONB NOT NULL DEFAULT '{}'
- undone     BOOLEAN NOT NULL DEFAULT false
- created_at TIMESTAMPTZ NOT NULL DEFAULT now()

### インデックス

- ix_schedule_op_log_user_year_week_created ON (user_id, iso_year, iso_week, created_at)

### v1 記録対象

a. POST /schedule/place-and-fix (fix_pattern=False のみ)
b. DELETE /visits/{id}
c. PATCH /courses/{id} assigned_staff_id 変更
d. POST /v2/visit-move-week-only

### v2 後続（記録しない）

PUT fixed-visits / apply 系 などの PFV 系操作（スナップショット undo は v2 以降）

## PG / SQLite 互換

* UUID は PG: postgresql.UUID(as_uuid=True) + gen_random_uuid() サーバーデフォルト /
  SQLite: String(36) + Python uuid4。
* JSONB は PG: postgresql.JSONB / SQLite: sa.JSON。
* BOOLEAN は両方で動作する sa.Boolean。
* TIMESTAMPTZ は DateTime(timezone=True) + CURRENT_TIMESTAMP で両方動作。

## downgrade

DROP TABLE schedule_op_log (インデックスはテーブル削除で消える)。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0052_schedule_op_log"
down_revision: Union[str, Sequence[str], None] = "0051_l3_fix_primary_staff_flag_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        uuid_type: sa.types.TypeEngine = postgresql.UUID(as_uuid=True)
        id_col = sa.Column(
            "id",
            uuid_type,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        )
        jsonb_type = postgresql.JSONB(astext_type=sa.Text())
        now_default = sa.func.now()
    else:
        uuid_type = sa.String(36)
        id_col = sa.Column("id", uuid_type, primary_key=True, default=lambda: str(uuid.uuid4()))
        jsonb_type = sa.JSON()
        now_default = sa.func.current_timestamp()

    op.create_table(
        "schedule_op_log",
        id_col,
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("op_group_id", uuid_type, nullable=False),
        sa.Column("op_kind", sa.String(64), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column(
            "forward_payload",
            jsonb_type,
            nullable=False,
            server_default=sa.text("'{}'") if is_pg else sa.text("'{}'"),
        ),
        sa.Column(
            "inverse_payload",
            jsonb_type,
            nullable=False,
            server_default=sa.text("'{}'") if is_pg else sa.text("'{}'"),
        ),
        sa.Column(
            "undone",
            sa.Boolean(),
            nullable=False,
            default=False,
            server_default=sa.text("false") if is_pg else sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
    )

    op.create_index(
        "ix_schedule_op_log_user_year_week_created",
        "schedule_op_log",
        ["user_id", "iso_year", "iso_week", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_op_log_user_year_week_created",
        table_name="schedule_op_log",
    )
    op.drop_table("schedule_op_log")
