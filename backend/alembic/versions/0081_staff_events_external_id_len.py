"""staff_events.external_id を varchar(40) → varchar(64) に拡張.

Revision ID: 0081_staff_events_external_id_len
Revises: 0080_event_defaults_unique
Create Date: 2026-08-25

## このマイグレーションの責務

固定イベント展開 (`expand_staff_event_defaults`) の冪等キーは
``"{staff_event_defaults.id (UUID 36桁)}:{YYYY-MM-DD}"`` = **47 文字**。
mig 0062 で作った ``external_id varchar(40)`` (カイポケ取込キー
``"{個別業務ID}:{職員内部ID}:{YYYY-MM-DD}"`` 想定・本番最大 28 文字) に収まらず、
本番 Postgres では ``StringDataRightTruncationError`` で INSERT が落ちていた
(2026-08-25 本番で rollback 付き空打ちにより実測)。SQLite は VARCHAR の長さを
検査しないためテストでは検出できなかった。

固定イベントは 2026-08-25 に初めて本番登録された (42 件) ため、それ以前は
展開対象が 0 件で顕在化していなかった。週生成 / 固定枠に戻す / 個別提案適用
の各経路がこの展開を同一 TX で呼ぶので、修正しないと **それらが 500 になる**。

64 = UUID 36 + ':' 1 + 日付 10 = 47 に余裕を持たせた値。部分ユニーク索引
``uq_staff_events_source_external`` (source, external_id) は型変更後もそのまま有効。

## downgrade

varchar(40) へ戻す。40 文字超の行 (= 展開済み fixed 行) があると失敗するため、
先に ``DELETE FROM staff_events WHERE source='fixed'`` が必要 (fixed 行は
次の週生成で再展開される使い捨てデータなので消してよい)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0081_staff_events_external_id_len"
down_revision: str | Sequence[str] | None = "0080_event_defaults_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "staff_events",
        "external_id",
        existing_type=sa.String(40),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "staff_events",
        "external_id",
        existing_type=sa.String(64),
        type_=sa.String(40),
        existing_nullable=True,
    )
