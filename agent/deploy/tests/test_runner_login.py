"""箱の runner の「画面からのサインイン」の op（偽の codex / claude で・本物の CLI・網には触れない）。

    PYTHONPATH=agent python -m pytest agent/deploy/tests/test_runner_login.py -q -p no:asyncio
プロセスを起こす試験は POSIX だけ（os.killpg・unix ソケット）。Windows では形の試験だけが走る。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner" / "agent_runner.py"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX のプロセスグループ・unix ソケット")

CODEX_OUT = (
    "\nWelcome to Codex [v\x1b[90m0.162.1\x1b[0m]\n\nFollow these steps to sign in with ChatGPT using device code "
    "authorization:\n\n1. Open this link in your browser and sign in to your account\n"
    "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n"
    "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n   \x1b[94mT9U6-PTFBM\x1b[0m\n\n"
)
CLAUDE_URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a&response_type=code"
              "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=user%3Ainference"
              "&code_challenge=Zxg&code_challenge_method=S256&state=NkUk")
CLAUDE_OUT = f"Opening browser to sign in…\nIf the browser didn't open, visit: {CLAUDE_URL}\nPaste code here if prompted > "


@pytest.fixture
def rn(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(f"agent_runner_login_{tmp_path.name}", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(mod, "HOME_DIR", home)
    monkeypatch.setattr(mod, "CODEX_HOME", home / ".codex")
    monkeypatch.setattr(mod, "CLAUDE_HOME", home / ".claude")
    monkeypatch.setattr(mod, "LOGIN_URL_WAIT_SEC", 5.0)
    return mod


# ---------------------------------------------------------------- 形（どの OS でも）
def test_status_word_from_claude_auth_status(rn):
    w = rn.claude_status_word
    assert w(json.dumps({"loggedIn": False, "authMethod": "none"})) == "missing"
    assert w(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "po@example.com"})) == "ok"
    assert w(json.dumps({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"})) == "api_key"
    assert w(json.dumps({"loggedIn": True, "authMethod": "oauth_token"})) == "unknown"
    assert w("Not logged in") == "unknown" and w("[]") == "unknown"


def test_auth_env_is_an_allow_list(rn, monkeypatch):
    for k in rn.FORBIDDEN_ENV:
        monkeypatch.setenv(k, "leak")
    for p in ("codex", "claude"):
        env = rn.auth_env(p)
        assert not set(env) & set(rn.FORBIDDEN_ENV)
        assert env["HTTPS_PROXY"] == rn.EGRESS_PROXY and "RK_RELAY_TOKEN" not in env
    assert rn.auth_env("codex")["CODEX_HOME"].replace("\\", "/").endswith("/.codex")
    assert rn.auth_env("claude")["CLAUDE_CONFIG_DIR"].replace("\\", "/").endswith("/.claude")


class _P:  # Login.feed だけを見るための偽のプロセス
    returncode = None
    pid = 0


def test_feed_parses_codex_device_output(rn):
    lg = rn.Login("codex", _P())
    lg.feed(CODEX_OUT[:120])
    assert lg.state == "starting"
    lg.feed(CODEX_OUT[120:])
    pub = lg.public()
    assert pub["state"] == "waiting" and pub["url"] == "https://auth.openai.com/codex/device"
    assert pub["code"] == "T9U6-PTFBM" and pub["needs_input"] is False and 0 < pub["expires_in"] <= 900
    assert set(pub) == {"ev", "provider", "state", "url", "code", "needs_input", "expires_in", "error"}


def test_feed_parses_claude_url_and_invalid_code(rn):
    lg = rn.Login("claude", _P())
    lg.feed(CLAUDE_OUT)
    assert lg.state == "waiting" and lg.url == CLAUDE_URL and lg.public()["needs_input"]
    lg.input_mark, lg.state = len(lg.buf), "verifying"
    lg.feed("Invalid code. Please make sure the full code was copied.\n")
    assert lg.state == "waiting" and lg.error == "invalid_code"


def test_feed_ignores_urls_on_other_hosts(rn):
    lg = rn.Login("codex", _P())
    lg.feed("visit https://evil.example/codex/device\n   ABCD-EFGHI\n")
    assert lg.url is None and lg.state == "starting"


# ---------------------------------------------------------------- 偽の CLI で起こす（POSIX）
def _fake_cli(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body), encoding="utf-8")
    p.chmod(0o755)


@pytest.fixture
def fakes(rn, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    flag = tmp_path / "approve"
    _fake_cli(bindir, "codex", f"""
        import os, sys, time, pathlib
        a = sys.argv[1:]
        home = pathlib.Path(os.environ["CODEX_HOME"])
        if a == ["login", "--device-auth"]:
            assert os.environ.get("HTTPS_PROXY") and not os.environ.get("OPENAI_API_KEY")
            sys.stdout.write({CODEX_OUT!r}); sys.stdout.flush()
            for _ in range(200):
                if pathlib.Path({str(flag)!r}).exists():
                    (home / "auth.json").write_text("{{}}")
                    print("Successfully logged in"); sys.exit(0)
                time.sleep(0.05)
            sys.exit(1)
        if a == ["login", "status"]:
            print("Logged in using ChatGPT" if (home / "auth.json").exists() else "Not logged in"); sys.exit(0)
        if a == ["logout"]:
            (home / "auth.json").unlink(missing_ok=True); print("Successfully logged out"); sys.exit(0)
        sys.exit(2)
    """)
    _fake_cli(bindir, "claude", f"""
        import json, os, sys, pathlib
        a = sys.argv[1:]
        cred = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
        if a == ["auth", "login", "--claudeai"]:
            sys.stdout.write({CLAUDE_OUT!r}); sys.stdout.flush()
            for line in sys.stdin:
                code = line.strip()
                if "#" not in code:
                    print("Invalid code. Please make sure the full code was copied."); sys.stdout.flush(); continue
                if code.startswith("good"):
                    cred.write_text("{{}}"); print("Login successful."); sys.exit(0)
                print("Login failed: Request failed with status code 400"); sys.exit(1)
            sys.exit(1)
        if a == ["auth", "status"]:
            on = cred.exists()
            print(json.dumps({{"loggedIn": on, "authMethod": "claude.ai" if on else "none", "email": "x@y"}}))
            sys.exit(0 if on else 1)
        if a == ["auth", "logout"]:
            cred.unlink(missing_ok=True); print("Successfully logged out from your Anthropic account."); sys.exit(0)
        sys.exit(2)
    """)
    monkeypatch.setattr(rn, "CLI_PATH", f"{bindir}:/usr/bin:/bin")
    return flag


async def _until(pred, n=200):
    for _ in range(n):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("時間内に変わりませんでした")


@posix_only
def test_codex_device_login_end_to_end(rn, fakes):
    async def go():
        assert rn.auth_status("codex") == "missing"
        pub = await rn.login_start("codex")
        assert pub["state"] == "waiting" and pub["code"] == "T9U6-PTFBM"
        assert oct(rn.CODEX_HOME.stat().st_mode & 0o777) == "0o700"
        with pytest.raises(rn.LoginRefused, match="busy"):
            await rn.login_start("claude")  # 同時に 1 つだけ
        with pytest.raises(rn.LoginRefused):
            await rn.login_input("good#x" * 3)  # codex はコードを受けない
        fakes.write_text("y")
        await _until(lambda: rn.login_snapshot()["state"] == "done")
        assert rn.auth_status("codex") == "ok"
        assert rn.run_logout("codex") and rn.auth_status("codex") == "missing"
    asyncio.run(go())


@posix_only
def test_claude_paste_code_login(rn, fakes):
    async def go():
        pub = await rn.login_start("claude")
        assert pub["state"] == "waiting" and pub["url"] == CLAUDE_URL and pub["needs_input"]
        bad = await rn.login_input("no spaces allowed here")
        assert bad["state"] == "waiting" and bad["error"] == "invalid_code"  # 形が違うものは CLI へ送らない
        await rn.login_input("abcdefghij")  # 形は通るが # が無い → CLI が Invalid code → また待つ
        await _until(lambda: rn.login_snapshot()["error"] == "invalid_code" and rn.login_snapshot()["state"] == "waiting")
        r = await rn.login_input("good-code_123#state")
        assert r["state"] == "verifying"
        await _until(lambda: rn.login_snapshot()["state"] == "done")
        assert rn.auth_status("claude") == "ok"
        assert rn.run_logout("claude") and rn.auth_status("claude") == "missing"
    asyncio.run(go())


@posix_only
def test_claude_wrong_code_fails(rn, fakes):
    async def go():
        await rn.login_start("claude")
        await rn.login_input("wrong-code#state")
        await _until(lambda: rn.login_snapshot()["state"] == "failed")
        assert rn.auth_status("claude") == "missing"
    asyncio.run(go())


@posix_only
def test_cancel_and_timeout_stop_the_cli(rn, fakes, monkeypatch):
    async def go():
        await rn.login_start("codex")
        proc = rn._login.proc
        assert (await rn.login_cancel())["state"] == "cancelled"
        await _until(lambda: proc.returncode is not None)
        await asyncio.sleep(0.2)
        assert rn.login_snapshot()["state"] == "cancelled"  # 止めた後も「やめた」のまま
        monkeypatch.setattr(rn, "LOGIN_TTL_SEC", 1.0)
        await rn.login_start("codex")
        proc = rn._login.proc
        await _until(lambda: rn.login_snapshot()["state"] == "timeout")
        await _until(lambda: proc.returncode is not None)
    asyncio.run(go())


@posix_only
def test_ops_over_the_socket(rn, fakes, tmp_path, monkeypatch):
    """中継と同じ形（op ごとに 1 本の接続）で、決まった語・URL・コードだけが返ることを確かめる。"""
    monkeypatch.setattr(rn, "ALLOWED_PEER_UID", os.getuid())
    sock = str(tmp_path / "r.sock")

    async def ask(obj):
        r, w = await asyncio.open_unix_connection(sock)
        w.write((json.dumps(obj) + "\n").encode())
        await w.drain()
        line = await asyncio.wait_for(r.readline(), 10)
        w.close()
        return json.loads(line)

    async def go():
        server = await asyncio.start_unix_server(rn.handle, path=sock)
        async with server:
            assert await ask({"op": "auth_status", "provider": "claude"}) == {
                "ev": "auth_status", "provider": "claude", "state": "missing"}
            assert (await ask({"op": "auth_status"}))["provider"] == "codex"  # 既定は codex（前と同じ）
            assert (await ask({"op": "auth_status", "provider": "x"}))["ev"] == "error"
            st = await ask({"op": "login_start", "provider": "claude"})
            assert st["state"] == "waiting" and "Paste" not in json.dumps(st)  # CLI の文は返さない
            assert (await ask({"op": "logout", "provider": "claude"})) == {"ev": "error", "message": "login busy"}
            assert (await ask({"op": "login_input", "data": "good-x_1#s"}))["state"] == "verifying"
            for _ in range(100):
                if (await ask({"op": "login_poll"}))["state"] == "done":
                    break
                await asyncio.sleep(0.05)
            out = await ask({"op": "logout", "provider": "claude"})
            assert out == {"ev": "logout", "provider": "claude", "ok": True, "state": "missing"}
            assert (await ask({"op": "stdin", "data": "x"}))["message"] == "spawn first"  # 会話の op は今までどおり
    asyncio.run(go())
