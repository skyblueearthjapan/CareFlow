"""AI のつなぎ先の設定（人ごとの席・画面からのサインイン・サインアウト・既定の AI）: 公開口・中継・runner との約束（偽の runner）。

PO 2026-10-13: ChatGPT Business/Team は 1 席 1 人 → 相談は持ち主のログイン（席）で動かし、/engines* は呼んだ本人の席だけ。
"""

import asyncio
import json

from fastapi.testclient import TestClient

from relaykit import ADMIN, ADMIN2, STAFF, Rig, fake_auth
from relay import auth as relay_auth
from relay.config import RelayConfig
from relay.engines import EngineAdmin, _clean_login, seat_key
from relay.public import create_public_app
from relay.service import SIGNIN_JA, sandbox_runner_factory

H = {"Authorization": "Bearer tok-admin"}
H2 = {"Authorization": "Bearer tok-admin2"}
HS = {"Authorization": "Bearer tok-staff"}
K1 = seat_key(ADMIN.id)
K2 = seat_key(ADMIN2.id)
CODEX_URL = "https://auth.openai.com/codex/device"
CLAUDE_URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c&response_type=code"
              "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=abc")


class FakeRunner:
    """agent_runner.py の返し方を真似る（op ごとに 1 本の接続・席ごとのログイン・箱で同時に 1 つのサインイン）。
    箱の中から来る余計なもの（メール等）も混ぜる。"""

    def __init__(self, auth: dict | None = None, legacy: set | None = None):
        self.auth = dict(auth if auth is not None else {(K1, "codex"): "missing", (K1, "claude"): "ok"})
        self.legacy = set(legacy or ())  # 前の共有のログイン（/home/agent/.codex・.claude）
        self.login = None
        self.ops: list[dict] = []
        self.conns = 0
        self.down = False

    def state(self, key, prov):
        return self.auth.get((key, prov), "missing")

    def reply(self, op: dict) -> dict:
        self.ops.append(op)
        o = op["op"]
        key = op.get("user_key")
        if not isinstance(key, str) or len(key) != 32:
            return {"ev": "error", "message": "bad user_key"}
        if o == "auth_status":
            return {"ev": "auth_status", "provider": op["provider"], "state": self.state(key, op["provider"]),
                    "email": "po@example.com"}
        if o == "adopt_legacy":
            moved = []
            for p in sorted(self.legacy):
                if self.state(key, p) == "missing":
                    self.auth[(key, p)] = "ok"
                    moved.append(p)
            self.legacy.clear()
            return {"ev": "adopted", "moved": moved}
        mine = self.login if self.login and self.login["_key"] == key else None
        if o == "login_start":
            if self.login and self.login["state"] in ("waiting", "verifying"):
                return {"ev": "error", "message": "login busy"}
            p = op["provider"]
            self.login = {"ev": "login", "provider": p, "state": "waiting",
                          "url": CODEX_URL if p == "codex" else CLAUDE_URL,
                          "code": "T9U6-PTFBM" if p == "codex" else None,
                          "needs_input": p == "claude", "expires_in": 899, "error": None,
                          "stdout": "token=sk-secret", "_key": key}
            return self.login
        if o == "login_poll":
            return mine or {"ev": "login", "state": "idle"}
        if o == "login_input":
            if not mine or mine["provider"] != "claude" or mine["state"] != "waiting":
                return {"ev": "error", "message": "not waiting for a code"}
            mine["state"] = "verifying"
            mine["needs_input"] = False
            return mine
        if o == "login_cancel":
            if mine:
                mine["state"] = "cancelled"
            return mine or {"ev": "login", "state": "idle"}
        if o == "logout":
            self.auth[(key, op["provider"])] = "missing"
            return {"ev": "logout", "provider": op["provider"], "ok": True, "state": "missing"}
        return {"ev": "error", "message": "unknown op"}

    def finish(self, ok=True):
        self.login["state"] = "done" if ok else "failed"
        self.login["expires_in"] = 0
        if ok:
            self.auth[(self.login["_key"], self.login["provider"])] = "ok"

    async def connect(self, _path):
        if self.down:
            raise OSError("runner down")
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


def _rig(tmp_path, fake=None, **cfg_kw):
    rig = Rig(tmp_path, **cfg_kw)
    fake = fake or FakeRunner()
    rig.svc.engines = EngineAdmin("/run/agent/runner.sock", connect=fake.connect)
    return rig, fake


def _client(rig):
    return TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600))


def test_seat_key_is_opaque_and_stable():
    assert K1 == seat_key(ADMIN.id) and K1 != K2
    assert len(K1) == 32 and all(c in "0123456789abcdef" for c in K1)
    assert ADMIN.id not in K1


def test_engines_admin_only(tmp_path):
    rig, _ = _rig(tmp_path)
    with _client(rig) as c:
        assert c.get("/agent-relay/engines").status_code == 401
        assert c.get("/agent-relay/engines", headers=HS).status_code == 403
        assert c.get("/agent-relay/engines/seats", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/codex/login", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/codex/logout", headers=HS).status_code == 403
        assert c.post("/agent-relay/engines/default", json={"provider": "claude"}, headers=HS).status_code == 403
        r = c.post("/agent-relay/engines/codex/login", headers={**H, "Origin": "https://evil.example"})
        assert r.status_code == 403
    rig.cfg.enabled = False
    with _client(rig) as c:
        assert c.get("/agent-relay/engines", headers=H).status_code == 404


def test_gate_has_a_seam_for_staff_purposes(monkeypatch):
    """いまは相談＝管理者だけ。職員へ限られた目的（例: カルテの下書き）を開くときは AGENT_USES に足すだけ。"""
    assert relay_auth.can_use(ADMIN) and relay_auth.can_use(ADMIN2) and not relay_auth.can_use(STAFF)
    assert not relay_auth.can_use(STAFF, "karte")
    monkeypatch.setitem(relay_auth.AGENT_USES, "karte", frozenset({"admin", "manager", "staff"}))
    assert relay_auth.can_use(STAFF, "karte") and not relay_auth.can_use(STAFF, "consult")


def test_engines_view_is_the_callers_own_seat_and_default(tmp_path):
    rig, fake = _rig(tmp_path)
    with _client(rig) as c:
        v = c.get("/agent-relay/engines", headers=H).json()
        assert v["engines"] == {"codex": {"state": "missing"}, "claude": {"state": "ok"}}
        assert v["seat"] == "own" and v["can_view_seats"] is True
        assert "email" not in json.dumps(v)  # 箱から来た余計なものは画面に出ない
        assert v["default_provider"] == "codex" and v["default_source"] == "env" and v["can_sign_in"]
        assert v["login"] is None
        # 2 人目は自分の席（まだ何もない）
        v2 = c.get("/agent-relay/engines", headers=H2).json()
        assert v2["engines"] == {"codex": {"state": "missing"}, "claude": {"state": "missing"}}
        assert {o["user_key"] for o in fake.ops if o["op"] == "auth_status"} == {K1, K2}
        assert c.post("/agent-relay/engines/default", json={"provider": "gemini"}, headers=H).status_code == 400
        r = c.post("/agent-relay/engines/default", json={"provider": "claude"}, headers=H)
        assert r.json() == {"default_provider": "claude", "default_source": "user"}
        assert c.get("/agent-relay/engine", headers=H).json()["default_provider"] == "claude"
        assert c.get("/agent-relay/engines", headers=H2).json()["default_provider"] == "codex"  # ほかの人には効かない
        s = c.post("/agent-relay/sessions", json={}, headers=H).json()
        assert s["provider"] == "claude"  # 新しい相談はその人が選んだ AI で
        assert c.post("/agent-relay/sessions", json={}, headers=H2).json()["provider"] == "codex"
        s2 = c.post("/agent-relay/sessions", json={"provider": "codex"}, headers=H).json()
        assert s2["provider"] == "codex"  # 相談ごとに選ぶこともできる
    # 全体の既定（agent.settings の default_provider）は、その人の選択が無い人の既定になる
    rig.store.set_setting("default_provider", "claude", "u-admin-1")
    assert rig.svc.default_provider(ADMIN2) == ("claude", "setting")
    assert rig.svc.default_provider(ADMIN) == ("claude", "user")
    ev = rig.store.list_admin_events()
    assert ev[0]["action"] == "default_provider" and ev[0]["user_id"] == "u-admin-1"
    assert ev[0]["detail"] == "codex->claude"


def test_codex_sign_in_flow_is_per_seat_filtered_and_audited(tmp_path):
    rig, fake = _rig(tmp_path)
    with _client(rig) as c:
        r = c.post("/agent-relay/engines/codex/login", headers=H).json()
        assert r == {"provider": "codex", "state": "waiting", "url": CODEX_URL, "code": "T9U6-PTFBM",
                     "needs_input": False, "expires_in": 899, "error": None}
        assert [o for o in fake.ops if o["op"] == "login_start"] == [
            {"op": "login_start", "provider": "codex", "user_key": K1}]
        # 2 人目には 1 人目のサインインは見えない。同時に始めると断る（箱で同時に 1 つ）
        assert c.get("/agent-relay/engines", headers=H2).json()["login"] is None
        assert c.get("/agent-relay/engines/login", headers=H2).json()["state"] == "idle"
        busy = c.post("/agent-relay/engines/claude/login", headers=H2)
        assert busy.status_code == 409 and "途中" in busy.json()["detail"]
        assert c.post("/agent-relay/engines/login/cancel", headers=H2).json()["state"] == "idle"  # 他人のは止めない
        assert c.get("/agent-relay/engines", headers=H).json()["login"]["state"] == "waiting"
        fake.finish(ok=True)
        done = c.get("/agent-relay/engines/login", headers=H).json()
        assert done["state"] == "done"
        assert c.get("/agent-relay/engines", headers=H).json()["engines"]["codex"]["state"] == "ok"
        assert c.get("/agent-relay/engines", headers=H2).json()["engines"]["codex"]["state"] == "missing"
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
        # 他人のサインインにコードは貼れない
        assert c.post("/agent-relay/engines/login/code", json={"code": code}, headers=H2).status_code == 409
        r = c.post("/agent-relay/engines/login/code", json={"code": f"  {code}\n"}, headers=H).json()
        assert r["state"] == "verifying"
        assert [o for o in fake.ops if o["op"] == "login_input" and o["user_key"] == K1] == [
            {"op": "login_input", "data": code, "user_key": K1}]
        assert c.post("/agent-relay/engines/login/code", json={"code": code}, headers=H).status_code == 409
        fake.finish(ok=False)
        assert c.get("/agent-relay/engines/login", headers=H).json()["state"] == "failed"
    acts = [e["action"] for e in rig.store.list_admin_events()]
    assert "login_code" in acts and "login_failed" in acts
    assert all(code not in json.dumps(e, default=str) for e in rig.store.list_admin_events())  # コードは記録に残さない


def test_cancel_logout_own_seat_only_and_rate_limit(tmp_path):
    rig, fake = _rig(tmp_path, FakeRunner({(K1, "claude"): "ok", (K2, "claude"): "ok"}))
    with _client(rig) as c:
        c.post("/agent-relay/engines/codex/login", headers=H)
        assert c.post("/agent-relay/engines/login/cancel", headers=H).json()["state"] == "cancelled"
        r = c.post("/agent-relay/engines/claude/logout", headers=H).json()
        assert r == {"provider": "claude", "ok": True, "state": "missing"}
        assert c.get("/agent-relay/engines", headers=H).json()["engines"]["claude"]["state"] == "missing"
        assert c.get("/agent-relay/engines", headers=H2).json()["engines"]["claude"]["state"] == "ok"  # 他人の席はそのまま
        assert c.get("/agent-relay/engine", headers=H).json()["login"]["claude"]["state"] == "missing"
        assert c.get("/agent-relay/engine", headers=H2).json()["login"] == {}
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


def test_conversation_runs_on_the_owners_seat(tmp_path):
    """runner に頼む spawn には、会話の持ち主の席の鍵だけが入る（パスは渡さない）。"""
    cfg = RelayConfig(enabled=True, launch="runner", runner_socket="/run/agent/runner.sock")
    make = sandbox_runner_factory(cfg)
    for user in (ADMIN, ADMIN2):
        for prov in ("codex", "claude"):
            r = make({"id": "sess-0001-abcdef", "user_id": user.id, "provider": prov}, "t" * 32, None)
            assert r.spawn_msg["user_key"] == seat_key(user.id)
            assert not any("/" in str(v) for k, v in r.spawn_msg.items() if k != "relay_token")


def test_not_signed_in_owner_gets_a_calm_signin_card(tmp_path):
    rig, fake = _rig(tmp_path, fixture="claude_ask_then_tool.jsonl", max_cli=2)
    with _client(rig) as c:
        s = c.post("/agent-relay/sessions", json={"provider": "codex"}, headers=H).json()
        r = c.post(f"/agent-relay/sessions/{s['id']}/messages", json={"text": "こんにちは"}, headers=H)
        assert r.status_code == 409 and r.json()["detail"] == SIGNIN_JA["codex"] == "ChatGPT にサインインすると、相談を始められます"
        evs = rig.store.list_events(s["id"])
        card = [e for e in evs if e["type"] == "error"]
        assert card and card[-1]["code"] == "signin" and card[-1]["provider"] == "codex" and not card[-1]["retry"]
        assert not rig.runners  # CLI は起こしていない
        # その人の Claude（サインイン済み）なら始められる。別の人のログインでは動かない
        s2 = c.post("/agent-relay/sessions", json={"provider": "claude"}, headers=H2).json()
        r2 = c.post(f"/agent-relay/sessions/{s2['id']}/messages", json={"text": "こんにちは"}, headers=H2)
        assert r2.status_code == 409 and r2.json()["detail"] == SIGNIN_JA["claude"]
        s3 = c.post("/agent-relay/sessions", json={"provider": "claude"}, headers=H).json()
        r3 = c.post(f"/agent-relay/sessions/{s3['id']}/messages", json={"text": "こんにちは"}, headers=H)
        assert r3.status_code == 200, r3.text
        assert len(rig.runners) == 1
        # サインインしたら、同じ相談をそのまま始められる
        fake.auth[(K1, "codex")] = "ok"
        rig.svc.engines.forget(K1)
        r4 = c.post(f"/agent-relay/sessions/{s['id']}/messages", json={"text": "もう一度"}, headers=H)
        assert r4.status_code == 200 and len(rig.runners) == 2


def test_legacy_shared_login_moves_to_the_owners_seat_once(tmp_path):
    fake = FakeRunner(auth={}, legacy={"codex", "claude"})
    rig, _ = _rig(tmp_path, fake, legacy_owner_user_id=ADMIN.id)
    with _client(rig) as c:
        # だれが最初に開いても、移す先は持ち主の席だけ
        v2 = c.get("/agent-relay/engines", headers=H2).json()
        assert v2["engines"]["codex"]["state"] == "missing"
        v1 = c.get("/agent-relay/engines", headers=H).json()
        assert v1["engines"] == {"codex": {"state": "ok"}, "claude": {"state": "ok"}}
        c.get("/agent-relay/engines", headers=H)
    adopts = [o for o in fake.ops if o["op"] == "adopt_legacy"]
    assert adopts == [{"op": "adopt_legacy", "user_key": K1}]  # 1 回だけ
    ev = [e for e in rig.store.list_admin_events() if e["action"] == "legacy_adopted"]
    assert len(ev) == 1 and ev[0]["user_id"] == ADMIN.id and ev[0]["detail"] == "claude,codex"


def test_legacy_owner_from_setting_and_retry_when_runner_is_down(tmp_path):
    fake = FakeRunner(auth={}, legacy={"codex"})
    rig, _ = _rig(tmp_path, fake)
    rig.store.set_setting("legacy_owner_user_id", ADMIN.id, "po")
    fake.down = True
    assert asyncio.run(rig.svc.adopt_legacy_seat()) == [] and not rig.svc._legacy_done  # 届かない → 次にまた
    fake.down = False
    assert asyncio.run(rig.svc.adopt_legacy_seat()) == ["codex"]
    assert asyncio.run(rig.svc.adopt_legacy_seat()) == []
    assert fake.state(K1, "codex") == "ok" and fake.state(K2, "codex") == "missing"


def test_no_legacy_owner_means_nothing_moves(tmp_path):
    fake = FakeRunner(auth={}, legacy={"codex"})
    rig, _ = _rig(tmp_path, fake)
    with _client(rig) as c:
        c.get("/agent-relay/engines", headers=H)
    assert not any(o["op"] == "adopt_legacy" for o in fake.ops) and fake.legacy == {"codex"}


def test_admin_seats_view_lists_admins_by_name_with_state_words(tmp_path):
    rig, fake = _rig(tmp_path, FakeRunner({(K1, "codex"): "ok", (K2, "claude"): "api_key"}))

    class Sql:
        queries: list = []

        def run(self, sql, params=None):
            self.queries.append(sql)
            return [{"id": ADMIN.id, "name": "今泉"}, {"id": ADMIN2.id, "name": "佐藤"}]

    rig.svc.sql = Sql()
    with _client(rig) as c:
        v = c.get("/agent-relay/engines/seats", headers=H2).json()
    assert v["providers"] == ["codex", "claude"]
    assert v["seats"] == [
        {"name": "今泉", "me": False, "engines": {"codex": {"state": "ok"}, "claude": {"state": "missing"}}},
        {"name": "佐藤", "me": True, "engines": {"codex": {"state": "missing"}, "claude": {"state": "api_key"}}},
    ]
    dump = json.dumps(v, ensure_ascii=False)
    assert "email" not in dump and "@" not in dump and ADMIN.id not in dump  # AI のメール・user id は出さない
    assert "role in ('admin', 'manager')" in Sql.queries[0] and "password" not in Sql.queries[0]


def test_admin_seats_view_without_sql_shows_only_me(tmp_path):
    rig, _ = _rig(tmp_path)

    class Broken:
        def run(self, sql, params=None):
            raise RuntimeError("no db")

    rig.svc.sql = Broken()
    with _client(rig) as c:
        v = c.get("/agent-relay/engines/seats", headers=H).json()
    assert [s["name"] for s in v["seats"]] == ["po"] and v["seats"][0]["me"]


def test_clean_login_drops_unexpected_urls_and_codes():
    bad = _clean_login({"ev": "login", "provider": "codex", "state": "waiting",
                        "url": "https://evil.example/device", "code": "token-sk-xxxxxxxx", "expires_in": 10 ** 6,
                        "needs_input": True, "error": "boom"})
    assert bad["url"] is None and bad["code"] is None and bad["expires_in"] == 900
    assert bad["needs_input"] is False and bad["error"] is None
    assert _clean_login({"provider": "claude", "state": "weird"})["state"] == "failed"
    assert _clean_login({"provider": "claude", "state": "waiting", "url": "https://claude.ai.evil.com/x"})["url"] is None
