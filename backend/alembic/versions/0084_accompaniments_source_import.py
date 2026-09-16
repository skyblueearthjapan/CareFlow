"""accompaniments.source に 'import' (カイポケ取込由来) を許可する.

Revision ID: 0084_accompaniments_source_import
Revises: 0083_kaipoke_csv_snapshots_division
Create Date: 2026-09-16

## このマイグレーションの責務

カイポケ取込 (差分 ``inbound.py`` / 置換 ``replace_inbound.py``) は、職員名2 が
**新人** だったときに ``accompaniments`` へ同行リンクを自動生成する
(案B② / 一般化 §3-5)。これまではこの行にも ``source='manual'`` を刻んでいた —
``ck_acc_source`` が ``('default', 'manual')`` の 2 値しか許さず、値を増やすには
本マイグレーションが必要だったため (当時のコメントにもその旨が残っていた)。

結果として「画面から人が張ったリンク」と「取込が機械的に張ったリンク」が
区別できず、取込の副作用を後から追えなかった (2026-09-16 W38 取込のダメ出し)。
CHECK を 3 値へ広げ、取込由来には ``'import'`` を刻めるようにする。

* ``'default'`` … 週生成時の既定 (accompaniment_defaults) 展開由来。
* ``'manual'``  … 画面からの手動追加。
* ``'import'``  … カイポケ取込 (差分/置換) の自動生成。**新規**。

既存行は書き換えない (過去の取込由来リンクは ``'manual'`` のまま = 区別が付くのは
本番適用以降の取込から)。``source`` を読んでいるのは API の表示
(``/accompaniments`` のレスポンス) だけで、分岐条件には使っていないため、
値が増えても既存の挙動は変わらない。

## ロックと非 PostgreSQL

CHECK の張り替えは ``ACCESS EXCLUSIVE`` を一瞬取る (accompaniments は小さいテーブル
なので実質ゼロ停止)。CHECK 制約名で DDL を打つため、0072 と同じく PostgreSQL 以外
(SQLite のテスト環境) ではスキップする — SQLite は ``ALTER TABLE ... DROP
CONSTRAINT`` を持たず、モデル側の CHECK も効かないため実害がない。

## downgrade

先に取込由来の行を ``'manual'`` へ戻してから CHECK を 2 値に戻す
(戻さないと CHECK 追加が失敗する)。区別が消えるだけでリンク自体は無傷。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0084_accompaniments_source_import"
down_revision: str | Sequence[str] | None = "0083_kaipoke_csv_snapshots_division"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_acc_source"
_TABLE = "accompaniments"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _drop_check() -> None:
    """CHECK を落とす (**存在するものだけ**).

    制約名は環境によって付いていない / 既に別名の可能性があるため、
    0072 と同じく ``DROP CONSTRAINT IF EXISTS`` で落として本体を止めない。
    """
    op.execute(f'ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS "{_CK}"')


def upgrade() -> None:
    if not _is_pg():
        return
    _drop_check()
    op.create_check_constraint(_CK, _TABLE, "source IN ('default', 'manual', 'import')")


def downgrade() -> None:
    if not _is_pg():
        return
    op.execute("UPDATE accompaniments SET source = 'manual' WHERE source = 'import'")
    _drop_check()
    op.create_check_constraint(_CK, _TABLE, "source IN ('default', 'manual')")
