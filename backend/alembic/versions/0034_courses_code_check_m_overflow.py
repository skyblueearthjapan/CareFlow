"""CareFlow Wave Next 2 cross-review [H1]: courses.code CHECK に M2..M9 を追加.

Revision ID: 0034_courses_code_check_m_overflow
Revises: 0033_v2_seed_m_template_all_offices
Create Date: 2026-05-18

## このマイグレーションの責務 (HIGH [H1])

旧定義: ``CHECK (code IN ('A','B','C','D','E','M'))`` (migration 0023)

問題:
  ``auto_allocator_v2.py`` の Stage 5 で overflow set を全部 ``code="M"`` に
  集約すると、``_apply_travel_time_to_courses()`` / ``_check_course_capacity_minutes()``
  / capacity 判定 (MAX_PATIENTS_PER_COURSE=6) が ``(office, weekday, code)`` で
  grouping するため、複数 overflow set を 1 物理ルートとして扱ってしまう
  (時刻繰り下げ・duration 合算・6 人 capacity 判定が誤発火).

対策:
  M overflow を ``M / M2 / M3 / ... / M9`` の最大 9 種類に分散させる.
  これにより各 overflow set が独立した物理ルートとして時刻・容量計算される.

CHECK 制約を ``CHECK (code IN ('A','B','C','D','E','M','M2','M3','M4','M5','M6','M7','M8','M9'))``
に拡張する.

## partial UNIQUE INDEX (0031) との整合性

``(iso_year, iso_week, weekday, code, office_id) WHERE course_status != 'proposed'
AND deleted_at IS NULL`` の partial UNIQUE は ``code`` を含むので、
``M / M2 / M3`` は別 code 値として衝突しない (1 拠点 × 1 曜日 × 1 週で
最大 9 個の M-overflow Course を独立保持可).

## SQLite 互換

* ``op.batch_alter_table`` で drop + create する (0023 と同パターン).
* SQLite は CHECK 制約名を失うことがあるため、明示的に名前付き制約を再作成.
* PostgreSQL は ALTER TABLE DROP/ADD CONSTRAINT で同等の挙動.

## downgrade

旧 CHECK ('A','B','C','D','E','M') に戻す.
data に M2..M9 code が残っている場合 downgrade は失敗するので、運用上は
事前にデータ側で M2..M9 → M に丸める or 削除しておく必要がある.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union


from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0034_courses_code_check_m_overflow"
down_revision: Union[str, Sequence[str], None] = "0033_v2_seed_m_template_all_offices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """旧 CHECK ('A','B','C','D','E','M') を drop し、M2..M9 を含む新 CHECK に再構築."""
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("ck_courses_code_v2", type_="check")
        batch.create_check_constraint(
            "ck_courses_code_v2",
            "code IN ('A', 'B', 'C', 'D', 'E', 'M', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9')",
        )


def downgrade() -> None:
    """新 CHECK を drop し、0023 状態 ('A','B','C','D','E','M') に戻す.

    M2..M9 code を持つ既存行があれば downgrade は失敗する.
    """
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("ck_courses_code_v2", type_="check")
        batch.create_check_constraint(
            "ck_courses_code_v2",
            "code IN ('A', 'B', 'C', 'D', 'E', 'M')",
        )
