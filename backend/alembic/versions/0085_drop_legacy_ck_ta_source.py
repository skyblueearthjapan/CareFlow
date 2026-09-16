"""accompaniments に残っていた旧名の CHECK (ck_ta_source = 2 値) を除去する.

Revision ID: 0085_drop_legacy_ck_ta_source
Revises: 0084_accompaniments_source_import
Create Date: 2026-09-17

## 経緯 (2026-09-17 本番で発見)

0072 で ``trainee_accompaniments`` → ``accompaniments`` に改名した際、CHECK 制約の
名前掃除は「存在するものだけ」だったため、命名規約でプレフィックスが付いた
``ck_trainee_accompaniments_ck_ta_source`` (``source IN ('default','manual')``) が
本番に残っていた。0084 は ``ck_acc_source`` を 3 値に広げたが、この旧制約が
同じ列に残ったままなので、取込が ``source='import'`` の同行リンクを作った瞬間に
旧制約違反で取込全体がロールバックする (0084 適用直後の本番検査で発見・
まだ実行前)。

同列を縛る旧名の CHECK を **存在するものだけ** 落とす。他の ``ck_ta_*``
(target_type / visit_presence / course_presence) は内容が現行と同じで害が無いため
触らない。

## downgrade

旧制約は復元しない (0084 の 3 値 CHECK が同列を縛っており、2 値へ戻すのは
0084 の downgrade の責務)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0085_drop_legacy_ck_ta_source"
down_revision: str | Sequence[str] | None = "0084_accompaniments_source_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "accompaniments"
_LEGACY_NAMES = (
    "ck_trainee_accompaniments_ck_ta_source",
    "ck_ta_source",
)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_pg():
        return
    for name in _LEGACY_NAMES:
        op.execute(f'ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS "{name}"')


def downgrade() -> None:
    # 旧制約は復元しない (docstring 参照)。
    return
