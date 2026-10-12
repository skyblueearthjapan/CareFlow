"""らく助エージェントの中継の記録: 別スキーマ ``agent`` (6 表).

Revision ID: 0091_agent_schema
Revises: 0090_visit_signatures
Create Date: 2026-10-12

## このマイグレーションの責務

正典設計書 ``docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md`` §1-6 (PO 決定 D3)。

らく助エージェントの中継 (``agent/relay``) が、相談・画面用イベント・案・承認・反映・
読んだものの記録を、らく助の DB の **別スキーマ** ``agent`` に置く (毎日のバックアップに入る・
SQL で監査できる)。アプリ本体 (backend) の表には触らない。

* ``agent.sessions``  相談 (作った本人だけが見られる・D8)。``resolved_model`` は CLI の init が返した実際の版。
* ``agent.events``    画面用の共通イベントをそのまま追記 (``payload`` jsonb・``(session_id, seq)`` UNIQUE)。
* ``agent.plans``     案の版ごと (``plan_sha256``・A4/A3 の HTML・状態)。``(session_id, version)`` UNIQUE。
* ``agent.approvals`` 承認番号 ``ap_…`` (15 分・1 回限り・案の sha256 と版と相談と人に結ぶ)。
* ``agent.runs``      反映・元に戻すの記録 (rk の run_id・バックアップ名・log・undo)。
* ``agent.tool_calls`` エージェントが読んだもの (GET・読むだけの POST・SQL・断ったもの)。
* ``agent.journal_entries`` 相談の引き継ぎ帳 (事業所ごと・会話が消えても残す・外部キーなし・保存期間なし。
  PO 2026-10-12。名前を含みうるのでこのスキーマの中だけ。`rk journal` が中継の内側口経由で読み書きする)。

id は文字列 (相談・案 = uuid の文字列・承認 = ``ap_…``・反映 = rk の run_id)。
``user_id`` は backend の ``users.id`` の文字列で、**外部キーは張らない** (別スキーマ・
ユーザーを消しても監査の記録は残す)。中継のモデルは ``agent/relay/db.py`` (この形と同じ)。

保存期間 (D8): events・tool_calls 90 日 / plans・approvals・runs 1 年。掃除は中継の定時処理 (UTC)。

## ロール (このマイグレーションでは作らない)

``agent_ro`` / ``agent_dump`` / ``agent_relay`` の 3 ロール (D2) はパスワードを伴うインフラの変更なので、
deploy 側の SQL で作る。``agent_relay`` が ``agent`` スキーマを読み書きできるよう、ロールがあれば
ここで GRANT する (無ければ何もしない＝ローカル・CI でも流せる)。

## downgrade

``agent`` スキーマの 7 表を drop し、スキーマも落とす (中の記録は消える。戻す前にバックアップ)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0091_agent_schema"
down_revision: str | Sequence[str] | None = "0090_visit_signatures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "agent"
_TS = sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _schema() -> str | None:
    """SQLite (テスト) にはスキーマが無いので None。"""
    return _SCHEMA if op.get_bind().dialect.name == "postgresql" else None


def upgrade() -> None:
    schema = _schema()
    if schema:
        op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
    fk = (lambda t: f"{_SCHEMA}.{t}") if schema else (lambda t: t)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(16), nullable=False, server_default="claude"),
        sa.Column("model", sa.String(64), nullable=False, server_default="opus"),
        sa.Column("resolved_model", sa.String(128), nullable=True),
        sa.Column("cli_session_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="idle"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_active_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", _TS, nullable=True),
        sa.CheckConstraint("provider IN ('claude','codex')", name="ck_agent_sessions_provider"),
        schema=schema,
    )
    op.create_index("ix_agent_sessions_user_active", "sessions", ["user_id", "last_active_at"], schema=schema)

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey(f"{fk('sessions')}.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_events_session_seq"),
        schema=schema,
    )
    op.create_index("ix_agent_events_created_at", "events", ["created_at"], schema=schema)

    op.create_table(
        "plans",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey(f"{fk('sessions')}.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("plan_json", _json(), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("check_result", _json(), nullable=False),
        sa.Column("basis_hash", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "version", name="uq_agent_plans_session_version"),
        sa.CheckConstraint(
            "status IN ('draft','approved','applying','applied','failed','rejected','stale','superseded')",
            name="ck_agent_plans_status"),
        schema=schema,
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey(f"{fk('sessions')}.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("plan_id", sa.String(40), sa.ForeignKey(f"{fk('plans')}.id", ondelete="CASCADE"), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=True),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("target_run_id", sa.String(80), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("device", sa.String(16), nullable=False, server_default="pc"),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("consumed_at", _TS, nullable=True),
        sa.Column("run_id", sa.String(80), nullable=True),
        sa.CheckConstraint("kind IN ('apply','undo')", name="ck_agent_approvals_kind"),
        sa.CheckConstraint("decision IN ('approve','replaced','revoked')", name="ck_agent_approvals_decision"),
        schema=schema,
    )
    op.create_index("ix_agent_approvals_session", "approvals", ["session_id", "kind"], schema=schema)

    op.create_table(
        "runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey(f"{fk('sessions')}.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("plan_id", sa.String(40), nullable=True),
        sa.Column("approval_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("backup_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("log", _json(), nullable=False),
        sa.Column("undo", _json(), nullable=True),
        sa.Column("result", _json(), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        schema=schema,
    )
    op.create_index("ix_agent_runs_session", "runs", ["session_id"], schema=schema)

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey(f"{fk('sessions')}.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("sql_head", sa.Text(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        schema=schema,
    )
    op.create_index("ix_agent_tool_calls_session", "tool_calls", ["session_id"], schema=schema)
    op.create_index("ix_agent_tool_calls_created_at", "tool_calls", ["created_at"], schema=schema)

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("office", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(40), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("asked", sa.Text(), nullable=False, server_default=""),
        sa.Column("tried", sa.Text(), nullable=False, server_default=""),
        sa.Column("said", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("next_time", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", _json(), nullable=False),
        sa.Column("plans", _json(), nullable=False),
        sa.Column("retired_at", _TS, nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("outcome IN ('applied','plan-only','declined','unanswered')",
                           name="ck_agent_journal_outcome"),
        schema=schema,
    )
    op.create_index("ix_agent_journal_office_ts", "journal_entries", ["office", "ts"], schema=schema)

    if schema:
        # agent_relay ロールがあれば agent スキーマだけ読み書きできるようにする (無ければ何もしない)。
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_relay') THEN
                    GRANT USAGE ON SCHEMA {_SCHEMA} TO agent_relay;
                    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_SCHEMA} TO agent_relay;
                    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {_SCHEMA} TO agent_relay;
                END IF;
            END $$;
        """))


def downgrade() -> None:
    schema = _schema()
    for name in ("journal_entries", "tool_calls", "runs", "approvals", "plans", "events", "sessions"):
        op.drop_table(name, schema=schema)
    if schema:
        op.execute(sa.text(f"DROP SCHEMA IF EXISTS {_SCHEMA}"))
