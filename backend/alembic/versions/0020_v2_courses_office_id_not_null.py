"""W15-BE-FIXPATTERN (Phase 2): courses.office_id を NOT NULL 化.

Revision ID: 0020_v2_courses_office_id_not_null
Revises: 0019_v2_w15_be1_foundation
Create Date: 2026-05-08

## このマイグレーションの責務

Phase 1 (migration 0019) では既存テスト互換の都合で
``courses.office_id`` を NULLABLE で導入した。Phase 2 (本リビジョン) では
設計仕様 §4.5 に準拠して NOT NULL 化する。

* 実データが存在しないため backfill 不要
* 仮にデータが存在する場合の backfill stub をコメントとして残す
  (将来運用で 0020 を再走させるケースを想定)

## downgrade

NULLABLE に戻すのみ。データ削除は行わない。

## SQLite 互換

batch_alter_table を用いるので SQLite でも安全に走る。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0020_v2_courses_office_id_not_null"
down_revision: Union[str, Sequence[str], None] = "0019_v2_w15_be1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``courses.office_id`` を NOT NULL に変更する.

    W15-codex-fix (2): defensive backfill を実装する。
    本来 Wave 15 Phase 1 完了時点では courses は空だが、Phase 1 リリース後に
    courses 行が積まれた状態で 0020 をデプロイしようとすると NOT NULL 化が
    失敗するため、事前に backfill を試みる。

    backfill 戦略 (空テーブルなら no-op):
        1. courses で office_id IS NULL の件数を取得
        2. 0 件なら何もしない (高速パス)
        3. >0 件なら visits → patients を辿って primary_office_id で埋める
            UPDATE courses SET office_id = (
                SELECT p.primary_office_id FROM patients p
                JOIN visits v ON v.patient_id = p.id
                WHERE v.course_id = courses.id
                  AND p.primary_office_id IS NOT NULL
                LIMIT 1
            ) WHERE office_id IS NULL
        4. それでも残った NULL があれば RuntimeError で abort
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)

    # ---- W15-codex-fix (2): defensive backfill -----------------------------
    null_count_row = bind.execute(
        sa.text("SELECT COUNT(*) FROM courses WHERE office_id IS NULL")
    ).scalar()
    null_count = int(null_count_row or 0)

    if null_count > 0:
        # visits → patients.primary_office_id 経由で推論
        bind.execute(
            sa.text(
                "UPDATE courses SET office_id = ("
                "  SELECT p.primary_office_id FROM patients p"
                "  JOIN visits v ON v.patient_id = p.id"
                "  WHERE v.course_id = courses.id"
                "    AND p.primary_office_id IS NOT NULL"
                "  LIMIT 1"
                ") WHERE office_id IS NULL"
            )
        )
        remaining_row = bind.execute(
            sa.text("SELECT COUNT(*) FROM courses WHERE office_id IS NULL")
        ).scalar()
        remaining = int(remaining_row or 0)
        if remaining > 0:
            raise RuntimeError(
                f"Cannot upgrade migration 0020: {remaining} courses rows have "
                "NULL office_id and no patient.primary_office_id could be inferred. "
                "Backfill office_id manually before re-running."
            )

    with op.batch_alter_table("courses") as batch:
        batch.alter_column(
            "office_id",
            existing_type=uuid_type,
            nullable=False,
        )


def downgrade() -> None:
    """``courses.office_id`` を NULLABLE に戻す (Phase 1 状態に巻き戻し)."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)

    with op.batch_alter_table("courses") as batch:
        batch.alter_column(
            "office_id",
            existing_type=uuid_type,
            nullable=True,
        )
