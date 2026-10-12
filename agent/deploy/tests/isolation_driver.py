"""認証情報の隔離の試験（箱の中・uid 10002 = CLI の側で動かす。isolation_e2e.sh が呼ぶ）。

本物の claude / codex を、runner と同じ環境（agent_runner.build_env）・同じ起こし方で動かし、偽のモデル（fake_model.py）に
「ログイン情報を読むコマンド」を出させる。確かめること:
  - モデルのコマンドは uid 10004（agentx）で走り、ログイン情報（ダミー）は Permission denied で読めない（claude / codex）
  - CLI 自身（uid 10002）はダミーの認証ファイルを読める
  - claude の Read の道具でログイン情報を読もうとしても settings の deny で断られる
  - 実行係が無いとき bx は走らせない（126）
  - 人ごとの席（/home/agent/u/<鍵>/.claude・.codex・2026-10-13〜）: CLI は持ち主の席のログインを使い、モデルのコマンドからは
    持ち主の席も別の人の席も、前の共有の置き場も読めない。席は 0700
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRET = "DUMMY-SECRET-VALUE-0123456789"
PORT = 18999
LOG = Path("/tmp/fake_model.log")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  <- {detail[:600]}"), flush=True)


spec = importlib.util.spec_from_file_location("agent_runner", "/opt/runner/agent_runner.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)


def start_model(scenario: dict) -> subprocess.Popen:
    LOG.unlink(missing_ok=True)
    env = {"PATH": os.environ["PATH"], "FAKE_SCENARIO": json.dumps(scenario), "FAKE_LOG": str(LOG)}
    p = subprocess.Popen([sys.executable, str(HERE / "fake_model.py"), str(PORT)], env=env)
    for _ in range(50):
        try:
            import socket
            socket.create_connection(("127.0.0.1", PORT), 0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("fake model did not start")


def tool_results() -> list[str]:
    if not LOG.exists():
        return []
    return [json.loads(x)["result"] for x in LOG.read_text(encoding="utf-8").splitlines() if '"tool_result"' in x]


KEY = "c" * 32   # 会話の持ち主の席（中継の seat_key と同じ形）
KEY2 = "d" * 32  # 別の人の席


def cli_env(provider: str, sid: str) -> dict:
    env = ar.build_env(ar.prepare_session_dir(sid), provider, "t" * 40, KEY)
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
    return env


# ---------------------------------------------------------------- 事前: ダミーのログイン情報
home = Path(os.environ.get("HOME", "/home/agent"))
ar.ensure_seat(KEY)
ar.ensure_seat(KEY2)
SEAT, SEAT2 = ar.seat_dir(KEY), ar.seat_dir(KEY2)
creds = {}
for base in (home, SEAT, SEAT2):  # 前の共有の置き場・持ち主の席・別の人の席
    creds[base / ".claude" / ".credentials.json"] = json.dumps({"claudeAiOauth": {"accessToken": SECRET}})
    creds[base / ".codex" / "auth.json"] = json.dumps({"tokens": {"access_token": SECRET}})
for p, body in creds.items():
    p.parent.mkdir(mode=0o700, exist_ok=True)
    p.write_text(body)
    p.chmod(0o600)
check("席は 0700（/home/agent/u と各席）", all(oct(d.stat().st_mode & 0o777) == "0o700" for d in (home / "u", SEAT, SEAT2)),
      " ".join(oct(d.stat().st_mode) for d in (home / "u", SEAT, SEAT2)))
check("CLI は持ち主の席のログインを使う", cli_env("claude", "isotest-env")["CLAUDE_CONFIG_DIR"] == str(SEAT / ".claude")
      and cli_env("codex", "isotest-env")["CODEX_HOME"] == str(SEAT / ".codex"), "")

check("setup: runner が実行係の窓口を設定している", bool(ar.EXEC_SOCKET), "RUNNER_EXEC_SOCKET が空")
check("CLI 自身（uid %d）はダミーの認証ファイルを読める" % os.getuid(),
      all(SECRET in p.read_text() for p in creds), "")

# ---------------------------------------------------------------- claude: Bash
cmd = ("id -u; cat /home/agent/.claude/.credentials.json; echo rc=$?; cat /home/agent/.codex/auth.json; echo rc=$?; "
       f"cat {SEAT}/.claude/.credentials.json; echo rc=$?; cat {SEAT2}/.codex/auth.json; echo rc=$?; "
       "ls /home/agent; ls /home/agent/u; echo cwd=$(pwd)")
srv = start_model({"tool": "Bash", "input": {"command": cmd, "description": "t"}})
sid = "isotest-claude-bash"
sd = ar.prepare_session_dir(sid)
iso = json.loads(ar.settings_path(sid).read_text())
iso["env"] = {}  # 試験用: 偽のモデルに向けるため、本番の env の空打ち（ANTHROPIC_BASE_URL 等）と出口プロキシは外す
test_iso = Path("/run/agent") / f"{sid}.test.json"  # 本番と同じく実行係の手が届かない所
test_iso.write_text(json.dumps(iso))
env = cli_env("claude", sid)
env.update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT}", "ANTHROPIC_API_KEY": "dummy-not-a-real-key"})
argv = ["claude", "-p", "go", "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
        "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(ar.EMPTY_MCP),
        "--settings", str(test_iso), "--max-turns", "4", "--model", "claude-opus-4-5"]
r = subprocess.run(argv, env=env, cwd=str(ar.AGENT_DIR), capture_output=True, text=True, timeout=120)
res = " ".join(tool_results())
print("  claude の結果:", res[:1800].replace("\n", " | "))
check("claude: Bash の結果が返った", bool(res), (r.stdout[-800:] + r.stderr[-800:]))
check("claude: モデルのコマンドは uid 10004 で走る", "10004" in res, res)
check("claude: ログイン情報は Permission denied（共有・持ち主の席・別の人の席）", res.count("Permission denied") >= 4 and "rc=1" in res, res)
check("claude: ダミーの秘密が結果に出ない", SECRET not in res and SECRET not in r.stdout, res)
check("claude: /home/agent の中身も見えない", "ls: cannot open directory" in res or "Permission denied" in res, res)
srv.terminate()

# ---------------------------------------------------------------- claude: Read の道具
srv = start_model({"tool": "Read", "input": {"file_path": f"{SEAT}/.claude/.credentials.json"}})
sid = "isotest-claude-read"
sd = ar.prepare_session_dir(sid)
test_iso = Path("/run/agent") / f"{sid}.test.json"
test_iso.write_text(json.dumps(iso))
env = cli_env("claude", sid)
env.update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT}", "ANTHROPIC_API_KEY": "dummy-not-a-real-key"})
argv[argv.index("--settings") + 1] = str(test_iso)
r = subprocess.run(argv, env=env, cwd=str(ar.AGENT_DIR), capture_output=True, text=True, timeout=120)
res = " ".join(tool_results())
check("claude: Read の道具でもログイン情報は読めない（settings の deny）", bool(res) and SECRET not in res
      and SECRET not in r.stdout, res + r.stdout[-500:])
srv.terminate()

# ---------------------------------------------------------------- codex: shell
def work_of(sid: str) -> Path:
    return ar.SESSIONS_DIR / sid / "work"


for SCEN in ("shell", "patch"):
    cmd = ("id -u; cat /home/agent/.codex/auth.json; echo rc=$?; cat \"$CODEX_HOME/auth.json\"; echo rc=$?; "
           f"cat {SEAT}/.codex/auth.json; echo rc=$?; cat {SEAT2}/.claude/.credentials.json; echo rc=$?; "
           "(setsid sleep 311 >/dev/null 2>&1 < /dev/null &); echo RKTOKEN=${RK_RELAY_TOKEN:+set}; env | grep -E '^(PATH|RK_MODE|RK_WORK_DIR|PYTHONPATH)='; which rk; pwd")
    PATCH_FILE = work_of("isotest-codex") / "patched.txt"
    PATCH = "*** Begin Patch\n*** Add File: " + str(PATCH_FILE) + "\n+patched\n*** End Patch\n"
    # patch: モデルの道具に apply_patch が無い（偽のモデル名）ので、codex が横取りする「apply_patch <<EOF」の形で頼む
    srv = start_model({"shell": cmd} if SCEN == "shell" else {"patch": PATCH})
    sid = "isotest-codex"
    ar.prepare_codex_home(ar.provider_home("codex", KEY))
    env = cli_env("codex", sid)
    env.update({"FAKE_KEY": "dummy-not-a-real-key"})
    sys.path.insert(0, "/srv/agent")
    from relay import codex_config as cx  # noqa: E402

    cfg: list[str] = []
    for k, v in cx.LOCKDOWN:  # 本番の締め付けそのまま（偽のモデルに向けるため、ログイン方式と provider だけ除く）
        if k not in ("forced_login_method", "model_provider"):
            cfg += ["-c", f"{k}={v}"]
    for name in cx.FEATURES_OFF:
        cfg += ["-c", f"features.{name}=false"]
    cfg += ar.codex_exec_env_args(env)  # 本番と同じ: RK_* は shell_environment_policy.set で実行係へ渡す
    argv = ["codex"] + cfg + [
        "-c", f'model_providers.fake={{name="fake",base_url="http://127.0.0.1:{PORT}/v1",env_key="FAKE_KEY",wire_api="responses"}}',
        "-c", 'model_provider="fake"', "-c", 'model="gpt-5.1-codex"', "app-server"]
    p = subprocess.Popen(argv, env=env, cwd=str(ar.AGENT_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    lines: list[str] = []


    def send(o: dict) -> None:
        p.stdin.write(json.dumps(o) + "\n")
        p.stdin.flush()


    import selectors  # noqa: E402

    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)


    def pump(until, timeout=90.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            for _k, _ in sel.select(0.5):
                ln = p.stdout.readline()
                if not ln:
                    return False
                lines.append(ln)
                try:
                    m = json.loads(ln)
                except ValueError:
                    continue
                if m.get("method") and "id" in m:  # サーバーからの問い合わせには許可で返す
                    send({"id": m["id"], "result": {"decision": "accept"}})
                if until(m):
                    return True
        return False


    time.sleep(1.5)
    if p.poll() is not None:
        check("codex: app-server が起動した", False, p.stderr.read()[-1500:])
        sys.exit(1)
    send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "iso", "version": "0"},
                                                         "capabilities": {"experimentalApi": True}}})
    pump(lambda m: m.get("id") == 1, 20)
    send({"method": "initialized"})
    send({"id": 2, "method": "thread/start", "params": {"cwd": str(ar.AGENT_DIR), "model": "gpt-5.1-codex",
                                                          "approvalPolicy": "never", "sandbox": "danger-full-access"}})
    tid = None


    def got_thread(m):
        global tid
        if m.get("id") == 2 and "result" in m:
            tid = m["result"]["thread"]["id"]
            return True
        return m.get("id") == 2


    pump(got_thread, 30)
    if tid:
        send({"id": 3, "method": "turn/start", "params": {"threadId": tid, "input": [{"type": "text", "text": "go"}]}})
        pump(lambda m: m.get("method") == "turn/completed", 90)
    fs_text = ""
    if SCEN == "patch":
        # 実測の方針: apply_patch の道具はモデルの名前しだいで出る（偽のモデルでは出ない）ので、app-server 自身のファイル API
        # （fs/readFile）が CLI の uid のローカルファイルを読まないこと＝ファイル操作は実行係の環境だけに出ることを確かめる
        send({"id": 9, "method": "fs/readFile", "params": {"path": f"{SEAT}/.codex/auth.json"}})
        got = {}
        pump(lambda m: m.get("id") == 9 and not got.update(m), 15)
        fs_text = json.dumps(got, ensure_ascii=False)
    time.sleep(1)
    res = " ".join(tool_results())
    p.terminate()
    if SCEN == "shell":
        print("  codex の結果:", res[:1800].replace("\n", " | "))
        check("codex: コマンドの結果が返った", bool(res), "".join(lines)[-1500:] + p.stderr.read()[-800:])
        check("codex: モデルのコマンドは uid 10004 で走る", "10004" in res, res)
        check("codex: ログイン情報は Permission denied（共有・持ち主の席・別の人の席）", res.count("Permission denied") >= 3 and "rc=1" in res, res)
        check("codex: ダミーの秘密が結果に出ない", SECRET not in res, res)
        for _ in range(20):
            if not subprocess.run(["pgrep", "-f", "sleep 311"], capture_output=True).stdout:
                break
            time.sleep(0.5)
        check("codex: 逃げた子は定期の掃除で片づく", not subprocess.run(["pgrep", "-f", "sleep 311"], capture_output=True).stdout, "sleep 311 が残っている")
    else:
        print("  codex の fs/readFile:", fs_text[:200])
        check("codex: app-server 自身はローカルのファイルを読まない（local filesystem is not configured）",
              "not configured" in fs_text and SECRET not in fs_text, fs_text + "".join(lines)[-500:])
    srv.terminate()

# ---------------------------------------------------------------- 実行係の手が届く範囲（設定の差し替え・逃げる子・リンク）
import hashlib  # noqa: E402

sid = "isotest-claude-bash"
sdir = ar.SESSIONS_DIR / sid
work = sdir / "work"
settings = ar.settings_path(sid)
before = hashlib.sha256(settings.read_bytes()).hexdigest()
bx = ["/opt/runner/bx"]
benv = {**os.environ, "RK_WORK_DIR": str(work)}


def bx_run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(bx + [cmd], env=benv, cwd=str(work), capture_output=True, text=True, timeout=60)


r = bx_run(f"rm -f {settings}; echo '{{}}' > {settings}; ls /run/agent; mv {work} {sdir}/work2; touch {sdir}/evil; "
           f"rm -rf {sdir}/isolation.json; mv {sdir} {sdir.parent}/moved; echo inside > {work}/ok.txt; echo rc=$?")
after = hashlib.sha256(settings.read_bytes()).hexdigest() if settings.exists() else "gone"
check("実行係は --settings を消す・差し替えることができない", after == before and r.stdout.count("Permission denied") + r.stderr.count("Permission denied") >= 3,
      r.stdout + r.stderr)
check("実行係は会話のフォルダーの付け替え・直下の作成ができない（work の中だけ書ける）",
      work.is_dir() and not (sdir / "evil").exists() and not (sdir.parent / "moved").exists() and (work / "ok.txt").exists(),
      r.stdout + r.stderr)

r = bx_run("setsid sleep 301 >/dev/null 2>&1 < /dev/null & (setsid sh -c 'sleep 302' >/dev/null 2>&1 < /dev/null &) ; "
           "nohup sleep 303 >/dev/null 2>&1 & echo started")


def alive(pat: str) -> bool:
    out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return any(pat in ln and "ps -eo" not in ln for ln in out.splitlines())


for _ in range(20):  # 孫が親を失うのは少し後＝定期の掃除（2 秒ごと）も含めて待つ
    if not any(alive(f"sleep {n}") for n in (301, 302, 303)):
        break
    time.sleep(0.5)
check("setsid・二重 fork で逃げた子はコマンドの終わりに片づく", r.stdout.strip() == "started" and not any(
    alive(f"sleep {n}") for n in (301, 302, 303)), r.stdout + r.stderr)

r = bx_run(f"ln -s /home/agent/.claude/.credentials.json {work}/leak; ln -s /home/agent {work}/scratch/home; "
           f"ln -s {work}/ok.txt {work}/fine; ls {work}")
check("work の外を指すリンクはコマンドの後に消える（中を指すリンクは残る）",
      not os.path.lexists(work / "leak") and not os.path.lexists(work / "scratch" / "home") and os.path.islink(work / "fine"),
      r.stdout + r.stderr)

# ---------------------------------------------------------------- 実行係が無いとき（閉じる側に倒れる）
r = subprocess.run(["/opt/runner/bx", "echo should-not-run"], env={**os.environ, "RUNNER_EXEC_SOCKET": "/nonexistent.sock"},
                   capture_output=True, text=True)
check("実行係に届かないとき bx は走らせない（126）", r.returncode == 126 and "should-not-run" not in r.stdout, r.stdout + r.stderr)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
