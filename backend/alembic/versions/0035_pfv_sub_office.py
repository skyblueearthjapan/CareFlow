"""Phase E-5 (項目 ⑥B): patient_fixed_visits.sub_office_id カラム追加.

Revision ID: 0035_pfv_sub_office
Revises: 0034_courses_code_check_m_overflow
Create Date: 2026-05-19

## このマイグレーションの責務

「主担当拠点が稲毛などで決まっている患者の固定枠を、フォロー目的で別拠点
(都賀など) でも設定可能にする」運用要件を満たすため、``patient_fixed_visits``
にサブ拠点 ID を追加する.

| カラム | 型 | nullable | default | 用途 |
|---|---|---|---|---|
| ``sub_office_id`` | UUID | NULL 可 | NULL | サブ拠点 (Phase E-5 ⑥B) |

* FK ``offices(id)`` (ondelete 未指定 = RESTRICT)
* 既存行は NULL で埋まる (後方互換).
* 自動算出本体 (auto_allocator_v2 run_v2_pipeline) は patient.primary_office_id
  しか見ない. ``sub_office_id`` は diff_add pool 展開のみで考慮される.

## SQLite 互換

* ``UUID`` 型は PG では ``UUID``、SQLite では ``String(36)``.
* ``ADD COLUMN`` + nullable FK は SQLite でもテーブル再構築不要で安全.

## downgrade

* PG: FK 制約 + index を drop してから column を drop.
* SQLite: batch_alter_table で column drop (FK/index は自動で消える).
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0035_pfv_sub_office"
down_revision: Union[str, Sequence[str], None] = "0034_courses_code_check_m_overflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``patient_fixed_visits.sub_office_id`` カラムを追加する.

    既存行は NULL で埋められる (後方互換). サブ拠点未設定の PFV は従来通り
    主担当拠点 (patient.primary_office_id) のみで動く.
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.add_column(
            "patient_fixed_visits",
            sa.Column(
                "sub_office_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
                comment=(
                    "Phase E-5 (項目 ⑥B): サブ拠点 ID. "
                    "主担当 (primary_office_id) とは別にフォロー時に固定枠を配置できる."
                ),
            ),
        )
        op.create_foreign_key(
            "fk_pfv_sub_office_id",
            "patient_fixed_visits",
            "offices",
            ["sub_office_id"],
            ["id"],
        )
        op.create_index(
            "ix_patient_fixed_visits_sub_office_id",
            "patient_fixed_visits",
            ["sub_office_id"],
        )
    else:
        # SQLite (テスト) フォールバック: UUID → String(36).
        with op.batch_alter_table("patient_fixed_visits") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "sub_office_id",
                    sa.String(36),
                    sa.ForeignKey(
                        "offices.id",
                        name="fk_pfv_sub_office_id",
                    ),
                    nullable=True,
                ),
            )
            batch_op.create_index(
                "ix_patient_fixed_visits_sub_office_id",
                ["sub_office_id"],
            )


def downgrade() -> None:
    """``sub_office_id`` カラムを削除する.

    PostgreSQL は index → FK 制約 → column の順に drop する.
    SQLite では batch_alter_table で自動的に再構築されるため drop_constraint は
    SQLite では呼ばない.
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.drop_index(
            "ix_patient_fixed_visits_sub_office_id",
            table_name="patient_fixed_visits",
        )
        op.drop_constraint(
            "fk_pfv_sub_office_id",
            "patient_fixed_visits",
            type_="foreignkey",
        )
        op.drop_column("patient_fixed_visits", "sub_office_id")
    else:
        with op.batch_alter_table("patient_fixed_visits") as batch_op:
            batch_op.drop_index("ix_patient_fixed_visits_sub_office_id")
            batch_op.drop_column("sub_office_id")
