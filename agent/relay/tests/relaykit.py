"""中継の試験の道具（本番・ssh・CLI に触れない）。

    PYTHONPATH=agent python -m pytest agent/relay/tests -q -p no:asyncio
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


AGENT = Path(__file__).resolve().parents[2]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from rk.opsbase import Op, register  # noqa: E402

from relay.auth import User  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.execs import record_backup  # noqa: E402
from relay.normalize import ClaudeNormalizer  # noqa: E402
from relay.runner import FakeSession  # noqa: E402
from relay.service import RelayService  # noqa: E402
from relay.store import SqlStore  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 10, 12, 3, 0, 0, tzinfo=timezone.utc)

ADMIN = User(id="u-admin-1", role="admin", username="po")
ADMIN2 = User(id="u-admin-2", role="manager", username="other")
STAFF = User(id="u-staff-1", role="staff", username="staff")
TOKENS = {"tok-admin": ADMIN, "tok-admin2": ADMIN2, "tok-staff": STAFF}


@register
class RelayTestSet(Op):
    """試験だけの操作: 偽の API の辞書に担当を書く。"""
    name = "relay-test-set"
    summary = "担当を替える（中継の試験）"

    def validate(self, op):
        return [] if op.get("visit_id") else ["visit_id がありません"]

    def request(self, op):
        return ("PATCH", f"/t/{op['visit_id']}", {"staff": op["to"]})

    def snapshot(self, api, op):
        return api.get(f"/t/{op['visit_id']}")

    def inverse(self, op, before):
        return [{"op": "relay-test-set", "visit_id": op["visit_id"], "to": before["staff"]}]

    def verify(self, api, op):
        got = api.get(f"/t/{op['visit_id']}")["staff"]
        return [] if got == op["to"] else [f"担当が {got}"]


class FakeBackend:
    """backend の代わり。書き込み（PATCH 等）は記録する＝承認なしで書けていないかを見る。"""

    def __init__(self):
        self.staff = {"v1": "S1", "v2": "S2"}
        self.calls: list[tuple] = []
        self.user_agents: list[tuple] = []

    def api(self, approval=None, approver=None):
        backend = self
        backend.user_agents.append((approval, approver))

        class _Api:
            def get(self, path, params=None):
                backend.calls.append(("GET", path, params))
                if path.startswith("/t/"):
                    return {"staff": backend.staff[path.rsplit("/", 1)[-1]]}
                if path == "/missing":
                    from rk.opsbase import ApiError
                    raise ApiError(404, {"detail": "Not Found"})
                if path == "/auth/me":
                    return {"id": "agent", "role": "admin"}
                return {"path": path, "params": params}

            def call(self, method, path, body=None):
                backend.calls.append((method, path, body))
                if method == "PATCH" and path.startswith("/t/"):
                    backend.staff[path.rsplit("/", 1)[-1]] = body["staff"]
                    return {"ok": True}
                return {"posted": path}

        return _Api()


class FakeSql:
    def __init__(self):
        self.queries = []

    def run(self, sql, params=None):
        from rk.sqlrun import wrap_sql
        wrap_sql(sql)  # 中継の本物と同じ検査
        self.queries.append((sql, params))
        return [{"n": 1}]


class FakeBackup:
    def __init__(self, rk_cfg, clock):
        self.rk_cfg, self.clock, self.taken = rk_cfg, clock, []

    def take(self, label):
        rec = {"label": label, "path": f"/backups/agent-{label}.sql.gz",
               "at_utc": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"), "size": 1_000_000}
        record_backup(self.rk_cfg, rec)
        self.taken.append(rec)
        return rec


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + timedelta(**kw)


def plan(to="S9", title="試験の案", **extra):
    p = {"kind": "relay-test", "title": title, "target": {"week_start": "2026-10-12"},
         "summary": ["v1 の担当を替える"],
         "ops": [{"op": "relay-test-set", "label": "v1 を替える", "visit_id": "v1", "to": to}],
         "checks": [], "warnings": []}
    p.update(extra)
    return p


class Rig:
    """中継一式（SQLite のメモリ・偽の backend・偽の CLI）。"""

    def __init__(self, tmp_path: Path, fixture: str | None = "claude_ask_then_tool.jsonl", **cfg_kw):
        self.clock = Clock()
        self.cfg = RelayConfig(enabled=True, db_url="sqlite://", data_dir=tmp_path, agent_dir=AGENT,
                               allowed_origins=["https://carelink.kaipoke-api.net"], **cfg_kw)
        self.store = SqlStore("sqlite://")
        self.backend = FakeBackend()
        self.sql = FakeSql()
        self.fixture = fixture
        self.runners: list[FakeSession] = []
        self.by_session: dict[str, FakeSession] = {}
        self.svc = RelayService(self.cfg, self.store, runner_factory=self._runner, api_factory=self.backend.api,
                                sql=self.sql, backup=None, now=self.clock, mem_mb=lambda: None)
        self.svc.backup_taker = FakeBackup(self.svc.rk_cfg, self.clock)

    def _runner(self, session, token, resume_id):
        lines = []
        if self.fixture:
            import json
            lines = [json.loads(x) for x in (FIXTURES / self.fixture).read_text(encoding="utf-8").splitlines() if x]
        r = FakeSession(lines, ClaudeNormalizer(allowed_key_sources={"none"}, check_model=False, clock=lambda: 0.0))
        r.rk_token = token
        r.resume_id = resume_id
        self.runners.append(r)
        self.by_session[session["id"]] = r
        return r

    def sent_code(self, session_id: str, kind: str = "反映", timeout: float = 2.0) -> str:
        """中継が CLI に送った承認番号（画面には返さない番号）を、CLI に届いた文から読む。"""
        import re
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.by_session.get(session_id)
            for m in reversed(r.sent if r else []):
                if m.get("type") == "user":
                    found = re.search(r"承認番号 (ap_[A-Za-z0-9_-]+) で" + kind, m["message"]["content"][0]["text"])
                    if found:
                        return found.group(1)
            time.sleep(0.02)
        raise AssertionError("承認番号が CLI に届いていません")

    def session(self, user=ADMIN):
        s = self.store.create_session(user.id)
        return s, self.svc.issue_rk_token(s["id"])


async def fake_auth(token):
    return TOKENS.get(token)
