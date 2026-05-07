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

    実データが入っている運用環境を想定したコメントを残す:

    実装時点では courses 全行が空である前提のため backfill は不要。
    もし将来テーブルにデータが入った状態で 0020 を再走させる場合は、
    NOT NULL 化に先立ち以下のような backfill を行うこと:

        # bind = op.get_bind()
        # if bind.execute(sa.text("SELECT count(*) FROM courses")).scalar() > 0:
        #     bind.execute(sa.text(
        #         "UPDATE courses SET office_id = ("
        #         "  SELECT primary_office_id FROM patients"
        #         "  JOIN visits v ON v.patient_id = patients.id"
        #         "  WHERE v.course_id = courses.id"
        #         "  LIMIT 1"
        #         ") WHERE office_id IS NULL"
        #     ))
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)

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
