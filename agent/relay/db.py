"""会話の記録の表（らく助の DB の ``agent`` スキーマ・設計 §1-6・migration 0091_agent_schema）。

PostgreSQL では ``agent.*``、試験の SQLite ではスキーマなし（``schema_translate_map``）。
id は文字列（会話・案は uuid の文字列、承認は ``ap_…``、反映は rk の run_id）。user_id は backend の users.id の文字列。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "agent"
JSONType = sa.JSON().with_variant(JSONB(), "postgresql")
BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


class Base(DeclarativeBase):
    metadata = sa.MetaData(schema=SCHEMA)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    title: Mapped[str] = mapped_column(sa.Text, default="")
    provider: Mapped[str] = mapped_column(sa.String(16), default="claude")
    model: Mapped[str] = mapped_column(sa.String(64), default="opus")
    resolved_model: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    cli_session_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(24), default="idle")
    pinned: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS)
    last_active_at: Mapped[datetime] = mapped_column(TS)
    archived_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (sa.UniqueConstraint("session_id", "seq", name="uq_agent_events_session_seq"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(sa.Integer)
    type: Mapped[str] = mapped_column(sa.String(40))
    payload: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS)


class PlanRow(Base):
    __tablename__ = "plans"
    __table_args__ = (sa.UniqueConstraint("session_id", "version", name="uq_agent_plans_session_version"),)
    id: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(sa.Integer)
    kind: Mapped[str] = mapped_column(sa.String(64))
    title: Mapped[str] = mapped_column(sa.Text)
    plan_json: Mapped[dict] = mapped_column(JSONType)
    plan_sha256: Mapped[str] = mapped_column(sa.String(64))
    check_result: Mapped[dict] = mapped_column(JSONType)
    basis_hash: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    html_a4: Mapped[str] = mapped_column(sa.Text)
    html_a3: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16))  # draft/approved/applying/applied/failed/rejected/stale/superseded
    created_at: Mapped[datetime] = mapped_column(TS)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(sa.String(80), primary_key=True)  # ap_…
    session_id: Mapped[str] = mapped_column(sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"))
    plan_id: Mapped[str | None] = mapped_column(sa.String(40), sa.ForeignKey("plans.id", ondelete="CASCADE"),
                                                nullable=True)
    plan_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    plan_sha256: Mapped[str] = mapped_column(sa.String(64))
    kind: Mapped[str] = mapped_column(sa.String(8))  # apply / undo
    target_run_id: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)  # undo のとき
    user_id: Mapped[str] = mapped_column(sa.String(64))
    device: Mapped[str] = mapped_column(sa.String(16), default="pc")
    decision: Mapped[str] = mapped_column(sa.String(16))  # approve / replaced / revoked
    created_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    consumed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    run_id: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(sa.String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"))
    plan_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    approval_id: Mapped[str] = mapped_column(sa.String(80))
    kind: Mapped[str] = mapped_column(sa.String(8))
    backup_path: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16))  # ok / stopped / undone
    log: Mapped[list] = mapped_column(JSONType)
    undo: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict] = mapped_column(JSONType)
    started_at: Mapped[datetime] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                                            index=True)
    kind: Mapped[str] = mapped_column(sa.String(16))  # get / post-read / sql / refused
    target: Mapped[str] = mapped_column(sa.Text)
    sql_head: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    rows: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    status: Mapped[int] = mapped_column(sa.Integer)
    ms: Mapped[int] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
