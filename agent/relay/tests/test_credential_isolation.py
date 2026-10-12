"""認証情報の隔離（段 3 のレビュー指摘）の部品の試験: 実行係 execd・窓口 bx・runner の環境・settings の deny・道具の入力の秘密判定。

uid を分ける本物の試験は箱の中（agent/deploy/tests/isolation_e2e.sh・docker が要る）。ここは uid に依らない部分:
環境の絞り込み・要求の検査・ソケットの往復（同じ uid で）・相手が切れたら子を止める・実行係が無いとき bx が走らせない。
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from relaykit import AGENT
from relay.runner import control_reply, isolation_settings

RUNNER = AGENT / "deploy" / "runner"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, RUNNER / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


execd = _load("execd_under_test", "execd.py")
posix_only = pytest.mark.skipif(not hasattr(socket, "send_fds") or sys.platform == "win32",
                                reason="unix ソケット＋fd 渡しが要る")


def test_execd_env_keeps_only_allowed_names():
    env = execd.build_env({"RK_RELAY_TOKEN": "t", "RK_MODE": "relay", "CODEX_HOME": "/home/agent/.codex",
                           "HOME": "/home/agent", "CLAUDE_CONFIG_DIR": "/home/agent/.claude", "LD_PRELOAD": "/x.so",
                           "ANTHROPIC_API_KEY": "k", "PATH": "/srv/agent/bin:/evil:/usr/bin", "HTTPS_PROXY": "http://p"})
    assert env["RK_RELAY_TOKEN"] == "t" and env["HTTPS_PROXY"] == "http://p"
    assert env["HOME"] == execd.HOME and env["PATH"].split(":")[0] == "/srv/agent/bin" and "/evil" not in env["PATH"]
    for k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "LD_PRELOAD", "ANTHROPIC_API_KEY"):
        assert k not in env


def test_execd_rejects_bad_requests():
    for bad in (b"{}", b'{"argv": []}', b'{"argv": [1]}', b'{"argv": ["a\\u0000b"]}', b"not json"):
        with pytest.raises(ValueError):
            execd.parse_request(bad)
    argv, cwd, env = execd.parse_request(json.dumps({"argv": ["x"], "cwd": "relative", "env": 3}).encode())
    assert cwd == "/srv/agent" and env == {}


@pytest.fixture
def daemon(tmp_path):
    if sys.platform == "win32":
        pytest.skip("unix only")
    sock = tmp_path / "exec.sock"
    execd.SOCKET_PATH = str(sock)
    execd.ALLOWED_PEER_UID = os.getuid()
    execd.SWEEP_ENABLED = False  # 同じ uid の無関係なプロセスを止めない
    t = threading.Thread(target=execd.main, daemon=True)
    t.start()
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.02)
    yield str(sock)


def _bx(sock, *args, timeout=20, env=None):
    e = {**os.environ, "RUNNER_EXEC_SOCKET": sock, **(env or {})}
    return subprocess.run([sys.executable, str(RUNNER / "bx"), *args], env=e, capture_output=True, text=True,
                          timeout=timeout)


@posix_only
def test_bx_runs_through_execd_and_returns_exit_code(daemon):
    r = _bx(daemon, "echo out; echo err >&2; exit 7", env={"RK_WORK_DIR": "/w", "CODEX_HOME": "/secret"})
    assert (r.returncode, r.stdout.strip(), r.stderr.strip()) == (7, "out", "err")
    r = _bx(daemon, 'echo "${RK_WORK_DIR:-none} ${CODEX_HOME:-none}"', env={"RK_WORK_DIR": "/w", "CODEX_HOME": "/secret"})
    assert r.stdout.strip() == "/w none"  # RK_* は渡り、CODEX_HOME は渡らない


@posix_only
def test_bx_does_not_run_when_execd_unreachable(tmp_path):
    marker = tmp_path / "ran"
    r = _bx(str(tmp_path / "nope.sock"), f"touch {marker}")
    assert r.returncode == 126 and not marker.exists()


@posix_only
def test_execd_kills_the_command_when_client_goes_away(daemon, tmp_path):
    pidfile = tmp_path / "pid"
    p = subprocess.Popen([sys.executable, str(RUNNER / "bx"), f"echo $$ > {pidfile}; exec sleep 60"],
                         env={**os.environ, "RUNNER_EXEC_SOCKET": daemon})
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    p.kill()
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("コマンドが止まっていない")


@posix_only
def test_execd_refuses_other_uids(daemon, tmp_path):
    execd.ALLOWED_PEER_UID = os.getuid() + 1
    try:
        r = _bx(daemon, "echo should-not-run")
        assert r.returncode == 126 and "should-not-run" not in r.stdout
    finally:
        execd.ALLOWED_PEER_UID = os.getuid()


def test_runner_points_both_clis_at_the_exec_split(monkeypatch):
    runner = _load("agent_runner_exec_under_test", "agent_runner.py")
    monkeypatch.setattr(runner, "EXEC_SOCKET", "/run/x/exec.sock")
    sd = Path("/data/sessions/x")
    claude = runner.build_env(sd, "claude", "t" * 40, "b" * 32)
    assert claude["CLAUDE_CODE_SHELL_PREFIX"].replace("\\", "/").endswith("/bx") and claude["RUNNER_EXEC_SOCKET"] == "/run/x/exec.sock"
    codex = runner.build_env(sd, "codex", "t" * 40, "b" * 32)
    assert codex["CODEX_EXEC_SERVER_URL"].startswith("ws://127.0.0.1:")
    args = runner.codex_exec_env_args(codex)
    joined = " ".join(args)
    assert "RK_RELAY_TOKEN" in joined and "allow_login_shell=false" in joined
    assert "CODEX_HOME" not in joined and "HOME =" not in joined  # 実行係には CLI の HOME / CODEX_HOME を渡さない
    # ログイン情報は持ち主の席（/home/agent/u/<鍵>/）だけ。実行係に渡る値にも席のパスは入らない
    assert codex["CODEX_HOME"].replace("\\", "/").endswith("/home/agent/u/" + "b" * 32 + "/.codex")
    assert claude["CLAUDE_CONFIG_DIR"].replace("\\", "/").endswith("/home/agent/u/" + "b" * 32 + "/.claude")
    assert codex["HOME"] == claude["HOME"] and codex["HOME"].replace("\\", "/").endswith("/u/" + "b" * 32)
    assert "/u/" not in joined
    monkeypatch.setattr(runner, "EXEC_SOCKET", "")
    assert "CLAUDE_CODE_SHELL_PREFIX" not in runner.build_env(sd, "claude", "t" * 40, "b" * 32)


def test_isolation_settings_deny_cli_tools_on_credentials():
    deny = isolation_settings("/home/agent", "/srv/agent")["permissions"]["deny"]
    assert "Read(//home/agent/**)" in deny and "Edit(//home/agent/**)" in deny
    tpl = json.loads((RUNNER / "isolation.json").read_text(encoding="utf-8"))
    assert "Read(//home/agent/**)" in tpl["permissions"]["deny"]


def test_cli_tools_cannot_touch_credentials_even_if_allowed():
    def reply(tool, **inp):
        return control_reply({"request_id": "r", "request": {"subtype": "can_use_tool", "tool_name": tool, "input": inp}})

    for tool, inp in (("Read", {"file_path": "/home/agent/.claude/.credentials.json"}),
                      ("Grep", {"pattern": "token", "path": "/home/agent/.codex/auth.json"}),
                      ("Glob", {"pattern": "/home/agent/**"}),
                      ("Edit", {"file_path": "/home/agent/.claude/CLAUDE.md"})):
        assert reply(tool, **inp)["response"]["response"]["behavior"] == "deny", (tool, inp)
    ok = reply("Read", file_path="/data/sessions/s1/work/report.md")
    assert ok["response"]["response"]["behavior"] == "allow"


def test_execd_removes_links_pointing_outside_the_work_dir(tmp_path):
    work = tmp_path / "s1" / "work"
    (work / "scratch").mkdir(parents=True)
    (work / "report.md").write_text("x")
    outside = tmp_path / "secret.json"
    outside.write_text("s")
    if sys.platform == "win32":
        pytest.skip("symlink")
    (work / "leak").symlink_to(outside)
    (work / "scratch" / "dir").symlink_to(tmp_path)
    (work / "fine").symlink_to(work / "report.md")
    (work / "dangling").symlink_to(tmp_path / "nope")
    assert execd.sweep_links(work) == 3
    assert not (work / "leak").is_symlink() and (work / "fine").is_symlink() and outside.exists()
    execd.SESSIONS_DIR = tmp_path
    assert execd.work_root_of(str(work / "scratch")) == tmp_path / "s1" / "work"
    assert execd.work_root_of("/srv/agent") is None
