"""W18 Phase A-1: patients.requires_multiple_staff カラム追加.

Revision ID: 0024_v2_patient_requires_multiple_staff
Revises: 0023_v2_courses_code_check_add_e
Create Date: 2026-05-08

## このマイグレーションの責務

設計仕様書 §4.1 で残置確定された **「複数スタッフ必須」フラグ** を
``patients`` テーブルに物理列として追加する。

| カラム | 型 | nullable | default | 用途 |
|---|---|---|---|---|
| ``requires_multiple_staff`` | ``BOOLEAN`` | NOT NULL | FALSE | 複数スタッフ同行を必須とする患者 (§4.1 / §5.4) |

これまでは ``weekly_pattern.entries[].staff_count`` で曜日別に表現していたが、
**「患者単位で常に複数スタッフ必須」** という運用要件が分離して必要に
なったため、独立した物理列で表現する (W18-FE 同期)。

## SQLite 互換性

* ``BOOLEAN`` 型は SQLite では ``INTEGER`` (0/1) に展開される。
* ``server_default=sa.false()`` は dialect 依存で ``FALSE`` (PG) /
  ``0`` (SQLite) にコンパイルされるため、両 dialect で安全に動く。
* ``ADD COLUMN ... NOT NULL DEFAULT FALSE`` は SQLite でも安全な操作
  (テーブル再構築不要)。

## downgrade

``requires_multiple_staff`` カラムを DROP するのみ。データは失われる。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0024_v2_patient_requires_multiple_staff"
down_revision: Union[str, Sequence[str], None] = "0023_v2_courses_code_check_add_e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``patients.requires_multiple_staff`` カラムを追加する.

    既存行は ``FALSE`` で埋められる (server_default).
    """
    op.add_column(
        "patients",
        sa.Column(
            "requires_multiple_staff",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "複数スタッフ同行を必須とする患者フラグ (§4.1 / §5.4). "
                "TRUE で常時 2 名体制を要求 (W18 Phase A)."
            ),
        ),
    )


def downgrade() -> None:
    """``requires_multiple_staff`` カラムを削除する."""
    op.drop_column("patients", "requires_multiple_staff")
