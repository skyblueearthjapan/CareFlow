"""W34: visits partial UNIQUE (patient_id, visit_date, start_time) WHERE deleted_at IS NULL.

Revision ID: 0026_v2_visits_unique_pds
Revises: 0025_v2_pfv_course_template_id
Create Date: 2026-05-09

## このマイグレーションの責務 (W34 (C))

本番 DB で同一 ``(patient_id, visit_date, start_time)`` の visits 行が
複数積み上がる障害 (Layer 1 の auto 再生成漏れ + 手動再投入) の
**恒久対策**として、DB 層に partial UNIQUE 制約を追加する.

| 制約 | 列 | 条件 |
|---|---|---|
| ``uq_visits_pds_active`` | (patient_id, visit_date, start_time) | ``WHERE deleted_at IS NULL`` |

論理削除済み (``deleted_at IS NOT NULL``) の行は UNIQUE の対象外なので、
Layer 1 が auto 行を soft-delete してから新しい auto 行を INSERT しても
衝突しない (W34 (B) と整合).

## 重要: 適用前に重複解消が必須

このマイグレーションを適用する **前に**
``scripts/cleanup_visits_duplicate.py --apply`` を必ず実行すること.
重複が残っているマイグレーションは UNIQUE 違反で失敗する.

デプロイ手順 (本番 VPS):
  1. backend rebuild
  2. ``python scripts/cleanup_visits_duplicate.py --dry-run``
  3. ``python scripts/cleanup_visits_duplicate.py --apply``
  4. ``alembic upgrade head`` (本マイグレーション 0026 適用)
  5. 確認 SQL で重複 0 件確認

## SQLite 互換

* SQLite 3.8.0+ は partial UNIQUE INDEX (``CREATE UNIQUE INDEX ... WHERE ...``)
  をサポートする (ref: 0021 でも採用済み).
* ``CREATE UNIQUE INDEX`` を ``op.execute`` で直接発行する.

## downgrade

partial UNIQUE INDEX を ``DROP INDEX`` する. データには触らない.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0026_v2_visits_unique_pds"
down_revision: Union[str, Sequence[str], None] = "0025_v2_pfv_course_template_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``visits`` に partial UNIQUE INDEX (``uq_visits_pds_active``) を追加する.

    PostgreSQL / SQLite 3.8.0+ どちらも partial INDEX をサポート.
    """
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visits_pds_active
        ON visits (patient_id, visit_date, start_time)
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    """partial UNIQUE INDEX (``uq_visits_pds_active``) を drop する."""
    op.execute("DROP INDEX IF EXISTS uq_visits_pds_active")
