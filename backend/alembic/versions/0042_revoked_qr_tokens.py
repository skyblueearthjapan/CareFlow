"""QR 訪問チェックイン Phase 5-1: revoked_qr_tokens テーブル.

Revision ID: 0042_revoked_qr_tokens
Revises: 0041_qr_checkin
Create Date: 2026-06-30

## このマイグレーションの責務

QR 再発行で失効した旧トークンを記録する新テーブル ``revoked_qr_tokens`` を新設
する (additive)。judge が未知トークンを「ローテ済 (revoked)」か「完全な未知」か
で区別し、前者に 410 Gone を返すための引き当て表。

* ``patient_id`` は監査証跡として **ON DELETE RESTRICT** (0041 の visit_checkins
  に倣う)。``token`` は非 NULL の重複を禁止 (unique)。

## SQLite 互換

0041 と同じく dialect 分岐: PG = ``postgresql.UUID`` / ``gen_random_uuid()``、
SQLite = ``String(36)``。``now()`` は両 dialect の関数差を吸収する。

## downgrade

``revoked_qr_tokens`` を drop する。downgrade で失効履歴は失われる。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0042_revoked_qr_tokens"
down_revision: Union[str, Sequence[str], None] = "0041_qr_checkin"
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
        "revoked_qr_tokens",
        id_column,
        sa.Column(
            "patient_id",
            uuid_type,
            sa.ForeignKey(
                "patients.id",
                ondelete="RESTRICT",
                name="fk_revoked_qr_tokens_patient_id_patients",
            ),
            nullable=False,
        ),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.UniqueConstraint("token", name="uq_revoked_qr_tokens_token"),
    )
    op.create_index(
        "ix_revoked_qr_tokens_patient_id",
        "revoked_qr_tokens",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_qr_tokens_patient_id", table_name="revoked_qr_tokens")
    op.drop_table("revoked_qr_tokens")
