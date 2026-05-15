"""courses: switch UNIQUE (iso_year, iso_week, weekday, code, office_id) to partial.

Revision ID: 0030_courses_unique_proposed_partial
Revises: 0029_courses_proposal_batch
Create Date: 2026-05-16

## このマイグレーションの責務 (Wave 41 自動算出 v1.0 feedback)

W41 v1.0 の自動算出では「再算出」操作 (= 同一週・拠点で既に
``course_status='staff_assigned'`` まで進んだ Course が残っている状態で再度
``auto-allocate`` を実行する) を許可する必要がある。

旧 UNIQUE 制約 ``uq_courses_year_week_weekday_code_office`` は ``course_status``
を含まないため、新規 ``proposed`` Course の INSERT が既存 ``staff_assigned`` /
``course_fixed`` 行と衝突して 23505 (UniqueViolation) → API 層で 409 Conflict
になっていた。

本 migration で UNIQUE を **partial UNIQUE INDEX
``(iso_year, iso_week, weekday, code, office_id) WHERE course_status != 'proposed'``**
に切り替える。

これにより:

  * 同じ (year, week, weekday, code, office) を持つ行は、status が
    ``course_fixed`` または ``staff_assigned`` (= 確定済み) の中では 1 行のみ
    (これは旧 UNIQUE と同じ業務上の不変条件)
  * 同時に ``proposed`` の Course は (year, week, weekday, code, office) が
    重複しても許される (= 再算出時に新 proposed が既存 finalized と共存可能)
  * 採用 (apply) 時には既存 finalized を soft-delete してから proposed を
    finalized に昇格させる契約 (本ファイルでは migration のみ、API 側は
    ``app/api/v1/schedule.py`` ``apply_proposal`` で実装)

## SQLite 互換

SQLite 3.8.0+ は partial UNIQUE INDEX をサポートする (migration 0021 と同じ
パターン)。CHECK / UNIQUE を batch_alter_table で drop してから op.execute で
raw SQL を流す。

## downgrade

partial UNIQUE INDEX を drop し、旧 ``UniqueConstraint`` (status を含まない
フル UNIQUE) を再作成する。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0030_courses_unique_proposed_partial"
down_revision: Union[str, Sequence[str], None] = "0029_courses_proposal_batch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧 UNIQUE 制約を drop (status を含まないフル UNIQUE).
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("uq_courses_year_week_weekday_code_office", type_="unique")

    # status != 'proposed' のみを対象にする partial UNIQUE INDEX を新規作成.
    # PG / SQLite いずれも 3.8+ で partial INDEX をサポートするため
    # 同一 raw SQL で対応する.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_courses_year_week_weekday_code_office_finalized
        ON courses (iso_year, iso_week, weekday, code, office_id)
        WHERE course_status != 'proposed'
        """
    )


def downgrade() -> None:
    # partial UNIQUE INDEX を drop し、旧 UNIQUE を復元する.
    op.execute("DROP INDEX IF EXISTS uq_courses_year_week_weekday_code_office_finalized")

    with op.batch_alter_table("courses") as batch:
        batch.create_unique_constraint(
            "uq_courses_year_week_weekday_code_office",
            ["iso_year", "iso_week", "weekday", "code", "office_id"],
        )
