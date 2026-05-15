"""v2: widen patients.sex_restriction from VARCHAR(8) to VARCHAR(16).

Revision ID: 0028_v2_patient_sex_restriction_widen
Revises: 0027_v2_multi_staff_slot_index
Create Date: 2026-05-15

v2 Pydantic schema (``SexRestrictionV2``) は ``female_only`` (11 chars) /
``male_only`` (9 chars) を受け取るのに、`patients.sex_restriction` 列が
VARCHAR(8) のままだったため、書き込み時に
``StringDataRightTruncationError`` が発生する状態だった。

migration 0009 で v2 整理を行った際に拡張漏れだったと推測される。
データ損失なしで 16 に拡張する。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_v2_patient_sex_restriction_widen"
down_revision = "0027_v2_multi_staff_slot_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "patients",
        "sex_restriction",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "patients",
        "sex_restriction",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=True,
    )
