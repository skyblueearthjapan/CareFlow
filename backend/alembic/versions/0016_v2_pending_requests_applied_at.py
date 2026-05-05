"""W7-BE3: pending_requests.applied_at カラム追加 (冪等性強化).

Revision ID: 0016_v2_pending_requests_applied_at
Revises: 0015_v2_drop_legacy_backup_tables
Create Date: 2026-05-06

設計仕様書 v0.9 §3.5.3 / §4.4 / Codex Must-fix #5 に対応する冪等性強化
マイグレーション。

## このマイグレーションの責務

承認ワークフローの **同時 approve 競合** を防ぐため、``pending_requests``
テーブルに ``applied_at`` カラムを追加する。

### 冪等性強化の設計 (W7-BE3)

W2-BE5 時点では ``status='approved'`` で冪等性を表現していたが、
以下の問題が残っていた:

1. **同時 approve の race**: HTTP layer の ``status`` チェックと UPDATE が
   別ステートメントになり、2 リクエストが同時に到着すると両方が
   pending を見て両方反映されてしまう。
2. **applier 側 no-op の判定が ``approved_at IS NOT NULL`` 依存**:
   業務反映と status 更新の境界が曖昧。

### 本マイグレーションで導入するもの

| カラム | 型 | nullable | 用途 |
|---|---|---|---|
| ``applied_at`` | ``timestamptz`` | YES (NULL = 未反映) | 業務反映完了の確定タイムスタンプ |

API 層 (W7-BE3) は以下の手順で冪等性を担保する:

1. ``SELECT ... FOR UPDATE`` で行ロック (PG only)
2. ``WHERE applied_at IS NULL AND status = 'pending'`` の条件付き UPDATE
   - 0 行更新なら他の TX が先行 → 409
   - 1 行更新なら本 TX が勝者 → applier 実行 → commit

### Alembic chain での位置付け

- ``Wave 7`` の sequential phase で実行
- 番号は予約表で **0016** に予約済み (W7-BE3)
- ``down_revision = "0015_v2_drop_legacy_backup_tables"``

## SQLite 互換性

``applied_at`` は ``timestamptz`` (PG) / ``DATETIME`` (SQLite) として
追加される。``ADD COLUMN`` は SQLite でも安全な操作 (table rebuild 不要)。

## downgrade

``applied_at`` カラムを DROP するのみ。データは失われるが、status カラムで
冪等性を表現していた W2-BE5 時点の状態に戻る。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0016_v2_pending_requests_applied_at"
down_revision: Union[str, Sequence[str], None] = "0015_v2_drop_legacy_backup_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """``pending_requests.applied_at`` カラムを追加する."""
    op.add_column(
        "pending_requests",
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="業務反映完了タイムスタンプ (W7-BE3 冪等性). NULL = 未反映",
        ),
    )

    # 既存の approved レコードは applied_at = approved_at で埋める (back-fill).
    # rejected / pending には触らない。
    op.execute(
        sa.text(
            "UPDATE pending_requests "
            "SET applied_at = approved_at "
            "WHERE status = 'approved' AND approved_at IS NOT NULL"
        )
    )

    # 部分インデックス: applied_at IS NULL かつ status = 'pending' の絞り込みを高速化。
    # SQLite は部分インデックスを 3.8 以降サポート、PG は問題なし。
    op.create_index(
        "ix_pending_requests_pending_unapplied",
        "pending_requests",
        ["id"],
        unique=False,
        postgresql_where=sa.text("applied_at IS NULL AND status = 'pending'"),
        sqlite_where=sa.text("applied_at IS NULL AND status = 'pending'"),
    )


def downgrade() -> None:
    """``applied_at`` カラムと部分インデックスを削除する."""
    op.drop_index("ix_pending_requests_pending_unapplied", table_name="pending_requests")
    op.drop_column("pending_requests", "applied_at")
