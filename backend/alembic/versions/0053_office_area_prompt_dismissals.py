"""W-7 地域ルールの学習: office_area_prompt_dismissals テーブル新設.

Revision ID: 0053_office_area_prompt_dismissals
Revises: 0052_schedule_op_log
Create Date: 2026-07-04

## このマイグレーションの責務 (W-7)

未カバー地域の「担当エリア登録呼びかけ」を組織として「今回だけ」で断った City を
記憶する ``office_area_prompt_dismissals`` テーブルを新設する
(設計書 `docs/plans/region-rule-learning-design.md` §3)。

### カラム

- id         UUID PK (PG: gen_random_uuid(), SQLite: Python uuid4)
- city_id    UUID NOT NULL — FK cities.id (ON DELETE CASCADE) / UNIQUE
             (組織全体で City ごとに 1 行)
- created_at TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約 (命名規約 = app/db/base.py NAMING_CONVENTION)

- pk_office_area_prompt_dismissals
- fk_office_area_prompt_dismissals_city_id_cities (ON DELETE CASCADE)
- uq_office_area_prompt_dismissals_city_id

## PG / SQLite 互換

* UUID は PG: postgresql.UUID(as_uuid=True) + gen_random_uuid() サーバーデフォルト /
  SQLite: String(36) + Python uuid4。
* TIMESTAMPTZ は DateTime(timezone=True) + now()/current_timestamp で両方動作。

## downgrade

DROP TABLE office_area_prompt_dismissals。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0053_office_area_prompt_dismissals"
down_revision: Union[str, Sequence[str], None] = "0052_schedule_op_log"
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
        now_default = sa.func.now()
    else:
        uuid_type = sa.String(36)
        id_col = sa.Column("id", uuid_type, primary_key=True, default=lambda: str(uuid.uuid4()))
        now_default = sa.func.current_timestamp()

    op.create_table(
        "office_area_prompt_dismissals",
        id_col,
        sa.Column("city_id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.ForeignKeyConstraint(
            ["city_id"],
            ["cities.id"],
            name="fk_office_area_prompt_dismissals_city_id_cities",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "city_id",
            name="uq_office_area_prompt_dismissals_city_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("office_area_prompt_dismissals")
