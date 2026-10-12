"""AI のつなぎ先の設定（画面からのサインイン・サインアウト・既定の AI）: 公開口・中継・runner との約束（偽の runner）。"""

import asyncio
import json

from fastapi.testclient import TestClient

from relaykit import Rig, fake_auth
from relay.engines import EngineAdmin, _clean_login
from relay.public import create_public_app

H = {"Authorization": "Bearer tok-admin"}
H2 = {"Authorization": "Bearer tok-admin2"}
HS = {"Authorization": "Bearer tok-staff"}
CODEX_URL = "https://auth.openai.com/codex/device"
CLAUDE_URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c&response_type=code"
              "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=abc")


class FakeRunner:
    """agent_runner.py の返し方を真似る（op ごとに 1 本の接続）。箱の中から来る余計なもの（メール等）も混ぜる。"""

    def __init__(self):
        self.auth = {"codex": "missing", "claude": "ok"}
        self.login = None
        self.ops: list[dict] = []
        self.conns = 0

    def reply(self, op: dict) -> dict:
        self.ops.append(op)
        o = op["op"]
        if o == "auth_status":
            return {"ev": "auth_status", "provider": op["provider"], "state": self.auth[op["provider"]],
                    "email": "po@example.com"}
        if o == "login_start":
            if self.login and self.login["state"] in ("waiting", "verifying"):
                return {"ev": "error", "message": "login busy"}
            p = op["provider"]
            self.login = {"ev": "login", "provider": p, "state": "waiting",
                          "url": CODEX_URL if p == "codex" else CLAUDE_URL,
                          "code": "T9U6-PTFBM" if p == "codex" else None,
                          "needs_input": p == "claude", "expires_in": 899, "error": None,
                          "stdout": "token=sk-secret"}
            return self.login
        if o == "login_poll":
            return self.login or {"ev": "login", "state": "idle"}
        if o == "login_input":
            if not self.login or self.login["provider"] != "claude" or self.login["state"] != "waiting":
                return {"ev": "error", "message": "not waiting for a code"}
            self.login["state"] = "verifying"
            self.login["needs_input"] = False
            return self.login
        if o == "login_cancel":
            if self.login:
                self.login["state"] = "cancelled"
            return self.login or {"ev": "login", "state": "idle"}
        if o == "logout":
            self.auth[op["provider"]] = "missing"
            return {"ev": "logout", "provider": op["provider"], "ok": True, "state": "missing"}
        return {"ev": "error", "message": "unknown op"}

    def finish(self, ok=True):
        self.login["state"] = "done" if ok else "failed"
        self.login["expires_in"] = 0
        if ok:
            self.auth[self.login["provider"]] = "ok"

    async def connect(self, _path):
        self.conns += 1
        runner = self
        reader = asyncio.StreamReader()

        class W:
            def write(self, data):
                for ln in data.decode("utf-8").splitlines():
                    reader.feed_data((json.dumps(runner.reply(json.loads(ln))) + "\n").encode("utf-8"))
                reader.feed_eof()

            async def drain(self):
                return None

            def close(self):
                pass

        return reader, W()


def _rig(tmp_path):
    rig = Rig(tmp_path)
    fake = FakeRunner()
    rig.svc.engines = EngineAdmin("/run/agent/runner.sock", connect=fake.connect)
    return rig, fake


def _client(rig):
    return TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600))


def test_engines_admin_only(tmp_path):
    rig, _ = _rig(tmp_path)
    with _client(rig) as c:
        assert c.get("/agent-relay/engines").status_code == 401
        assert c.get("/agent-relay/engines", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/codex/login", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/codex/logout", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/default", json={"provider": "claude"}, headers=HS).status_code == 403
        r = c.post("/agent-relay/engines/codex/login", headers={**H, "Origin": "https://evil.example"})
        assert r.status_code == 403
    rig.cfg.enabled = False
    with _client(rig) as c:
        assert c.get("/agent-relay/engines", headers=H).status_code == 404


def test_engines_view_and_default_provider(tmp_path):
    rig, _ = _rig(tmp_path)
    with _client(rig) as c:
        v = c.get("/agent-relay/engines", headers=H).json()
        assert v["engines"] == {"codex": {"state": "missing"}, "claude": {"state": "ok"}}
        assert "email" not in json.dumps(v)  # 箱から来た余計なものは画面に出ない
        assert v["default_provider"] == "codex" and v["default_source"] == "env" and v["can_sign_in"]
        assert v["login"] is None
        assert c.post("/agent-relay/engines/default", json={"provider": "gemini"}, headers=H).status_code == 400
        r = c.post("/agent-relay/engines/default", json={"provider": "claude"}, headers=H)
        assert r.json() == {"default_provider": "claude", "default_source": "setting"}
        assert c.get("/agent-relay/engine", headers=H).json()["default_provider"] == "claude"
        s = c.post("/agent-relay/sessions", json={}, headers=H).json()
        assert s["provider"] == "claude"  # 新しい相談は選んだ AI で
        s2 = c.post("/agent-relay/sessions", json={"provider": "codex"}, headers=H).json()
        assert s2["provider"] == "codex"  # 相談ごとに選ぶこともできる
    ev = rig.store.list_admin_events()
    assert ev[0]["action"] == "default_provider" and ev[0]["user_id"] == "u-admin-1"
    assert ev[0]["detail"] == "codex->claude"


def test_codex_sign_in_flow_is_filtered_and_audited(tmp_path):
    rig, fake = _rig(tmp_path)
    with _client(rig) as c:
        r = c.post("/agent-relay/engines/codex/login", headers=H).json()
        assert r == {"provider": "codex", "state": "waiting", "url": CODEX_URL, "code": "T9U6-PTFBM",
                     "needs_input": False, "expires_in": 899, "error": None}
        # 2 人目が同時に始めると断る（同時に 1 つだけ）
        busy = c.post("/agent-relay/engines/claude/login", headers=H2)
        assert busy.status_code == 409 and "途中" in busy.json()["detail"]
        assert c.get("/agent-relay/engines", headers=H).json()["login"]["state"] == "waiting"
        fake.finish(ok=True)
        done = c.get("/agent-relay/engines/login", headers=H).json()
        assert done["state"] == "done"
        v = c.get("/agent-relay/engines", headers=H).json()
        assert v["engines"]["codex"]["state"] == "ok"  # 終わったら様子を読み直す
    acts = [(e["action"], e["provider"], e["user_id"]) for e in reversed(rig.store.list_admin_events())]
    assert acts[0] == ("login_start", "codex", "u-admin-1")
    assert ("login_done", "codex", "u-admin-1") in acts
    assert sum(1 for a in acts if a[0] == "login_done") == 1  # 終わりの記録は 1 回だけ
    assert all("sk-secret" not in json.dumps(e, default=str) for e in rig.store.list_admin_events())


def test_claude_sign_in_paste_code(tmp_path):
    rig, fake = _rig(tmp_path)
    with _client(rig) as c:
        r = c.post("/agent-relay/engines/claude/login", headers=H).json()
        assert r["url"] == CLAUDE_URL and r["needs_input"] and r["code"] is None
        assert c.post("/agent-relay/engines/login/code", json={"code": "bad code!"}, headers=H).status_code == 400
        assert c.post("/agent-relay/engines/login/code", json={}, headers=H).status_code == 400
        assert not any(o["op"] == "login_input" for o in fake.ops)  # 形の違うものは箱へ送らない
        code = "AbCdEf123456_-xyz#stateABC123"
        r = c.post("/agent-relay/engines/login/code", json={"code": f"  {code}\n"}, headers=H).json()
        assert r["state"] == "verifying"
        assert [o for o in fake.ops if o["op"] == "login_input"] == [{"op": "login_input", "data": code}]
        assert c.post("/agent-relay/engines/login/code", json={"code": code}, headers=H).status_code == 409
        fake.finish(ok=False)
        assert c.get("/agent-relay/engines/login", headers=H).json()["state"] == "failed"
    acts = [e["action"] for e in rig.store.list_admin_events()]
    assert "login_code" in acts and "login_failed" in acts
    assert all(code not in json.dumps(e, default=str) for e in rig.store.list_admin_events())  # コードは記録に残さない


def test_cancel_logout_and_rate_limit(tmp_path):
    rig, fake = _rig(tmp_path)
    with _client(rig) as c:
        c.post("/agent-relay/engines/codex/login", headers=H)
        assert c.post("/agent-relay/engines/login/cancel", headers=H).json()["state"] == "cancelled"
        r = c.post("/agent-relay/engines/claude/logout", headers=H).json()
        assert r == {"provider": "claude", "ok": True, "state": "missing"}
        assert c.get("/agent-relay/engines", headers=H).json()["engines"]["claude"]["state"] == "missing"
        assert c.get("/agent-relay/engine", headers=H).json()["login"]["claude"]["state"] == "missing"
        assert c.post("/agent-relay/engines/gemini/logout", headers=H).status_code == 404
        for _ in range(4):
            c.post("/agent-relay/engines/codex/login", headers=H)
            c.post("/agent-relay/engines/login/cancel", headers=H)
        assert c.post("/agent-relay/engines/codex/login", headers=H).status_code == 429
    acts = [e["action"] for e in rig.store.list_admin_events()]
    assert "login_cancel" in acts and "logout" in acts and "login_cancelled" in acts


def test_without_runner_sign_in_is_not_offered(tmp_path):
    rig = Rig(tmp_path)  # 手元（direct）: runner が無い
    with _client(rig) as c:
        v = c.get("/agent-relay/engines", headers=H).json()
        assert v["can_sign_in"] is False and v["engines"]["codex"]["state"] == "unknown"
        r = c.post("/agent-relay/engines/codex/login", headers=H)
        assert r.status_code == 503 and "できません" in r.json()["detail"]


def test_clean_login_drops_unexpected_urls_and_codes():
    bad = _clean_login({"ev": "login", "provider": "codex", "state": "waiting",
                        "url": "https://evil.example/device", "code": "token-sk-xxxxxxxx", "expires_in": 10 ** 6,
                        "needs_input": True, "error": "boom"})
    assert bad["url"] is None and bad["code"] is None and bad["expires_in"] == 900
    assert bad["needs_input"] is False and bad["error"] is None
    assert _clean_login({"provider": "claude", "state": "weird"})["state"] == "failed"
    assert _clean_login({"provider": "claude", "state": "waiting", "url": "https://claude.ai.evil.com/x"})["url"] is None
