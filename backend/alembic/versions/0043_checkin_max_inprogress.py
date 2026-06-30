"""QR 訪問チェックイン Phase 4: checkin_settings.max_inprogress_min 列追加.

Revision ID: 0043_checkin_max_inprogress
Revises: 0042_revoked_qr_tokens
Create Date: 2026-07-01

## このマイグレーションの責務

退出忘れ (長時間 inprogress) しきい値を **6 つ目の全社一律設定** に格上げする。
Phase 3 までは ``monitor.MAX_INPROGRESS_MIN = 240`` / ``constants.ts`` に
ハードコードされていた値を ``checkin_settings`` の設定列にする (additive)。

* ``checkin_settings`` に ``max_inprogress_min`` (Integer NULL) を追加。
  NULL = コード既定 240 fallback (他の設定列と同じ nullable 既定方式)。
* 範囲 CHECK ``ck_checkin_settings_max_inprogress_range`` (30..1440)。NULL は
  CHECK をすり抜ける (既定 fallback と整合)。

## SQLite 互換

upgrade は **インライン列 CHECK 付き ADD COLUMN** を 1 文の標準 SQL で実行する。
PG / SQLite いずれも単一の ``ALTER TABLE ADD COLUMN ... CONSTRAINT ... CHECK``
構文を解釈でき、テーブル再構築 (= シングルトン部分 UNIQUE index の reflection) を
回避できる。downgrade は SQLite が「CHECK に参照される列」を直接 DROP できないため
dialect 分岐する (PG = 制約 drop + 列 drop、SQLite = batch 再構築で列 drop)。

## downgrade

``max_inprogress_min`` 列と CHECK を drop する。downgrade で設定値は失われる。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0043_checkin_max_inprogress"
down_revision: Union[str, Sequence[str], None] = "0042_revoked_qr_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK_NAME = "ck_checkin_settings_max_inprogress_range"
_CHECK_SQL = (
    "max_inprogress_min IS NULL OR "
    "(max_inprogress_min >= 30 AND max_inprogress_min <= 1440)"
)


def upgrade() -> None:
    # インライン列 CHECK 付き ADD COLUMN (PG / SQLite 共通の標準 SQL)。
    # 1 文の ALTER で済むため SQLite でもテーブル再構築 (部分 UNIQUE index の
    # reflection) を回避できる。
    op.execute(
        "ALTER TABLE checkin_settings ADD COLUMN max_inprogress_min INTEGER "
        f"CONSTRAINT {_CHECK_NAME} CHECK ({_CHECK_SQL})"
    )


def downgrade() -> None:
    """注意: 本番でこの migration を巻き戻すと max_inprogress_min 設定は失われる."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        op.drop_constraint(_CHECK_NAME, "checkin_settings", type_="check")
        op.drop_column("checkin_settings", "max_inprogress_min")
    else:
        # SQLite は CHECK に参照される列を直接 DROP できないため batch 再構築。
        # 再構築は reflection した CHECK をそのまま引き継ぐので、列と一緒に明示的に
        # 制約も drop しないと「no such column」になる。
        with op.batch_alter_table("checkin_settings") as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
            batch.drop_column("max_inprogress_min")
