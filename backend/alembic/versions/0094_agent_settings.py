"""らく助エージェントの中継: 設定 (agent.settings) と管理の記録 (agent.admin_events).

Revision ID: 0094_agent_settings
Revises: 0093_template_all_offices
Create Date: 2026-10-13

PO 2026-10-13: 管理者が画面から、エージェントの AI のつなぎ先 (ChatGPT=Codex / Claude) の
サインイン・サインアウトと、新しい相談で使う既定の AI を選べるようにする。

* ``agent.settings``      小さなキーと値 (例 ``default_provider`` = ``{"v": "codex"}``)。
  中継の ``AGENT_DEFAULT_PROVIDER`` (環境変数) より、ここで選んだものが優先。
* ``agent.admin_events``  誰がいつ、サインインを始めた・終わった・サインアウトした・既定の AI を変えたか。
  トークン・メール・貼り付けたコードは書かない (状態の語だけ)。

中継のモデルは ``agent/relay/db.py`` (SettingRow・AdminEventRow)。``agent_relay`` ロールがあれば GRANT する。
downgrade は 2 表を drop する (既定の AI は環境変数に戻る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0094_agent_settings"
down_revision: str | Sequence[str] | None = "0093_template_all_offices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "agent"
_TS = sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _schema() -> str | None:
    return _SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", _json(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("updated_at", _TS, nullable=False),
        schema=schema,
    )
    op.create_table(
        "admin_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(16), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.create_index("ix_agent_admin_events_ts", "admin_events", ["ts"], schema=schema)

    if schema:
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_relay') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.settings, {_SCHEMA}.admin_events TO agent_relay;
                    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {_SCHEMA} TO agent_relay;
                END IF;
            END $$;
        """))


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_agent_admin_events_ts", table_name="admin_events", schema=schema)
    op.drop_table("admin_events", schema=schema)
    op.drop_table("settings", schema=schema)
