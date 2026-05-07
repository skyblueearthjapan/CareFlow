"""W15-codex-fix (3)(4): course_templates partial UNIQUE + courses UNIQUE + office_id.

Revision ID: 0021_v2_course_templates_courses_uniqueness
Revises: 0020_v2_courses_office_id_not_null
Create Date: 2026-05-08

## このマイグレーションの責務 (Codex 全方位レビュー指摘 (3)(4))

### (3) course_templates UNIQUE が deleted_at を考慮しない問題

旧定義: ``UNIQUE (office_id, label)`` (deleted_at に関係なく重複禁止)

* 論理削除済みの label と同じラベルで再作成しようとすると 409 Conflict
* 業務上、過去ラベルを再活性化したいケースで運用不能

対策:
* PostgreSQL: ``WHERE deleted_at IS NULL`` の partial UNIQUE INDEX に切替
  (削除済み行は UNIQUE の対象外になる)
* SQLite: 3.8.0+ は partial INDEX をサポートするため、同一 SQL で対応可能
  (ORM 側の ``sqlite_where`` と整合させる)

### (4) courses UNIQUE が office 非スコープ問題

旧定義: ``UNIQUE (iso_year, iso_week, weekday, code)``

* 異なる拠点 (office) でも同じ (year, week, weekday, code) を取れない
* マルチ拠点運用で「拠点 A の月曜 A コース」と「拠点 B の月曜 A コース」が
  共存できない設計バグ

対策: ``UNIQUE (iso_year, iso_week, weekday, code, office_id)`` に再構築
(office 単位の独立した UNIQUE を担保)

## SQLite 互換

* batch_alter_table を使った drop + create で SQLite でも安全に走る
* SQLite 3.8.0+ は partial INDEX をサポートするため、PostgreSQL と同一 SQL を使用する。

## downgrade

旧 UNIQUE 定義に戻す。データは触らない。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union


from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0021_v2_course_templates_courses_uniqueness"
down_revision: Union[str, Sequence[str], None] = "0020_v2_courses_office_id_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ----------------------------------------------------------------------
    # (3) course_templates: 論理削除考慮の partial UNIQUE
    # ----------------------------------------------------------------------
    # 旧 UNIQUE 制約 ``uq_course_templates_office_label`` を drop。
    # PostgreSQL では partial UNIQUE INDEX を新規作成する。
    # SQLite では partial INDEX 不可なので新たに通常 UNIQUE INDEX を貼り直す
    # (テスト環境互換、本番との挙動差は許容)。
    with op.batch_alter_table("course_templates") as batch:
        batch.drop_constraint("uq_course_templates_office_label", type_="unique")

    if is_pg:
        # partial UNIQUE: 論理削除済み行は UNIQUE の対象外
        op.execute(
            """
            CREATE UNIQUE INDEX uq_course_templates_office_label_active
            ON course_templates (office_id, label)
            WHERE deleted_at IS NULL
            """
        )
    else:
        # SQLite 3.8.0+ は partial UNIQUE INDEX をサポートする
        op.execute(
            """
            CREATE UNIQUE INDEX uq_course_templates_office_label_active
            ON course_templates (office_id, label)
            WHERE deleted_at IS NULL
            """
        )

    # ----------------------------------------------------------------------
    # (4) courses: UNIQUE に office_id を含めて拡張
    # ----------------------------------------------------------------------
    # 旧: UNIQUE (iso_year, iso_week, weekday, code)
    # 新: UNIQUE (iso_year, iso_week, weekday, code, office_id)
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("uq_courses_year_week_weekday_code", type_="unique")
        batch.create_unique_constraint(
            "uq_courses_year_week_weekday_code_office",
            ["iso_year", "iso_week", "weekday", "code", "office_id"],
        )


def downgrade() -> None:
    """旧 UNIQUE 定義に戻す。"""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # (4) courses UNIQUE を元に戻す
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("uq_courses_year_week_weekday_code_office", type_="unique")
        batch.create_unique_constraint(
            "uq_courses_year_week_weekday_code",
            ["iso_year", "iso_week", "weekday", "code"],
        )

    # (3) course_templates UNIQUE を元に戻す
    if is_pg:
        op.drop_index(
            "uq_course_templates_office_label_active",
            table_name="course_templates",
        )
    else:
        op.drop_index(
            "uq_course_templates_office_label_active",
            table_name="course_templates",
        )
    with op.batch_alter_table("course_templates") as batch:
        batch.create_unique_constraint(
            "uq_course_templates_office_label",
            ["office_id", "label"],
        )
