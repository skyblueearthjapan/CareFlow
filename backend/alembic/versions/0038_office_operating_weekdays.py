"""Phase G-45: offices.operating_weekdays JSONB カラム追加.

Revision ID: 0038_office_operating_weekdays
Revises: 0037_g21_pfv_is_pinned_and_same_address_links
Create Date: 2026-05-24

## このマイグレーションの責務

拠点 (Office) ごとの稼働曜日を保持する ``operating_weekdays`` カラムを追加する.

* 型: ``JSONB`` (PG) / ``JSON`` (SQLite テスト) — 0..6 の int 配列
* default: ``[0, 1, 2, 3, 4, 5]`` (= 月-土)
* NOT NULL — 後方互換のため、 ADD COLUMN 時に server_default で全行を埋める.

### 既存データ更新

* 全 office を default ``[0..5]`` で埋めた後、 都賀 (code='TSUGA' or name LIKE '都賀%')
  のみ ``[0..4]`` (= 月-金) に UPDATE する.
* 稲毛は default のまま (= 月-土).

### Backend pipeline 連動

このカラムは ``_load_office_operating_weekdays`` (= scheduling.auto_allocator_v2) で
読み出し、 V2Visit 生成時に休業曜日を skip するために使用する. 詳細は Phase G-45
設計書を参照.

### SQLite 互換

* PG: ``postgresql.JSONB`` + ``server_default=text("'[0,1,2,3,4,5]'::jsonb")``
* SQLite: ``sa.JSON`` + ``server_default=text("'[0,1,2,3,4,5]'")``

### downgrade

* operating_weekdays カラムを drop する. 都賀の上書きデータも失われる.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0038_office_operating_weekdays"
down_revision: Union[str, Sequence[str], None] = "0037_g21_pfv_is_pinned_and_same_address_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_OP_WEEKDAYS_PG = sa.text("'[0,1,2,3,4,5]'::jsonb")
_DEFAULT_OP_WEEKDAYS_SQLITE = sa.text("'[0,1,2,3,4,5]'")
_TSUGA_OP_WEEKDAYS_PG = sa.text("'[0,1,2,3,4]'::jsonb")
_TSUGA_OP_WEEKDAYS_SQLITE = sa.text("'[0,1,2,3,4]'")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.add_column(
            "offices",
            sa.Column(
                "operating_weekdays",
                postgresql.JSONB(),
                nullable=False,
                server_default=_DEFAULT_OP_WEEKDAYS_PG,
                comment=(
                    "Phase G-45: 拠点稼働曜日 (0=月..6=日 の int 配列). "
                    "default [0..5] (月-土). 休業曜日はパイプラインで V2Visit "
                    "生成を skip + staff 応援判定で secondary_office にフォールバック."
                ),
            ),
        )
        # 都賀のみ [0..4] (= 月-金) に更新.
        op.execute(
            "UPDATE offices SET operating_weekdays = '[0,1,2,3,4]'::jsonb "
            "WHERE code = 'TSUGA' OR name LIKE '都賀%'"
        )
    else:
        # SQLite: JSON 型. server_default は文字列リテラル.
        op.add_column(
            "offices",
            sa.Column(
                "operating_weekdays",
                sa.JSON(),
                nullable=False,
                server_default=_DEFAULT_OP_WEEKDAYS_SQLITE,
            ),
        )
        op.execute(
            "UPDATE offices SET operating_weekdays = '[0,1,2,3,4]' "
            "WHERE code = 'TSUGA' OR name LIKE '都賀%'"
        )


def downgrade() -> None:
    """注意: 本番でこの migration を巻き戻すと operating_weekdays データは復元できない.

    drop_column は破壊的操作のため、 列ごとデータが消える.
    復元するには:
      1) 本 0038 を再 upgrade して列を再追加し、
      2) UPDATE 文を手動で適用して当該 office の operating_weekdays を再投入する.

    本番運用では downgrade を実行する前に必ず ``pg_dump`` で offices テーブルの
    バックアップを取得すること.
    """
    op.drop_column("offices", "operating_weekdays")
