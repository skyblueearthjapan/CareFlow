"""visits.week_pinned (青ピン専用フラグ) 追加 — PO 決定 2026-08-09.

Revision ID: 0066_visit_week_pinned
Revises: 0065_reset_machine_set_movability_locked
Create Date: 2026-08-09

## このマイグレーションの責務

週のピン (青ピン) の実体を ``visit.source='manual_week'`` の書き換えから
専用 boolean 列 ``visits.week_pinned`` へ移す。

## なぜ列が必要になったか

青ピン導入時は「manual_week の既存意味論に入口を足すだけ」で列追加を避けた。
しかし本番の今週はカイポケ取込 (source='import') が 119 件中 114 件で、
source 書き換え方式では次の構造的な欠陥がある:

  1. 取込訪問を一括固定すると source が 'manual_week' になり
     「カイポケ由来」という出所の記録が失われる。
  2. さらに一括解除すると source='auto' になり、**取込訪問が週生成で
     削除できる状態に落ちる** (= 一括解除がカイポケ週を破壊できる)。

フラグ方式なら 掛け外しは source に触れず、取込は取込のまま保護が続く。

## backfill

``source='manual_week'`` の既存行は week_pinned=true に埋める
(青ピン表示の意味論を保つ。source はそのまま = 「今週のみ」チップも従来どおり)。

## downgrade

列 drop のみ (フラグ情報は失われるが、source は無傷なので保護は
manual_week ベースの従来水準に戻るだけ)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0066_visit_week_pinned"
down_revision: str | Sequence[str] | None = "0065_reset_machine_set_movability_locked"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    false_lit = "false" if is_pg else "0"
    op.add_column(
        "visits",
        sa.Column(
            "week_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text(false_lit),
            comment=(
                "週のピン (青ピン)。true = この訪問は今週この位置のまま動かさない "
                "(週生成の削除対象外 + 当該患者日の再生成 skip)。source とは独立で、"
                "掛け外ししても出所 (import 等) は失われない。"
            ),
        ),
    )
    # backfill: 既存の manual_week 行は青ピン扱いを引き継ぐ.
    op.execute(
        sa.text(
            "UPDATE visits SET week_pinned = "
            + ("true" if is_pg else "1")
            + " WHERE source = 'manual_week'"
        )
    )


def downgrade() -> None:
    op.drop_column("visits", "week_pinned")
