"""ログインのアカウント (users) に名前 (display_name) を付けられるようにする。

Revision ID: 0095_user_display_name
Revises: 0094_agent_settings
Create Date: 2026-10-13

PO 2026-10-13: どのアカウントにも「名前（画面に出す呼び名）」を付けられるようにする。
ヘッダーのアカウントの表示・管理者の一覧・らく助エージェントの席の一覧で使う。

* ``users.display_name`` VARCHAR(60) NULL を足すだけ。
* 既存の行は空 (NULL) のまま。名前は後で管理者が画面 (/admin/users) で書き入れる
  (PO 2026-10-13: 職員名を写す backfill はしない)。空のあいだ画面はログイン ID を出す。

downgrade は列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0095_user_display_name"
down_revision: str | Sequence[str] | None = "0094_agent_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
