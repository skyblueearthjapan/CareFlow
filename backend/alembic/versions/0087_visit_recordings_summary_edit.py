"""visit_recordings に要約の人手修正の痕跡 (summary_edited_by / summary_edited_at) を追加.

Revision ID: 0087_visit_recordings_summary_edit
Revises: 0086_visit_recordings
Create Date: 2026-09-18

## このマイグレーションの責務

正典設計書 ``docs/plans/visit-voice-record-design-2026-09-17.md`` §11-2
(Phase 2-B) の「``PATCH`` に ``summary_text``（人手修正・``summary_edited_by/at``
列を 0087 で追加）」。

* ``summary_text`` は AI が書いた要約でもあり、人が直した要約でもある。列が 1 本
  しか無いと **画面でどちらを見ているのか分からない**。「誰がいつ直したか」を
  併せて持つことで、一覧・詳細に「修正済み」を出せるようにする。
* 再処理 (``POST /visit-recordings/{id}/retry``) は AI に要約を書き直させるので、
  その時点で ``summary_edited_*`` は嘘になる → API 側でクリアする。消える人手
  修正は ``summary['previous_manual']`` へ退避する (``error_message`` に混ぜない
  — あれは失敗の文言の置き場で、次の成功で上書きされる)。

列を 2 本足すだけ。既存行は NULL = 「人手修正なし」で意味が通るため backfill は
しない。index も貼らない (絞り込みの主語にはならず、表示のための属性)。

## downgrade

追加した 2 列を drop するだけ (人手修正の痕跡は失われるが、``summary_text``
本体は残る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0087_visit_recordings_summary_edit"
down_revision: str | Sequence[str] | None = "0086_visit_recordings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "visit_recordings"


def _uuid_type():
    """0086 と同じ uuid 表現 (PG=uuid / それ以外=varchar(36)) を返す。"""
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=True)
    return sa.String(36)


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "summary_edited_by",
            _uuid_type(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="要約を人手で直した利用者 (NULL = AI のまま)",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "summary_edited_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="要約を人手で直した日時 (再処理でクリアされる)",
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "summary_edited_at")
    op.drop_column(_TABLE, "summary_edited_by")
