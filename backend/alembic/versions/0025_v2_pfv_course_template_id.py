"""W22 Phase A-1: patient_fixed_visits.course_template_id カラム追加.

Revision ID: 0025_v2_pfv_course_template_id
Revises: 0024_v2_patient_requires_multiple_staff
Create Date: 2026-05-08

## このマイグレーションの責務

「A コースに割り当てられた患者は次週以降も A コース」という運用要件を
データ層で表現するため、``patient_fixed_visits`` に
``course_template_id`` (UUID, NULL 可) を追加する。

| カラム | 型 | nullable | default | 用途 |
|---|---|---|---|---|
| ``course_template_id`` | UUID | NULL 可 | NULL | 固定枠が属するコーステンプレート (§W22) |

* FK ``course_templates(id)`` ON DELETE SET NULL
  - course_template が削除された場合は固定枠は残るが course は外れる。
* 既存行は NULL で埋まる (後方互換)。Layer 1 / 取込スクリプトで段階的に補正する。

## SQLite 互換

* ``UUID`` 型は PG では ``UUID``、SQLite では ``String(36)``。0017 と同じ分岐。
* ``ADD COLUMN`` + nullable FK は SQLite でもテーブル再構築不要で安全。

## downgrade

* FK 制約を drop してから column を drop。
* SQLite では ALTER TABLE DROP CONSTRAINT が無いため、batch_alter_table 経由。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0025_v2_pfv_course_template_id"
down_revision: Union[str, Sequence[str], None] = "0024_v2_patient_requires_multiple_staff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``patient_fixed_visits.course_template_id`` カラムを追加する.

    既存行は NULL で埋められる (後方互換)。Layer 1 は NULL の場合
    現行フォールバック (office × A 系列優先) で動作する。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.add_column(
            "patient_fixed_visits",
            sa.Column(
                "course_template_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "course_templates.id",
                    ondelete="SET NULL",
                    name="fk_pfv_course_template_id",
                ),
                nullable=True,
                comment=(
                    "固定枠が属する course_templates.id (W22 Phase A). "
                    "NULL の場合は Layer 1 の office フォールバックで解決する。"
                ),
            ),
        )
    else:
        # SQLite (テスト) フォールバック: UUID → String(36).
        with op.batch_alter_table("patient_fixed_visits") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "course_template_id",
                    sa.String(36),
                    sa.ForeignKey(
                        "course_templates.id",
                        ondelete="SET NULL",
                        name="fk_pfv_course_template_id",
                    ),
                    nullable=True,
                ),
            )


def downgrade() -> None:
    """``course_template_id`` カラムを削除する.

    PostgreSQL は FK 制約を先に drop する必要があるが、SQLite は
    batch_alter_table で自動的に再構築されるため drop_constraint は
    SQLite では呼ばない (drop_constraint が NotImplementedError になる)。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.drop_constraint(
            "fk_pfv_course_template_id",
            "patient_fixed_visits",
            type_="foreignkey",
        )
        op.drop_column("patient_fixed_visits", "course_template_id")
    else:
        with op.batch_alter_table("patient_fixed_visits") as batch_op:
            batch_op.drop_column("course_template_id")
