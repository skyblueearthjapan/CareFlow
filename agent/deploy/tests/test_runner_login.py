"""箱の runner の「画面からのサインイン」の op（偽の codex / claude で・本物の CLI・網には触れない）。

人ごとの席（PO 2026-10-13）: ログイン情報は HOME_DIR/u/<user_key>/.codex・.claude。鍵の形が違えば断る・他人の席は見えない。

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
    monkeypatch.setattr(mod, "LOGIN_URL_WAIT_SEC", 5.0)
    return mod


KA = "a" * 32  # 1 人目の席の鍵（中継の seat_key と同じ形）
KB = "b" * 32  # 2 人目


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
        env = rn.auth_env(p, KA)
        assert not set(env) & set(rn.FORBIDDEN_ENV)
        assert env["HTTPS_PROXY"] == rn.EGRESS_PROXY and "RK_RELAY_TOKEN" not in env
    assert rn.auth_env("codex", KA)["CODEX_HOME"].replace("\\", "/").endswith(f"/home/u/{KA}/.codex")
    assert rn.auth_env("claude", KB)["CLAUDE_CONFIG_DIR"].replace("\\", "/").endswith(f"/home/u/{KB}/.claude")
    assert rn.auth_env("codex", KA)["HOME"].replace("\\", "/").endswith(f"/home/u/{KA}")


def test_user_key_must_be_an_opaque_hex_key(rn):
    for bad in ("", "../x", "A" * 32, "a" * 31, "a" * 33, "/home/agent/.codex", None, 7, "g" * 32):
        with pytest.raises(ValueError):
            rn.check_user_key(bad)
        with pytest.raises(ValueError):
            rn.seat_dir(bad)  # type: ignore[arg-type]
    assert rn.seat_dir(KA) == rn.HOME_DIR / "u" / KA


def test_seats_are_separate_and_status_does_not_create_them(rn):
    assert rn.auth_status("codex", KA) == "missing" and rn.auth_status("claude", KA) == "missing"
    assert not (rn.HOME_DIR / "u").exists()  # 様子を見ただけでは席を作らない
    assert rn.run_logout("codex", KA) is True  # サインインしていない人のサインアウトは何もしない
    seat = rn.ensure_seat(KA)
    assert seat == rn.HOME_DIR / "u" / KA and seat.is_dir()
    if sys.platform != "win32":
        assert oct(seat.stat().st_mode & 0o777) == "0o700" and oct(seat.parent.stat().st_mode & 0o777) == "0o700"
    assert not (rn.HOME_DIR / "u" / KB).exists()


def test_adopt_legacy_moves_the_shared_login_into_one_seat_once(rn):
    legacy = rn.HOME_DIR / ".codex"
    legacy.mkdir()
    (legacy / "auth.json").write_text("{}")
    (rn.HOME_DIR / ".claude").mkdir()
    (rn.HOME_DIR / ".claude" / ".credentials.json").write_text("{}")
    # 持ち主の席に Claude のログインがもうある（先にサインインした）→ Claude は触らない
    rn.provider_home("claude", KA).mkdir(parents=True)
    (rn.provider_home("claude", KA) / ".credentials.json").write_text("mine")
    assert rn.adopt_legacy(KA) == ["codex"]
    assert (rn.provider_home("codex", KA) / "auth.json").read_text() == "{}" and not legacy.exists()
    assert (rn.provider_home("claude", KA) / ".credentials.json").read_text() == "mine"
    assert (rn.HOME_DIR / ".claude").exists()  # 移さなかったものは残す（PO が手で片づける）
    assert rn.adopt_legacy(KA) == [] and rn.adopt_legacy(KB) == []  # 2 回目・ほかの人には何も移らない
    assert not rn.provider_home("codex", KB).exists()


def test_adopt_legacy_into_an_empty_seat_dir(rn):
    (rn.HOME_DIR / ".codex").mkdir()
    (rn.HOME_DIR / ".codex" / "auth.json").write_text("{}")
    rn.provider_home("codex", KA).mkdir(parents=True)  # 空の置き場だけがあった
    assert rn.adopt_legacy(KA) == ["codex"]
    assert (rn.provider_home("codex", KA) / "auth.json").exists()


def test_prepare_codex_home_is_per_seat(rn):
    home = rn.provider_home("codex", KA)
    rn.ensure_seat(KA)
    home.mkdir()
    (home / "auth.json").write_text("{}")
    (home / "config.toml").write_text("[mcp_servers.x]\n")
    rn.prepare_codex_home(home)
    assert (home / "auth.json").read_text() == "{}"  # ログイン情報には触らない
    assert "mcp_servers" not in (home / "config.toml").read_text(encoding="utf-8")
    assert [f.name for f in (home / "rules").iterdir()] == [rn.CODEX_RULES.name]
    assert not rn.provider_home("codex", KB).exists()


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
        assert pathlib.Path(os.environ["HOME"]) == home.parent and home.parent.parent.name == "u"
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
        assert pathlib.Path(os.environ["HOME"]) == cred.parent.parent and cred.parent.parent.parent.name == "u"
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
        assert rn.auth_status("codex", KA) == "missing"
        pub = await rn.login_start("codex", KA)
        assert pub["state"] == "waiting" and pub["code"] == "T9U6-PTFBM"
        for d in (rn.HOME_DIR / "u", rn.seat_dir(KA), rn.provider_home("codex", KA)):
            assert oct(d.stat().st_mode & 0o777) == "0o700"  # 席は持ち主だけ（実行係 10004 は入れない）
        with pytest.raises(rn.LoginRefused, match="busy"):
            await rn.login_start("claude", KB)  # 箱で同時に 1 つだけ（ほかの人も）
        with pytest.raises(rn.LoginRefused):
            await rn.login_input("good#x" * 3, KA)  # codex はコードを受けない
        assert rn.login_snapshot(KB) == {"ev": "login", "state": "idle"}  # 他人のサインインは見えない
        assert (await rn.login_cancel(KB))["state"] == "idle" and rn.login_snapshot(KA)["state"] == "waiting"
        fakes.write_text("y")
        await _until(lambda: rn.login_snapshot(KA)["state"] == "done")
        assert rn.auth_status("codex", KA) == "ok" and rn.auth_status("codex", KB) == "missing"
        assert (rn.provider_home("codex", KA) / "auth.json").exists()
        assert not (rn.HOME_DIR / ".codex").exists()  # 共有の置き場には書かない
        assert rn.run_logout("codex", KA) and rn.auth_status("codex", KA) == "missing"
    asyncio.run(go())


@posix_only
def test_two_people_sign_in_to_their_own_seats(rn, fakes):
    async def go():
        await rn.login_start("codex", KA)
        fakes.write_text("y")
        await _until(lambda: rn.login_snapshot(KA)["state"] == "done")
        fakes.unlink()
        await rn.login_start("codex", KB)
        fakes.write_text("y")
        await _until(lambda: rn.login_snapshot(KB)["state"] == "done")
        assert rn.auth_status("codex", KA) == rn.auth_status("codex", KB) == "ok"
        assert rn.run_logout("codex", KB) and rn.auth_status("codex", KB) == "missing"
        assert rn.auth_status("codex", KA) == "ok"  # 1 人のサインアウトはほかの人の席に触らない
    asyncio.run(go())


@posix_only
def test_claude_paste_code_login(rn, fakes):
    async def go():
        pub = await rn.login_start("claude", KA)
        assert pub["state"] == "waiting" and pub["url"] == CLAUDE_URL and pub["needs_input"]
        bad = await rn.login_input("no spaces allowed here", KA)
        assert bad["state"] == "waiting" and bad["error"] == "invalid_code"  # 形が違うものは CLI へ送らない
        with pytest.raises(rn.LoginRefused):
            await rn.login_input("good-code_123#state", KB)  # 他人のサインインにコードは渡せない
        await rn.login_input("abcdefghij", KA)  # 形は通るが # が無い → CLI が Invalid code → また待つ
        await _until(lambda: rn.login_snapshot(KA)["error"] == "invalid_code" and rn.login_snapshot(KA)["state"] == "waiting")
        r = await rn.login_input("good-code_123#state", KA)
        assert r["state"] == "verifying"
        await _until(lambda: rn.login_snapshot(KA)["state"] == "done")
        assert rn.auth_status("claude", KA) == "ok" and rn.auth_status("claude", KB) == "missing"
        assert rn.run_logout("claude", KA) and rn.auth_status("claude", KA) == "missing"
    asyncio.run(go())


@posix_only
def test_claude_wrong_code_fails(rn, fakes):
    async def go():
        await rn.login_start("claude", KA)
        await rn.login_input("wrong-code#state", KA)
        await _until(lambda: rn.login_snapshot(KA)["state"] == "failed")
        assert rn.auth_status("claude", KA) == "missing"
    asyncio.run(go())


@posix_only
def test_cancel_and_timeout_stop_the_cli(rn, fakes, monkeypatch):
    async def go():
        await rn.login_start("codex", KA)
        proc = rn._login.proc
        assert (await rn.login_cancel(KA))["state"] == "cancelled"
        await _until(lambda: proc.returncode is not None)
        await asyncio.sleep(0.2)
        assert rn.login_snapshot()["state"] == "cancelled"  # 止めた後も「やめた」のまま
        monkeypatch.setattr(rn, "LOGIN_TTL_SEC", 1.0)
        await rn.login_start("codex", KA)
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
            for op in ("auth_status", "login_start", "login_poll", "logout", "adopt_legacy"):
                for bad in ({}, {"user_key": "../../x"}, {"user_key": "/home/agent/.codex"}):
                    r = await ask({"op": op, "provider": "codex", **bad})
                    assert r == {"ev": "error", "message": "bad user_key"}  # 席の鍵が無い・形が違う → 何もしない
            assert not (rn.HOME_DIR / "u").exists()
            assert await ask({"op": "auth_status", "provider": "claude", "user_key": KA}) == {
                "ev": "auth_status", "provider": "claude", "state": "missing"}
            assert (await ask({"op": "auth_status", "user_key": KA}))["provider"] == "codex"  # 既定は codex（前と同じ）
            assert (await ask({"op": "auth_status", "provider": "x", "user_key": KA}))["ev"] == "error"
            st = await ask({"op": "login_start", "provider": "claude", "user_key": KA})
            assert st["state"] == "waiting" and "Paste" not in json.dumps(st)  # CLI の文は返さない
            assert (await ask({"op": "login_poll", "user_key": KB})) == {"ev": "login", "state": "idle"}
            assert (await ask({"op": "logout", "provider": "claude", "user_key": KA})) == {
                "ev": "error", "message": "login busy"}
            assert (await ask({"op": "login_input", "data": "good-x_1#s", "user_key": KB}))["ev"] == "error"
            assert (await ask({"op": "login_input", "data": "good-x_1#s", "user_key": KA}))["state"] == "verifying"
            for _ in range(100):
                if (await ask({"op": "login_poll", "user_key": KA}))["state"] == "done":
                    break
                await asyncio.sleep(0.05)
            out = await ask({"op": "logout", "provider": "claude", "user_key": KA})
            assert out == {"ev": "logout", "provider": "claude", "ok": True, "state": "missing"}
            assert await ask({"op": "adopt_legacy", "user_key": KA}) == {"ev": "adopted", "moved": []}
            assert (await ask({"op": "stdin", "data": "x"}))["message"] == "spawn first"  # 会話の op は今までどおり
    asyncio.run(go())
