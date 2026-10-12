"""型の作業場: 全拠点の下書き (office_id を NULL 可) と、最適化の報告 (steps.summary).

Revision ID: 0093_template_all_offices
Revises: 0092_template_drafts
Create Date: 2026-10-13

* ``template_drafts.office_id`` を NULL 可にする。NULL = 全拠点の下書き
  (どの拠点かは ``params.offices`` / 拠点ごとの対象の方は ``params.scopes``)。1 拠点の下書きは今までどおり。
* ``template_draft_steps.summary`` (JSON・NULL 可) を足す。最適化の手順ごとに
  「何をどう変えたか」の報告 (種類ごとの件数・曜日ごとの物差し・移動の増減の内訳・説明文) を残す。

downgrade は全拠点の下書き (office_id IS NULL) を消してから NOT NULL に戻し、summary を落とす。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093_template_all_offices"
down_revision: str | Sequence[str] | None = "0092_template_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()


def upgrade() -> None:
    with op.batch_alter_table("template_drafts") as b:
        b.alter_column("office_id", existing_type=_uuid(), nullable=True)
    with op.batch_alter_table("template_draft_steps") as b:
        b.add_column(sa.Column("summary", _json(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM template_drafts WHERE office_id IS NULL")
    with op.batch_alter_table("template_draft_steps") as b:
        b.drop_column("summary")
    with op.batch_alter_table("template_drafts") as b:
        b.alter_column("office_id", existing_type=_uuid(), nullable=False)
