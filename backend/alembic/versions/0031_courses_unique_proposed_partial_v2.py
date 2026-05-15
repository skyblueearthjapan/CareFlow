"""courses: partial UNIQUE INDEX に ``deleted_at IS NULL`` 条件を追加する.

Revision ID: 0031_courses_unique_proposed_partial_v2
Revises: 0030_courses_unique_proposed_partial
Create Date: 2026-05-16

## このマイグレーションの責務 (Wave 41 v1.0 cross-review C1)

0030 で導入した partial UNIQUE INDEX
``(iso_year, iso_week, weekday, code, office_id) WHERE course_status != 'proposed'``
は、**soft-delete 済み (``deleted_at IS NOT NULL``) の Course を除外していない**。

これにより以下のシナリオで UNIQUE 違反が発生する:

1. ``auto-allocate`` で proposed Course を生成
2. ``apply`` で既存 finalized Course を soft-delete (``deleted_at`` を set) しつつ
   proposed → ``staff_assigned`` へ昇格
3. → 同じ (year, week, weekday, code, office) に
   ``course_status='staff_assigned'`` の Course が **2 行** (古い soft-deleted +
   新規 finalized) 存在することになり partial UNIQUE が違反になる

(``course_status`` は soft-delete 時に変更しない契約のため、status だけでは
古い行を除外できない. SQLite / PostgreSQL いずれでも再現確認済み.)

修正方針:
  * 旧 partial UNIQUE INDEX ``uq_courses_year_week_weekday_code_office_finalized``
    を DROP
  * 新しい partial UNIQUE INDEX を ``WHERE course_status != 'proposed' AND
    deleted_at IS NULL`` で再作成

これにより:
  * ``proposed`` の Course は引き続き重複可 (再算出が可能)
  * soft-delete された finalized Course は UNIQUE から除外される (=
    apply で旧行を soft-delete してから新行を finalized 昇格できる)
  * 物理 ``DELETE`` ではなく soft-delete を維持できる (監査ログ要件)

## SQLite 互換

partial UNIQUE INDEX の WHERE 句で ``IS NULL`` を使うのは 0021 / 0030 と同じ
パターン. SQLite 3.8.0+ / PostgreSQL いずれもサポートする.

## downgrade

新 partial UNIQUE INDEX を drop し、0030 状態 (``WHERE course_status != 'proposed'``
のみ) に戻す.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0031_courses_unique_proposed_partial_v2"
down_revision: Union[str, Sequence[str], None] = "0030_courses_unique_proposed_partial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0030 で作った partial UNIQUE INDEX を一旦 drop し、deleted_at 条件付きで再作成.
    op.execute("DROP INDEX IF EXISTS uq_courses_year_week_weekday_code_office_finalized")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_courses_year_week_weekday_code_office_finalized
        ON courses (iso_year, iso_week, weekday, code, office_id)
        WHERE course_status != 'proposed' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    # 0030 の状態 (deleted_at 条件なし) に戻す.
    op.execute("DROP INDEX IF EXISTS uq_courses_year_week_weekday_code_office_finalized")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_courses_year_week_weekday_code_office_finalized
        ON courses (iso_year, iso_week, weekday, code, office_id)
        WHERE course_status != 'proposed'
        """
    )
