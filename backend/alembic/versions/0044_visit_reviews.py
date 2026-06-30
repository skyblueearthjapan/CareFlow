"""QR 訪問チェックイン Phase 5-3: visit_reviews テーブル.

Revision ID: 0044_visit_reviews
Revises: 0043_checkin_max_inprogress
Create Date: 2026-07-01

## このマイグレーションの責務

訪問モニターの要対応 (未訪問 / 場所違い / 要確認) を管理者が **visit 単位** で
「確認済み」にして要対応トレイから外すための新テーブル ``visit_reviews`` を新設
する (additive)。未訪問は紐づく visit_checkins が無いため checkin 単位ではクリア
できず、visit 単位の review が必須 (設計 §5-3 critic C1/C2)。

* ``visit_id`` は監査証跡として **ON DELETE RESTRICT** かつ **unique**
  (1 visit につき高々 1 行)。
* ``reviewed_by`` は確認者 (users)。SET NULL (人事異動でも記録は残す)。
* ``reviewed_at`` は確認時刻 (既定 now())、``comment`` は確認理由 (任意)。

## SQLite 互換

0042 と同じく dialect 分岐: PG = ``postgresql.UUID`` / ``gen_random_uuid()``、
SQLite = ``String(36)``。``now()`` は両 dialect の関数差を吸収する。

## downgrade

``visit_reviews`` を drop する。downgrade で確認済みマークは失われる。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0044_visit_reviews"
down_revision: Union[str, Sequence[str], None] = "0043_checkin_max_inprogress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type: sa.types.TypeEngine = (
        postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    )
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    id_server_default = sa.text("gen_random_uuid()") if is_pg else None

    id_column = (
        sa.Column("id", uuid_type, primary_key=True, server_default=id_server_default)
        if is_pg
        else sa.Column("id", uuid_type, primary_key=True)
    )
    op.create_table(
        "visit_reviews",
        id_column,
        sa.Column(
            "visit_id",
            uuid_type,
            sa.ForeignKey(
                "visits.id",
                ondelete="RESTRICT",
                name="fk_visit_reviews_visit_id_visits",
            ),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by",
            uuid_type,
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_visit_reviews_reviewed_by_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.UniqueConstraint("visit_id", name="uq_visit_reviews_visit_id"),
    )


def downgrade() -> None:
    op.drop_table("visit_reviews")
