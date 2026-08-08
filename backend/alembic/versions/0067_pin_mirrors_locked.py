"""is_pinned を movability='locked' の非推奨ミラーへ同期 — PO 決定 2026-08-09.

Revision ID: 0067_pin_mirrors_locked
Revises: 0066_visit_week_pinned
Create Date: 2026-08-09

## このマイグレーションの責務

赤ピン (is_pinned) と可動域「完全固定」(movability='locked') を **1 概念に統合**
する。正典は movability に一本化し、is_pinned は移行期間中の読み取り互換のための
**非推奨ミラー** (movability='locked' と常に同値) とする。

  1. is_pinned=true の残存行 (本番 1 件) を movability='locked' へ昇格
  2. 全行で is_pinned := (movability='locked') に同期

## 背景 (PO 決定)

エンジンから枠を守る機能は両者で完全に重複していた。統合後の意味論:
  - 完全固定 = エンジン (提案・最適化・自動割当) は動かさない
  - 人手 (患者マスタ編集・盤面の移動) は **警告のうえ常に可**
旧 is_pinned 固有だった「人手編集の 422 ブロック」は撤廃する (別コミット)。

## downgrade

no-op (ミラー同期は冪等・非破壊)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0067_pin_mirrors_locked"
down_revision: str | Sequence[str] | None = "0066_visit_week_pinned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    t = "true" if is_pg else "1"
    f = "false" if is_pg else "0"
    # 1) 残存ピンを完全固定へ昇格
    op.execute(
        sa.text(f"UPDATE patient_fixed_visits SET movability='locked' WHERE is_pinned = {t}")
    )
    # 2) ミラー同期 (is_pinned ≡ movability='locked')
    op.execute(
        sa.text(f"UPDATE patient_fixed_visits SET is_pinned = {t} WHERE movability = 'locked'")
    )
    op.execute(
        sa.text(f"UPDATE patient_fixed_visits SET is_pinned = {f} WHERE movability <> 'locked'")
    )


def downgrade() -> None:
    pass
