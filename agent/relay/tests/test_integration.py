"""段 3 の組み合わせ（P3-A 中継 × P3-B 画面 × P3-C 置き方）の約束を確かめる。

- 画面との約束: healthz・名前変更・plan_ref・approval.request・承認番号を返さない・印刷用の署名つき URL
- 置き方との約束: agent/deploy の環境変数の名前・DSN・runner で起こす・箱の網の判定
- 箱の鍵: API キーの変数を CLI に渡さない・apiKeySource は "none"（サブスク）だけ
"""

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relaykit import AGENT, FIXTURES, Rig, fake_auth, plan
from relay.checkpoint import create_checkpoint_app
from relay.config import RelayConfig, sqlalchemy_url
from relay.normalize import ClaudeNormalizer
from relay.public import PLAN_CSP, create_public_app
from relay.runner import FORBIDDEN_ENV, RunnerSession, claude_env
from relay.service import RelayService, default_runner_factory
from relay.store import SqlStore

from rk.transport import RELAY_TOKEN_HEADER

H = {"Authorization": "Bearer tok-admin"}
H2 = {"Authorization": "Bearer tok-admin2"}


def _pub(rig, **kw):
    return TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600), **kw)


def _wait(rig, sid, pred, n=300):
    for _ in range(n):
        evs = rig.store.list_events(sid)
        if pred(evs):
            return evs
        time.sleep(0.01)
    raise AssertionError([e["type"] for e in rig.store.list_events(sid)])


# ---------------- 画面との約束 ----------------
def test_healthz_without_auth(tmp_path):
    rig = Rig(tmp_path)
    rig.cfg.enabled = False  # 全体の止めでも healthz は 200（compose の healthcheck 用）
    with _pub(rig) as c:
        r = c.get("/agent-relay/healthz")
        assert r.status_code == 200 and r.json() == {"ok": True}


def test_rename_pin_and_needs_you(tmp_path):
    rig = Rig(tmp_path)
    with _pub(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        r = c.patch(f"/agent-relay/sessions/{sid}", json={"title": "  週43   の相談 ", "pinned": True}, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "週43 の相談" and r.json()["pinned"] is True
        assert c.patch(f"/agent-relay/sessions/{sid}", json={"title": " "}, headers=H).status_code == 400
        assert c.patch(f"/agent-relay/sessions/{sid}", json={"title": "x"}, headers=H2).status_code == 404  # 本人だけ
        rig.store.update_session(sid, status="waiting_approval")
        lst = c.get("/agent-relay/sessions", headers=H).json()
        assert lst[0]["needs_you"] is True and "cli_session_id" not in lst[0]


def test_plan_ref_approval_request_and_no_code_to_browser(tmp_path):
    rig = Rig(tmp_path)
    with _pub(rig) as c, TestClient(create_checkpoint_app(rig.svc)) as cp:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "休みの案を"}, headers=H).status_code == 200
        tok = {RELAY_TOKEN_HEADER: rig.by_session[sid].rk_token}
        assert cp.post("/rk/plans", json={"plan": plan()}, headers=tok).status_code == 200
        # 取った会話は最初に色を聞く → 先にカードで答えておく（答え待ちの間の文はカードの答えになるため）
        q = next(e for e in _wait(rig, sid, lambda es: any(e["type"] == "question" for e in es))
                 if e["type"] == "question")
        assert c.post(f"/agent-relay/sessions/{sid}/answers", json={"question_id": q["question_id"], "choice": "青"},
                      headers=H).status_code == 200
        evs = _wait(rig, sid, lambda es: any(e["type"] == "approval.request" for e in es))
        req = next(e for e in evs if e["type"] == "approval.request")
        assert req["kind"] == "apply" and req["version"] == 1 and req["plan_id"]
        types = [e["type"] for e in evs]
        assert types.index("plan") < types.index("approval.request")

        # 案について書く（plan_ref = 版の番号）→ 画面の記録と CLI の文
        r = c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "朝を少なく", "plan_ref": 1,
                                                                  "device": "mobile"}, headers=H)
        assert r.status_code == 200, r.text
        um = [e for e in rig.store.list_events(sid) if e["type"] == "user.message"][-1]
        assert um["plan_ref"] == 1 and um["device"] == "mobile"
        assert any(m.get("type") == "user" and "【案 1 について】朝を少なく" in m["message"]["content"][0]["text"]
                   for m in rig.by_session[sid].sent)
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "x", "plan_ref": 9},
                      headers=H).status_code == 404
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "x", "plan_ref": "1"},
                      headers=H).status_code == 400

        # 承認: 画面への返事にも、流れ（SSE）にも承認番号は出ない。CLI にだけ届く
        r = c.post(f"/agent-relay/sessions/{sid}/plans/1/approve", json={"device": "pc"}, headers=H)
        assert r.status_code == 200 and set(r.json()) == {"ok", "version", "expires_at"}
        code = rig.sent_code(sid)
        assert code.startswith("ap_")
        with c.stream("GET", f"/agent-relay/sessions/{sid}/events?after=0&follow=false", headers=H) as s:
            body = "".join(s.iter_text())
        assert code not in body and "approval.result" in body


def test_print_url_signed_short_lived(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    with _pub(rig) as c, TestClient(create_checkpoint_app(rig.svc)) as cp:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "案を"}, headers=H)
        tok = {RELAY_TOKEN_HEADER: rig.by_session[sid].rk_token}
        cp.post("/rk/plans", json={"plan": plan()}, headers=tok)
        r = c.post(f"/agent-relay/sessions/{sid}/plans/1/print-url", json={"size": "a4"}, headers=H)
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert url.startswith(f"/agent-relay/print/{sid}/1?")
        page = c.get(url)  # 鍵（Bearer）なしで開ける
        assert page.status_code == 200 and page.headers["content-security-policy"] == PLAN_CSP
        assert c.get(url.replace("sig=", "sig=0")).status_code == 403          # 署名を書き換えた
        assert c.get(url.replace("size=a4", "size=a3")).status_code == 403    # 別の大きさ
        assert c.post(f"/agent-relay/sessions/{sid}/plans/1/print-url", json={}, headers=H2).status_code == 404
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + 301)               # 5 分を過ぎた
        assert c.get(url).status_code == 403


def test_sandbox_subnet_split(tmp_path):
    rig = Rig(tmp_path, sandbox_subnet="172.31.250.0/24")
    # 内側口: 箱の網からだけ
    with TestClient(create_checkpoint_app(rig.svc), client=("172.24.0.5", 5000)) as cp:
        assert cp.post("/rk/progress", json={"text": "x"}).status_code == 403
    with TestClient(create_checkpoint_app(rig.svc), client=("172.31.250.3", 5000)) as cp:
        assert cp.post("/rk/progress", json={"text": "x"}).status_code == 401  # 網は通る・合言葉が無い
    # 公開口: 箱の網からは使えない（healthz も）
    with _pub(rig, client=("172.31.250.3", 5000)) as c:
        assert c.get("/agent-relay/healthz").status_code == 404
    with _pub(rig, client=("172.24.0.1", 5000)) as c:
        assert c.get("/agent-relay/healthz").status_code == 200


# ---------------- 置き方との約束 ----------------
DEPLOY_ENV = {
    # docker-compose.agent.yml の environment
    "AGENT_PUBLIC_PORT": "8090", "AGENT_INNER_PORT": "8091",
    "AGENT_BACKEND_URL": "http://stg-backend:8000/api/v1", "AGENT_PG_HOST": "stg-postgres", "AGENT_PG_DB": "carelink",
    "AGENT_RUNNER_SOCKET": "/run/agent/runner.sock", "AGENT_SANDBOX_SUBNET": "172.31.251.0/24",
    "AGENT_PUBLIC_ORIGIN": "http://127.0.0.1:3000", "AGENT_BACKUP_DIR": "/backups", "AGENT_MAX_PROCS": "1",
    "AGENT_MIN_MEM_AVAILABLE_MB": "900",
    # set-role-passwords.sh の秘密ファイル
    "AGENT_RO_DSN": "postgresql://agent_ro:r%2Fo@stg-postgres:5432/carelink",
    "AGENT_DUMP_DSN": "postgresql://agent_dump:d@stg-postgres:5432/carelink",
    "AGENT_RELAY_DSN": "postgresql://agent_relay:w@stg-postgres:5432/carelink",
    "AGENT_URL_SIGNING_KEY": "k" * 64,
    "AGENT_BACKEND_IDENTIFIER": "rakusuke-agent", "AGENT_BACKEND_PASSWORD": "pw",
    "AGENT_ALLOWED_USER_IDS": "u1,u2",
}


def test_config_reads_deploy_names(monkeypatch):
    for k in list(DEPLOY_ENV) + ["RK_IDENTIFIER", "RK_PASSWORD", "AGENT_RELAY_LAUNCH", "AGENT_RELAY_DB_URL"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in DEPLOY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AGENT_ALLOWED_KEY_SOURCES", "none,ANTHROPIC_API_KEY,apiKeyHelper")
    c = RelayConfig.from_env()
    assert (c.public_port, c.internal_port) == (8090, 8091)
    assert c.backend_url == "http://stg-backend:8000/api/v1"
    assert c.allowed_origins == ["http://127.0.0.1:3000"]
    assert c.db_url == "postgresql+psycopg://agent_relay:w@stg-postgres:5432/carelink"
    assert (c.ro.host, c.ro.user, c.ro.password, c.ro.database) == ("stg-postgres", "agent_ro", "r/o", "carelink")
    assert c.dump.user == "agent_dump"
    assert c.launch == "runner" and c.runner_socket == "/run/agent/runner.sock"
    assert c.in_sandbox("172.31.251.9") and not c.in_sandbox("172.24.0.2")
    assert c.url_signing_key == "k" * 64 and c.allowed_user_ids == {"u1", "u2"}
    assert c.allowed_key_sources == {"none"}  # サブスクだけ（API キーは書いても無視）
    assert c.backup_dir == Path("/backups") and c.max_cli == 1 and c.min_mem_mb == 900
    import os
    assert os.environ["RK_IDENTIFIER"] == "rakusuke-agent" and os.environ["RK_PASSWORD"] == "pw"
    svc = RelayService(c, SqlStore("sqlite://"))
    assert default_runner_factory(c).__qualname__.startswith("sandbox_runner_factory")
    assert svc.runner_factory.__qualname__.startswith("sandbox_runner_factory")


def test_config_direct_mode_by_default(monkeypatch):
    for k in ("AGENT_RUNNER_SOCKET", "AGENT_RELAY_LAUNCH"):
        monkeypatch.delenv(k, raising=False)
    assert RelayConfig.from_env().launch == "direct"
    monkeypatch.setenv("AGENT_RELAY_LAUNCH", "runner")
    with pytest.raises(ValueError):
        RelayConfig.from_env()
    assert sqlalchemy_url("sqlite:///x.db") == "sqlite:///x.db"


# ---------------- 箱の鍵 ----------------
def test_api_key_source_must_be_none():
    for src in (None, "ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key"):
        n = ClaudeNormalizer(check_model=False)
        msg = {"type": "system", "subtype": "init", "session_id": "x", "model": "claude-opus-5-5"}
        if src is not None:
            msg["apiKeySource"] = src
        assert [e["type"] for e in n.feed(msg)][-1] == "_abort", src
    n = ClaudeNormalizer(check_model=False)
    out = n.feed({"type": "system", "subtype": "init", "session_id": "x", "model": "claude-opus-5-5",
                  "apiKeySource": "none"})
    assert "_abort" not in [e["type"] for e in out]


def test_direct_env_has_no_api_keys(monkeypatch):
    for k in FORBIDDEN_ENV:
        monkeypatch.setenv(k, "leak")
    env = claude_env(home="/home/agent", cwd="/srv/agent", relay_url="http://r:8091", rk_token="t" * 32,
                     work_dir="/data/sessions/s/work", https_proxy="http://p:3128")
    assert not set(env) & set(FORBIDDEN_ENV)


def _load_runner_module():
    p = AGENT / "deploy" / "runner" / "agent_runner.py"
    spec = importlib.util.spec_from_file_location("agent_runner_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sandbox_runner_scrubs_api_keys(monkeypatch, tmp_path):
    mod = _load_runner_module()
    assert set(FORBIDDEN_ENV) == set(mod.FORBIDDEN_ENV)  # 中継と箱で一覧がずれない
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    dropped = mod.scrub_own_env()
    assert set(dropped) == {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
    import os
    assert "ANTHROPIC_API_KEY" not in os.environ
    for provider in ("claude", "codex"):
        env = mod.build_env(tmp_path, provider, "t" * 32, "a" * 32)
        assert not set(env) & set(mod.FORBIDDEN_ENV)
    # 中継の spawn の形を runner が受ける（session_id は uuid・token は token_urlsafe(24)）
    import secrets
    import uuid
    msg = {"op": "spawn", "session_id": str(uuid.uuid4()), "provider": "claude", "model": "opus",
           "relay_token": secrets.token_urlsafe(24), "resume_id": str(uuid.uuid4())}
    import pytest
    with pytest.raises(ValueError, match="user_key"):
        mod.validate_spawn(msg)  # 持ち主の席の鍵が無い spawn は起こさない
    from relay.engines import seat_key
    msg["user_key"] = seat_key("c3889e7b-6571-4347-8bc6-7c286b464031")
    assert mod.validate_spawn(msg)[0] == msg["session_id"] and mod.validate_spawn(msg)[-1] == msg["user_key"]
    for bad in ("../../etc", "A" * 32, "a" * 31, "/home/agent/.codex", 123):
        with pytest.raises(ValueError):
            mod.validate_spawn({**msg, "user_key": bad})
    assert (AGENT / "relay" / "relay-prompt.md").is_file()  # runner が --append-system-prompt-file に渡す


# ---------------- runner 経由の会話（unix ソケットの代わりにメモリの流れ） ----------------
class _FakeRunnerConn:
    """agent_runner.py の返し方を真似る: spawn → spawned、stdin の user 行 → 記録の 1 ターン、close_stdin → exit。"""

    def __init__(self, turns):
        self.reader = asyncio.StreamReader()
        self.turns = list(turns)
        self.ops: list[dict] = []
        self.closed = False

    def _out(self, obj):
        self.reader.feed_data((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    # writer の形
    def write(self, data: bytes):
        for ln in data.decode("utf-8").splitlines():
            op = json.loads(ln)
            self.ops.append(op)
            if op["op"] == "spawn":
                self._out({"ev": "spawned", "pid": 42})
            elif op["op"] == "stdin":
                m = json.loads(op["data"])
                if m.get("type") == "user" and self.turns:
                    for line in self.turns.pop(0):
                        self._out({"ev": "out", "line": json.dumps(line, ensure_ascii=False)})
                    self._out({"ev": "out", "line": "not json from the sandbox"})  # 信用しない入力は捨てる
            elif op["op"] in ("close_stdin", "kill"):
                self._out({"ev": "exit", "code": 0, "stderr_tail": ""})
                self.reader.feed_eof()

    async def drain(self):
        return None

    def close(self):
        self.closed = True


def test_runner_session_streams_fixture():
    lines = [json.loads(x) for x in (FIXTURES / "claude_sql_list_partial.jsonl").read_text(encoding="utf-8").splitlines()
             if x.strip()]
    box = {}

    async def connect(_path):
        conn = box["conn"] = _FakeRunnerConn([lines])
        return conn.reader, conn

    async def go():
        s = RunnerSession("/run/agent/runner.sock",
                          {"session_id": "s" * 8, "provider": "claude", "model": "opus", "relay_token": "t" * 32},
                          ClaudeNormalizer(check_model=False, clock=lambda: 0.0), connect=connect, stop_wait=1.0)
        await s.start()
        await s.send("こんにちは")
        got = []

        async def collect():
            async for e in s.events():
                got.append(e)
                if e.get("type") == "turn.end":
                    await s.close()
        await asyncio.wait_for(collect(), 5)
        return got

    got = asyncio.run(go())
    conn = box["conn"]
    expected = json.loads((FIXTURES / "claude_sql_list_partial.expected.json").read_text(encoding="utf-8"))
    public = [e for e in got if not e["type"].startswith("_")]
    assert public == [e for e in expected if not e["type"].startswith("_")]
    assert got[-1]["type"] == "_exit"
    assert conn.ops[0] == {"op": "spawn", "session_id": "s" * 8, "provider": "claude", "model": "opus",
                           "relay_token": "t" * 32}
    assert {o["op"] for o in conn.ops} >= {"spawn", "stdin", "close_stdin"}
    assert conn.closed


def test_runner_session_spawn_refused():
    class Refuse(_FakeRunnerConn):
        def write(self, data):
            self._out({"ev": "error", "message": "busy"})

    async def connect(_path):
        conn = Refuse([])
        return conn.reader, conn

    async def go():
        s = RunnerSession("/x", {"session_id": "s" * 8, "provider": "claude", "model": "opus",
                                 "relay_token": "t" * 32}, ClaudeNormalizer(), connect=connect)
        with pytest.raises(RuntimeError, match="busy"):
            await s.start()

    asyncio.run(go())


def test_cors_only_for_allowed_origin(tmp_path):
    rig = Rig(tmp_path)
    rig.cfg.allowed_origins = ["http://127.0.0.1:3000"]
    with _pub(rig) as c:
        ok = c.options("/agent-relay/sessions", headers={"Origin": "http://127.0.0.1:3000",
                                                         "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "authorization,content-type"})
        assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
        bad = c.options("/agent-relay/sessions", headers={"Origin": "https://evil.example",
                                                          "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in bad.headers
