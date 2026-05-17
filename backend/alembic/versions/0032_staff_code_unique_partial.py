"""staff.code partial unique index (alive + non-null).

Revision ID: 0032_staff_code_unique_partial
Revises: 0031_courses_unique_proposed_partial_v2
Create Date: 2026-05-17

## このマイグレーションの責務 (W41 後続 cross-review HIGH#3)

スタッフ Excel 機能の concurrent import 時に ``staff.code`` 重複が silent に
通る race condition を防ぐため、``staff.code`` に partial UNIQUE INDEX を追加.

条件: ``deleted_at IS NULL AND code IS NOT NULL``

* soft-delete 済みスタッフは UNIQUE 対象外 (削除済 code を新規 staff で再利用可能)
* ``code IS NULL`` のスタッフも UNIQUE 対象外 (現状 nullable な仕様維持)

## SQLite 互換

SQLite 3.8.0+ は partial UNIQUE INDEX をサポートする (0021 / 0030 と同じパターン)。
PostgreSQL / SQLite いずれも同一 SQL で対応する.

## 安全対策

upgrade で既存の重複行を pre-check し、見つかった場合は ``RuntimeError`` を
raise してマイグレーションを止める. 本番 VPS で重複データが残ったまま
UNIQUE 制約を追加して途中で失敗するより、明示的に停止して人手解決を促す.

## downgrade

partial UNIQUE INDEX を drop するだけ.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0032_staff_code_unique_partial"
down_revision: Union[str, Sequence[str], None] = "0031_courses_unique_proposed_partial_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 既存重複データが無いか pre-check (alive かつ code が non-null な行のみ).
    conn = op.get_bind()
    duplicates = conn.execute(
        sa.text(
            "SELECT code, COUNT(*) AS cnt FROM staff "
            "WHERE deleted_at IS NULL AND code IS NOT NULL "
            "GROUP BY code HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            f"Cannot add UNIQUE constraint: duplicate staff.code values found: "
            f"{[(row[0], row[1]) for row in duplicates]}. "
            "Resolve duplicates before migrating."
        )

    # PG / SQLite いずれも 3.8+ で partial UNIQUE INDEX をサポートするため
    # 同一 raw SQL で対応する.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_staff_code_unique_alive
        ON staff (code)
        WHERE deleted_at IS NULL AND code IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_staff_code_unique_alive")
