"""staff_event_defaults に重複防止のユニーク制約を追加.

Revision ID: 0080_event_defaults_unique
Revises: 0079_event_templates
Create Date: 2026-08-24

## このマイグレーションの責務

Phase 3 レビュー指摘 (MED) 対応。bulk / 単票 POST の重複スキップは
アプリ層のセット照合のみで、同一内容の同時リクエストが重複行を作れた。
`(staff_id, weekday, start_time, end_time, title)` の完全一致を DB 側でも
一意にする (アプリ層のスキップキーと同一タプル)。

本番の staff_event_defaults は執筆時点で 0 行のため、既存データの
重複解消は不要 (万一に備え upgrade 冒頭で重複行を安全に間引く)。

## downgrade

ユニークインデックスの drop のみ。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0080_event_defaults_unique"
down_revision: str | Sequence[str] | None = "0079_event_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_staff_event_defaults_content"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 既存の完全重複行があればもっとも古い 1 行を残して間引く
        # (本番 0 行想定の保険。ctid は PG 固有)。
        op.execute(
            """
            DELETE FROM staff_event_defaults a
            USING staff_event_defaults b
            WHERE a.staff_id = b.staff_id
              AND a.weekday = b.weekday
              AND a.start_time = b.start_time
              AND a.end_time = b.end_time
              AND a.title = b.title
              AND a.ctid > b.ctid
            """
        )
    op.create_index(
        INDEX_NAME,
        "staff_event_defaults",
        ["staff_id", "weekday", "start_time", "end_time", "title"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="staff_event_defaults")
