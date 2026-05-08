"""W16 codex fix (中 2): courses.code CHECK 制約に 'E' を追加.

Revision ID: 0023_v2_courses_code_check_add_e
Revises: 0022_v2_w16_manager_course_seed
Create Date: 2026-05-08

## このマイグレーションの責務 (Codex 全方位レビュー指摘 (中 2))

旧定義: ``CHECK (code IN ('A','B','C','D','M'))``

* 本店の course_templates は label A-E を持つ運用だが、'E' label を
  そのまま ``courses.code`` に複写すると CHECK 違反になる。
* schedule.py 側で「E → 'M' に丸める」フォールバックを入れていたが、
  本来不正なコードがマネージャー枠に紛れ込む副作用が発生する。

対策: CHECK 制約を ``CHECK (code IN ('A','B','C','D','E','M'))`` に拡張する。
本来 'E' label のテンプレートから生成された Course は code='E' で保存され、
M (マネージャー枠) は別途明示的に区別される。

## SQLite 互換

* ``op.batch_alter_table`` で drop + create する。SQLite は CHECK 制約名を
  失うことがあるため、明示的に名前付き制約を再作成する。
* PostgreSQL は ALTER TABLE DROP/ADD CONSTRAINT で同等の挙動になる。

## downgrade

旧 CHECK 定義 ('A','B','C','D','M') に戻す。データに 'E' code が残っている場合
downgrade は失敗するが、stub 環境ではデータが無いため問題ない。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union


from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0023_v2_courses_code_check_add_e"
down_revision: Union[str, Sequence[str], None] = "0022_v2_w16_manager_course_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """旧 CHECK ('A','B','C','D','M') を drop し、新 CHECK ('A','B','C','D','E','M') に再構築."""
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("ck_courses_code_v2", type_="check")
        batch.create_check_constraint(
            "ck_courses_code_v2",
            "code IN ('A', 'B', 'C', 'D', 'E', 'M')",
        )


def downgrade() -> None:
    """新 CHECK を drop し旧 CHECK ('A','B','C','D','M') に戻す.

    'E' code を持つ既存行があれば downgrade は失敗する。
    旧運用に戻すには事前にデータ側で E → M (or 削除) しておく必要がある。
    """
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("ck_courses_code_v2", type_="check")
        batch.create_check_constraint(
            "ck_courses_code_v2",
            "code IN ('A', 'B', 'C', 'D', 'M')",
        )
