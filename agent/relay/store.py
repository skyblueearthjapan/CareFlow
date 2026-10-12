"""記録の窓口（Store）。中継の中身はこの形だけを使う（DB を差し替えられるように）。

``SqlStore`` は SQLAlchemy（本番 = PostgreSQL の agent スキーマ・試験 = SQLite のメモリ）。結果はいつも dict で返す。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from relay.db import (AdminEventRow, ApprovalRow, Base, EventRow, JournalRow, PlanRow, RunRow, SessionRow, SettingRow,
                      ToolCallRow)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    if d is not None and d.tzinfo is None:  # SQLite は tz を落とす
        return d.replace(tzinfo=timezone.utc)
    return d


def _row(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.key)
        out[c.key] = _aware(v) if isinstance(v, datetime) else v
    return out


class Store(Protocol):
    def create_session(self, user_id: str, title: str = "", provider: str = "claude", model: str = "opus") -> dict: ...
    def get_session(self, session_id: str) -> dict | None: ...
    def list_sessions(self, user_id: str) -> list[dict]: ...
    def update_session(self, session_id: str, **fields: Any) -> None: ...
    def append_event(self, session_id: str, turn_id: int, event: dict) -> dict: ...
    def list_events(self, session_id: str, after: int = 0, limit: int = 5000) -> list[dict]: ...
    def add_plan(self, session_id: str, **fields: Any) -> dict: ...
    def get_plan(self, session_id: str, version: int) -> dict | None: ...
    def latest_plan(self, session_id: str) -> dict | None: ...
    def set_plan_status(self, plan_id: str, status: str) -> None: ...
    def add_approval(self, **fields: Any) -> dict: ...
    def get_approval(self, approval_id: str) -> dict | None: ...
    def replace_open_approvals(self, session_id: str, kind: str, plan_id: str | None, run_id: str | None) -> None: ...
    def consume_approval(self, approval_id: str, at: datetime) -> bool: ...
    def set_approval_run(self, approval_id: str, run_id: str) -> None: ...
    def add_run(self, **fields: Any) -> dict: ...
    def get_run(self, run_id: str) -> dict | None: ...
    def update_run(self, run_id: str, **fields: Any) -> None: ...
    def add_tool_call(self, session_id: str, **fields: Any) -> None: ...
    def list_tool_calls(self, session_id: str) -> list[dict]: ...
    def add_journal(self, **fields: Any) -> dict: ...
    def list_journal(self, office: str, since: datetime | None = None, include_retired: bool = False,
                     limit: int = 1000) -> list[dict]: ...
    def get_journal(self, office: str, entry_id: str) -> dict | None: ...
    def retire_journal(self, office: str, entry_id: str, reason: str) -> bool: ...
    def get_setting(self, key: str) -> Any: ...
    def set_setting(self, key: str, value: Any, user_id: str | None = None) -> None: ...
    def add_admin_event(self, user_id: str, action: str, provider: str | None = None, detail: str = "") -> None: ...
    def list_admin_events(self, limit: int = 100) -> list[dict]: ...


class SqlStore:
    def __init__(self, url: str, create: bool | None = None):
        sqlite = url.startswith("sqlite")
        kw: dict[str, Any] = {"future": True}
        if sqlite:
            kw["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
                kw["poolclass"] = StaticPool
        engine = sa.create_engine(url, **kw)
        if sqlite:
            engine = engine.execution_options(schema_translate_map={"agent": None})
        self.engine = engine
        self._sm = sessionmaker(engine, expire_on_commit=False)
        self._lock = threading.RLock()  # seq の採番・承認の消費を 1 つずつ（中継は 1 プロセス）
        if create if create is not None else sqlite:
            Base.metadata.create_all(engine)

    def _s(self) -> Session:
        return self._sm()

    # ---------- 会話 ----------
    def create_session(self, user_id: str, title: str = "", provider: str = "claude", model: str = "opus") -> dict:
        now = utcnow()
        row = SessionRow(id=str(uuid.uuid4()), user_id=str(user_id), title=title or "新しい相談", provider=provider,
                         model=model, status="idle", pinned=False, created_at=now, last_active_at=now)
        with self._s() as s, s.begin():
            s.add(row)
        return _row(row)

    def get_session(self, session_id: str) -> dict | None:
        with self._s() as s:
            r = s.get(SessionRow, session_id)
            return _row(r) if r else None

    def list_sessions(self, user_id: str) -> list[dict]:
        with self._s() as s:
            q = (sa.select(SessionRow).where(SessionRow.user_id == str(user_id), SessionRow.archived_at.is_(None))
                 .order_by(SessionRow.pinned.desc(), SessionRow.last_active_at.desc()))
            return [_row(r) for r in s.scalars(q)]

    def update_session(self, session_id: str, **fields: Any) -> None:
        with self._s() as s, s.begin():
            s.execute(sa.update(SessionRow).where(SessionRow.id == session_id).values(**fields))

    # ---------- イベント ----------
    def append_event(self, session_id: str, turn_id: int, event: dict) -> dict:
        with self._lock, self._s() as s, s.begin():
            seq = (s.scalar(sa.select(sa.func.max(EventRow.seq)).where(EventRow.session_id == session_id)) or 0) + 1
            now = utcnow()
            full = {"seq": seq, "ts": now.isoformat(), "session_id": session_id, "turn_id": turn_id, **event}
            s.add(EventRow(session_id=session_id, seq=seq, type=str(event.get("type")), payload=full, created_at=now))
        return full

    def list_events(self, session_id: str, after: int = 0, limit: int = 5000) -> list[dict]:
        with self._s() as s:
            q = (sa.select(EventRow.payload).where(EventRow.session_id == session_id, EventRow.seq > after)
                 .order_by(EventRow.seq).limit(limit))
            return list(s.scalars(q))

    # ---------- 案 ----------
    def add_plan(self, session_id: str, **fields: Any) -> dict:
        with self._lock, self._s() as s, s.begin():
            last = s.scalar(sa.select(sa.func.max(PlanRow.version)).where(PlanRow.session_id == session_id)) or 0
            s.execute(sa.update(PlanRow).where(PlanRow.session_id == session_id,
                                               PlanRow.status.in_(["draft", "approved"]))
                      .values(status="superseded"))
            row = PlanRow(id=str(uuid.uuid4()), session_id=session_id, version=last + 1, created_at=utcnow(),
                          status=fields.pop("status", "draft"), **fields)
            s.add(row)
            # 前の版への開いた承認は使えなくする
            s.execute(sa.update(ApprovalRow).where(ApprovalRow.session_id == session_id, ApprovalRow.kind == "apply",
                                                   ApprovalRow.consumed_at.is_(None),
                                                   ApprovalRow.decision == "approve")
                      .values(decision="replaced"))
        return _row(row)

    def get_plan(self, session_id: str, version: int) -> dict | None:
        with self._s() as s:
            r = s.scalar(sa.select(PlanRow).where(PlanRow.session_id == session_id, PlanRow.version == version))
            return _row(r) if r else None

    def get_plan_by_id(self, plan_id: str) -> dict | None:
        with self._s() as s:
            r = s.get(PlanRow, plan_id)
            return _row(r) if r else None

    def latest_plan(self, session_id: str) -> dict | None:
        with self._s() as s:
            r = s.scalar(sa.select(PlanRow).where(PlanRow.session_id == session_id)
                         .order_by(PlanRow.version.desc()).limit(1))
            return _row(r) if r else None

    def list_plans(self, session_id: str) -> list[dict]:
        with self._s() as s:
            q = sa.select(PlanRow).where(PlanRow.session_id == session_id).order_by(PlanRow.version)
            return [_row(r) for r in s.scalars(q)]

    def set_plan_status(self, plan_id: str, status: str) -> None:
        with self._s() as s, s.begin():
            s.execute(sa.update(PlanRow).where(PlanRow.id == plan_id).values(status=status))

    # ---------- 承認 ----------
    def add_approval(self, **fields: Any) -> dict:
        row = ApprovalRow(created_at=utcnow(), **fields)
        with self._s() as s, s.begin():
            s.add(row)
        return _row(row)

    def get_approval(self, approval_id: str) -> dict | None:
        with self._s() as s:
            r = s.get(ApprovalRow, approval_id)
            return _row(r) if r else None

    def replace_open_approvals(self, session_id: str, kind: str, plan_id: str | None, run_id: str | None) -> None:
        with self._s() as s, s.begin():
            q = sa.update(ApprovalRow).where(ApprovalRow.session_id == session_id, ApprovalRow.kind == kind,
                                             ApprovalRow.consumed_at.is_(None), ApprovalRow.decision == "approve")
            if plan_id is not None:
                q = q.where(ApprovalRow.plan_id == plan_id)
            if run_id is not None:
                q = q.where(ApprovalRow.target_run_id == run_id)
            s.execute(q.values(decision="replaced"))

    def consume_approval(self, approval_id: str, at: datetime) -> bool:
        """使用済みにする。もう使われていれば False（同じ番号で 2 回反映できない）。"""
        with self._lock, self._s() as s, s.begin():
            res = s.execute(sa.update(ApprovalRow).where(ApprovalRow.id == approval_id,
                                                         ApprovalRow.consumed_at.is_(None))
                            .values(consumed_at=at))
            return res.rowcount == 1

    def set_approval_run(self, approval_id: str, run_id: str) -> None:
        with self._s() as s, s.begin():
            s.execute(sa.update(ApprovalRow).where(ApprovalRow.id == approval_id).values(run_id=run_id))

    # ---------- 反映 ----------
    def add_run(self, **fields: Any) -> dict:
        row = RunRow(**fields)
        with self._s() as s, s.begin():
            s.add(row)
        return _row(row)

    def get_run(self, run_id: str) -> dict | None:
        with self._s() as s:
            r = s.get(RunRow, run_id)
            return _row(r) if r else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        with self._s() as s, s.begin():
            s.execute(sa.update(RunRow).where(RunRow.id == run_id).values(**fields))

    # ---------- 読んだものの記録 ----------
    def add_tool_call(self, session_id: str, **fields: Any) -> None:
        with self._s() as s, s.begin():
            s.add(ToolCallRow(session_id=session_id, created_at=utcnow(), **fields))

    def list_tool_calls(self, session_id: str) -> list[dict]:
        with self._s() as s:
            q = sa.select(ToolCallRow).where(ToolCallRow.session_id == session_id).order_by(ToolCallRow.id)
            return [_row(r) for r in s.scalars(q)]

    # ---------- 相談の引き継ぎ帳 ----------
    def add_journal(self, **fields: Any) -> dict:
        row = JournalRow(**fields)
        with self._s() as s, s.begin():
            s.add(row)
        return _row(row)

    def list_journal(self, office: str, since: datetime | None = None, include_retired: bool = False,
                     limit: int = 1000) -> list[dict]:
        with self._s() as s:
            q = sa.select(JournalRow).where(JournalRow.office == office)
            if since is not None:
                q = q.where(JournalRow.ts >= since)
            if not include_retired:
                q = q.where(JournalRow.retired_at.is_(None))
            return [_row(r) for r in s.scalars(q.order_by(JournalRow.ts.desc()).limit(limit))]

    def get_journal(self, office: str, entry_id: str) -> dict | None:
        with self._s() as s:
            r = s.get(JournalRow, entry_id)
            return _row(r) if r and r.office == office else None

    def retire_journal(self, office: str, entry_id: str, reason: str) -> bool:
        with self._s() as s, s.begin():
            res = s.execute(sa.update(JournalRow).where(JournalRow.id == entry_id, JournalRow.office == office)
                            .values(retired_at=utcnow(), retired_reason=reason[:200]))
            return res.rowcount == 1

    # ---------- 設定・管理の記録 ----------
    def get_setting(self, key: str) -> Any:
        with self._s() as s:
            r = s.get(SettingRow, key)
            return r.value.get("v") if r is not None and isinstance(r.value, dict) else None

    def set_setting(self, key: str, value: Any, user_id: str | None = None) -> None:
        with self._lock, self._s() as s, s.begin():
            r = s.get(SettingRow, key)
            if r is None:
                s.add(SettingRow(key=key, value={"v": value}, updated_by=user_id, updated_at=utcnow()))
            else:
                r.value, r.updated_by, r.updated_at = {"v": value}, user_id, utcnow()

    def add_admin_event(self, user_id: str, action: str, provider: str | None = None, detail: str = "") -> None:
        with self._s() as s, s.begin():
            s.add(AdminEventRow(ts=utcnow(), user_id=str(user_id), action=action[:32], provider=provider,
                                detail=detail[:500]))

    def list_admin_events(self, limit: int = 100) -> list[dict]:
        with self._s() as s:
            q = sa.select(AdminEventRow).order_by(AdminEventRow.id.desc()).limit(limit)
            return [_row(r) for r in s.scalars(q)]
