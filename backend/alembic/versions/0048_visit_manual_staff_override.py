"""P3-①: visits.manual_staff_override (当日欠勤の手動差替え保護フラグ).

Revision ID: 0048_visit_manual_staff_override
Revises: 0047_movability_and_suggestion_dismissals
Create Date: 2026-07-03

## このマイグレーションの責務 (代替スタッフ提案 Commit 1 データ基盤)

設計書 docs/plans/p3-1-staff-substitute-design.md §4 に従い、``visits`` に
``manual_staff_override`` カラムを 1 本追加する.

* 意味: 当日欠勤対応で手動差替えした visit を「自動割当 (assign-staff-only /
  layer3 ``_persist``) の再上書きから保護する」ためのフラグ.
* 値: ``BOOLEAN NOT NULL server_default=false``. ADD COLUMN 時点で既存全行が
  false で埋まる (= UPDATE 全行 backfill 不要). 新規 INSERT も false.
* index / CHECK は不要 (単純な真偽フラグ).

layer3 ``_persist`` の VSA DELETE/INSERT 対象からの除外 (= 保護の実効) は
**Commit 2** で実装する. 本 migration + モデルは列の追加のみを担う.

## PG / SQLite 互換 (0047 の作法を踏襲)

* ADD COLUMN + server_default は両 dialect ともそのまま実行できる
  (SQLite も ``ALTER TABLE ... ADD COLUMN ... DEFAULT`` を許容する).
  ``sa.false()`` は PG では ``false``, SQLite では ``0`` に per-dialect 展開される.
* downgrade の DROP COLUMN は旧 SQLite で ALTER 非対応のため ``batch_alter_table``
  で再構築する (0047 と同方針). PG は直接 ``drop_column``.

## downgrade

``manual_staff_override`` カラムを drop して完全に元に戻す.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0048_visit_manual_staff_override"
down_revision: Union[str, Sequence[str], None] = "0047_movability_and_suggestion_dismissals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default=false で NOT NULL カラム追加. ADD COLUMN 時点で既存全行が
    # false で埋まる (= UPDATE 全行 backfill 不要). 新規 INSERT も false.
    op.add_column(
        "visits",
        sa.Column(
            "manual_staff_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "P3-①: 当日欠勤対応で手動差替えした visit の保護フラグ. "
                "True の visit は layer3 _persist (assign-staff-only) の "
                "VSA DELETE/INSERT 対象から除外される (除外実装は Commit 2)."
            ),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.drop_column("visits", "manual_staff_override")
    else:
        # SQLite: 旧バージョンは ALTER DROP COLUMN 非対応のため batch 再構築.
        with op.batch_alter_table("visits") as batch:
            batch.drop_column("manual_staff_override")
