"""カルテの下書きを Claude の席でも（PO 2026-10-13）: AI の選び方・⚙ の選択・claude -p の 1 回きりの起こし方。

- 使う AI: ⚙ で選んだもの（未サインインなら signed_out で Gemini）／選んでいなければ席の人の既定の AI →
  サインインしている方
- Claude は runner の purpose=karte（--tools ""・--no-session-persistence・--max-turns 1・空の作業フォルダー）
- 答えの provider（codex / claude）を backend に返す（backend が codex-seat / claude-seat に分けて保存）
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from relaykit import ADMIN, ADMIN2, Rig, fake_auth
from relay.engines import seat_key
from relay.karte import SETTING_PROVIDER, SETTING_SEAT, KarteDrafter, instructions
from relay.normalize import ClaudeNormalizer
from relay.public import create_public_app
from relay.runner import FakeSession, RunnerSession
from test_integration import _load_runner_module
from test_karte import BODY, SUMMARY, FakeKarteSession

K1 = seat_key(ADMIN.id)


def _rig(tmp_path, states):
    rig = Rig(tmp_path)

    async def status(provider, key, fresh=False):
        return states.get((key, provider), "missing")

    rig.svc.engines.status = status
    rig.svc.engines.forget = lambda *a, **k: None
    made = []

    def factory(session_id, key, provider):
        s = FakeKarteSession()
        made.append((key, provider, s))
        return s

    rig.karte = KarteDrafter(rig.svc, session_factory=factory, timeout=5)
    rig.made = made

    class Sql:
        def run(self, sql, params=None):
            return [{"id": ADMIN.id, "name": "A004"}, {"id": ADMIN2.id, "name": "A002"}]

    rig.svc.sql = Sql()
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    return rig


@pytest.mark.parametrize("states,pref,want", [
    ({(K1, "codex"): "ok", (K1, "claude"): "ok"}, "claude", "claude"),   # 席の人の既定の AI
    ({(K1, "codex"): "ok", (K1, "claude"): "ok"}, None, "codex"),        # 既定なし → 並びの先
    ({(K1, "codex"): "missing", (K1, "claude"): "ok"}, None, "claude"),  # サインインしている方
    ({(K1, "codex"): "ok", (K1, "claude"): "missing"}, "claude", "codex"),
])
def test_auto_picks_default_then_signed_in(tmp_path, states, pref, want):
    rig = _rig(tmp_path, states)
    if pref:
        rig.store.set_setting(f"default_provider:{ADMIN.id}", pref)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] and r["provider"] == want and rig.made[0][1] == want


def test_chosen_ai_signed_out_falls_back_without_switching(tmp_path):
    rig = _rig(tmp_path, {(K1, "codex"): "ok", (K1, "claude"): "missing"})
    rig.store.set_setting(SETTING_PROVIDER, "claude")
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] is False and r["kind"] == "signed_out" and r["provider"] is None and rig.made == []


def test_claude_gets_instructions_in_the_message(tmp_path):
    rig = _rig(tmp_path, {(K1, "claude"): "ok"})
    rig.store.set_setting(SETTING_PROVIDER, "claude")
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] and r["provider"] == "claude"
    sent = rig.made[0][2].sent[0]
    assert sent.startswith(instructions()) and "看護師: 血圧は132の78です。" in sent and "山田花子" not in sent
    e = rig.store.list_admin_events()[0]
    assert e["action"] == "karte_draft" and e["provider"] == "claude"


def test_settings_choose_ai_only_if_signed_in(tmp_path):
    rig = _rig(tmp_path, {(K1, "codex"): "ok", (K1, "claude"): "missing"})
    h = {"Authorization": "Bearer tok-admin", "Origin": "https://carelink.kaipoke-api.net"}
    with TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600, karte=rig.karte)) as c:
        v = c.get("/agent-relay/karte/settings", headers=h).json()
        assert v["seats"][0]["engines"] == {"codex": {"state": "ok"}, "claude": {"state": "missing"}}
        assert c.put("/agent-relay/karte/settings", json={"provider": "claude"}, headers=h).status_code == 409
        assert c.put("/agent-relay/karte/settings", json={"provider": "gemini"}, headers=h).status_code == 400
        v = c.put("/agent-relay/karte/settings", json={"provider": "codex"}, headers=h).json()
        assert v["provider"] == "codex"
        v = c.put("/agent-relay/karte/settings", json={"provider": None}, headers=h).json()
        assert v["provider"] is None
    details = [e["detail"] for e in rig.store.list_admin_events() if e["action"] == "karte_settings"]
    assert details[1].endswith("ai=auto->codex") and details[0].endswith("ai=codex->auto")


def test_claude_spawn_is_karte_purpose(tmp_path):
    rig = Rig(tmp_path, launch="runner", runner_socket="/run/agent/runner.sock")
    s = KarteDrafter(rig.svc)._runner_session("karte-" + "b" * 24, K1, "claude")
    assert isinstance(s, RunnerSession)
    assert s.spawn_msg["purpose"] == "karte" and s.spawn_msg["provider"] == "claude"
    assert s.spawn_msg["user_key"] == K1 and "resume_id" not in s.spawn_msg
    assert s.spawn_msg["relay_token"] not in rig.svc._tokens
    # codex の spawn はいままでの形（古い runner でも動く）
    c = KarteDrafter(rig.svc)._runner_session("karte-" + "c" * 24, K1, "codex")
    assert "purpose" not in c.spawn_msg


def test_claude_stream_through_drafter(tmp_path):
    """claude の stream-json（init → assistant の JSON → result）を本物の正規化に通して下書きにする。"""
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-opus-5-5", "apiKeySource": "none",
         "tools": [], "mcp_servers": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": json.dumps({"summary": SUMMARY},
                                                                                         ensure_ascii=False)}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "session_id": "s-1",
         "result": "ok"},
    ]
    rig = _rig(tmp_path, {(K1, "claude"): "ok"})
    rig.karte.session_factory = lambda sid, key, prov: FakeSession(
        lines, ClaudeNormalizer(allowed_key_sources={"none"}, check_model=False, clock=lambda: 0.0))
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"], r
    assert r["provider"] == "claude" and r["model"] == "claude-opus-5-5" and r["summary"]["バイタル"]["血圧"] == "132/78"


def test_claude_api_key_source_is_refused(tmp_path):
    lines = [{"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-opus-5-5",
              "apiKeySource": "ANTHROPIC_API_KEY", "tools": [], "mcp_servers": []}]
    rig = _rig(tmp_path, {(K1, "claude"): "ok"})
    rig.karte.session_factory = lambda sid, key, prov: FakeSession(
        lines, ClaudeNormalizer(allowed_key_sources={"none"}, check_model=False, clock=lambda: 0.0))
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] is False and r["kind"] == "signed_out"


# ---------------- 箱の runner ----------------
def test_runner_karte_claude_argv_and_purpose(tmp_path):
    mod = _load_runner_module()
    argv = mod.karte_claude_argv(tmp_path, "opus")
    i = argv.index("--tools")
    assert argv[i + 1] == ""  # 道具なし
    assert "--no-session-persistence" in argv and argv[argv.index("--max-turns") + 1] == "1"
    assert "--permission-prompt-tool" not in argv and "--append-system-prompt-file" not in argv
    assert "--resume" not in argv and argv[argv.index("--model") + 1] == "opus"
    msg = {"op": "spawn", "session_id": "karte-" + "a" * 24, "provider": "claude", "model": "opus",
           "relay_token": "t" * 32, "user_key": K1, "purpose": "karte"}
    assert mod.validate_spawn(msg)[-1] == K1 and mod.spawn_purpose(msg) == "karte"
    assert mod.spawn_purpose({**msg, "purpose": None}) == ""
    with pytest.raises(ValueError):
        mod.validate_spawn({**msg, "purpose": "shell"})
    with pytest.raises(ValueError):
        mod.validate_spawn({**msg, "resume_id": "0190aaaa-0000-7000-8000-000000000000"})
