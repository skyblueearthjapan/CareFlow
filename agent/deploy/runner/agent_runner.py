"""箱（carelink-agent-sandbox）の runner — 決まった形の CLI 起動だけを受ける小さな常駐。

設計: docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md §1-2・§1-3・§1-4・§2-1・§2-5

守りの要点
- unix ソケット（既定 /run/agent/runner.sock）で待つ。接続ごとに SO_PEERCRED で相手の uid を見て、
  中継の uid（RUNNER_ALLOWED_PEER_UID・既定 10001）以外は即切る。箱の中のモデル（uid 10002）からは使えない。
- 受けるのは次の 4 つだけ（1 行 1 JSON）。任意のコマンド・引数・環境変数・作業フォルダーは受けない。
    {"op":"spawn","session_id":..,"provider":"claude"|"codex","model":..,"resume_id":..?,"relay_token":..}
    {"op":"auth_status"}                           … Codex の ChatGPT ログインの様子（ok/missing/api_key/unknown）
    {"op":"stdin","data":"<1 行以上の文字列>"}     … spawn した接続でだけ
    {"op":"close_stdin"}                           … 同上（CLI を穏やかに終わらせる）
    {"op":"kill"}                                  … 同上（TERM → 5 秒で KILL・プロセスグループごと）
    {"op":"status"}                                … 動いている会話の一覧
- 返すもの: {"ev":"spawned","pid":..} / {"ev":"out","line":"<CLI の stdout 1 行>"} /
            {"ev":"exit","code":..,"stderr_tail":"<最後の 4KB>"} / {"ev":"error","message":..} / {"ev":"status",..}
- CLI の環境は許可リストで一から作る（runner の環境を継がない）。isolation.json は runner が決まった形で書く
  （中継からは中身を受けない）。
- 接続が切れたら CLI を止める（取り残さない）。同時に動かせる数は RUNNER_MAX_PROCS（既定 2）。

中継から見た注意: この runner が返す行（とくに "out"）は箱の中から来る＝信用しない入力として扱う。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import signal
import socket
import struct
import sys
from pathlib import Path

SOCKET_PATH = os.environ.get("RUNNER_SOCKET", "/run/agent/runner.sock")
ALLOWED_PEER_UID = int(os.environ.get("RUNNER_ALLOWED_PEER_UID", "10001"))
MAX_PROCS = int(os.environ.get("RUNNER_MAX_PROCS", "2"))
EGRESS_PROXY = os.environ.get("RUNNER_EGRESS_PROXY", "http://agent-egress:3128")
RELAY_URL = os.environ.get("RUNNER_RELAY_URL", "http://agent-relay:8091")
AGENT_DIR = Path(os.environ.get("RUNNER_AGENT_DIR", "/srv/agent"))
SESSIONS_DIR = Path(os.environ.get("RUNNER_SESSIONS_DIR", "/data/sessions"))
HOME_DIR = Path(os.environ.get("RUNNER_HOME_DIR", "/home/agent"))
RUNTIME_DIR = Path(__file__).resolve().parent
EMPTY_MCP = RUNTIME_DIR / "empty-mcp.json"
ISOLATION_TEMPLATE = RUNTIME_DIR / "isolation.json"
#: Codex のコマンドの決まり（execpolicy）。起動のたびに CODEX_HOME/rules/ に書き直す（モデルが足したものは消す）
CODEX_RULES = RUNTIME_DIR / "rakusuke.rules"
#: Codex の起こし方の決まり（-c の締め付け）。中継と同じファイルをパスで読む（/srv/agent は読み取り専用）
CODEX_CONFIG_PY = AGENT_DIR / "relay" / "codex_config.py"
CODEX_HOME = HOME_DIR / ".codex"
# 画面の約束（承認は画面のボタン等）。中継の担当が agent/relay/relay-prompt.md に置く。無ければ付けない
APPEND_PROMPT = AGENT_DIR / "relay" / "relay-prompt.md"
MAX_TURNS = "80"

# モデル名の許可（別名か、claude-/gpt- 系の名前だけ）。中継の設定と揃える
CLAUDE_MODELS = re.compile(r"^(sonnet|opus|haiku|claude-[a-z0-9][a-z0-9.\-]{1,60})(\[1m\])?$")
CODEX_MODELS = re.compile(r"^(gpt-[a-z0-9][a-z0-9.\-]{0,60}|o[0-9][a-z0-9.\-]{0,30})$")
SESSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,63}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

MAX_IN_LINE = 1 << 20          # 中継 → runner の 1 行（1 MiB）
MAX_OUT_LINE = 32 << 20        # CLI の stdout の 1 行（stream-json の大きな tool_result 用）
STDERR_TAIL = 4096
KILL_GRACE_SEC = 5.0

#: 箱の CLI に決して渡さない環境変数（API キーでの課金・偽のつなぎ先を防ぐ。ログインはサブスクだけ）。
#: compose でも渡さないが、runner は起動時に自分の環境からも消し、CLI の環境にも入れない（二重）。
FORBIDDEN_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "CODEX_API_KEY",
)

_running: dict[str, "Session"] = {}


def scrub_own_env() -> list[str]:
    """runner 自身の環境から禁止の変数を消す（消した名前を返す。値は出さない）。"""
    found = [k for k in FORBIDDEN_ENV if k in os.environ]
    for k in found:
        os.environ.pop(k, None)
    return found


def log(msg: str) -> None:
    # 本文（会話・患者情報）は出さない。会話 ID・種類・数字だけ
    print(f"[runner] {msg}", file=sys.stderr, flush=True)


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def build_env(session_dir: Path, provider: str, relay_token: str) -> dict[str, str]:
    """CLI に渡す環境（許可リストで一から作る）。"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(HOME_DIR),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "Asia/Tokyo",
        "TMPDIR": "/tmp",
        "HTTPS_PROXY": EGRESS_PROXY,
        "https_proxy": EGRESS_PROXY,
        "HTTP_PROXY": EGRESS_PROXY,
        "http_proxy": EGRESS_PROXY,
        # 中継の内側口はプロキシを通さない（同じ箱の網の中）
        "NO_PROXY": "agent-relay",
        "no_proxy": "agent-relay",
        "PYTHONPATH": str(AGENT_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "RK_MODE": "relay",
        "RK_RELAY_URL": RELAY_URL,
        "RK_RELAY_TOKEN": relay_token,
        "RK_WORK_DIR": str(session_dir / "work"),
    }
    if provider == "claude":
        env.update({
            "CLAUDE_CONFIG_DIR": str(HOME_DIR / ".claude"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
        })
    else:
        env.update({
            "CODEX_HOME": str(CODEX_HOME),
            # rk の包み（/srv/agent/bin/rk）を PATH に（rakusuke.rules が allow する「いちばん楽な道」）
            "PATH": f"{AGENT_DIR}/bin:/usr/local/bin:/usr/bin:/bin",
            # その場の script・計算の置き場所（会話ごと・書ける）
            "TMPDIR": str(session_dir / "work" / "scratch"),
        })
    leaked = [k for k in FORBIDDEN_ENV if k in env]
    if leaked:  # 許可リストで作っているので来ないはず。来たら起こさない
        raise ValueError("forbidden env: " + ",".join(leaked))
    return env


def claude_argv(session_dir: Path, model: str, resume_id: str | None) -> list[str]:
    argv = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-prompt-tool", "stdio",
        "--permission-mode", "default",
        "--setting-sources", "project",
        "--strict-mcp-config", "--mcp-config", str(EMPTY_MCP),
        "--settings", str(session_dir / "isolation.json"),
        "--max-turns", MAX_TURNS,
        "--model", model,                    # --resume の時も毎回付ける（付けないと既定に戻る）
    ]
    if APPEND_PROMPT.is_file():
        argv += ["--append-system-prompt-file", str(APPEND_PROMPT)]
    if resume_id:
        argv += ["--resume", resume_id]
    return argv


def _codex_config():
    spec = importlib.util.spec_from_file_location("rakusuke_codex_config", CODEX_CONFIG_PY)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(str(CODEX_CONFIG_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def codex_argv() -> list[str]:
    """``codex -c …（締め付け）app-server``。モデルは中継が thread/start で渡す（model/list で最新の Sol を選ぶ）。

    sandbox は danger-full-access（箱＝コンテナが砂場。codex の Linux の砂場 bwrap は Docker の中では動かない
    ＝2026-10-12 に 0.162.1 で実測）。締め付けの一覧は agent/relay/codex_config.py（中継と同じ・1 か所）。
    """
    return _codex_config().codex_argv("codex")


def prepare_codex_home() -> None:
    """CODEX_HOME（箱の volume・ログイン情報 auth.json がある）を起動のたびに決まった形に戻す。

    - config.toml は空に近い決まった中身に書き直す（モデルが MCP・別のつなぎ先などを書き足していても消える。
      -c の上書きが勝つが、二重に。auth.json には触らない）
    - rules/ は rakusuke.rules だけにする
    """
    CODEX_HOME.mkdir(mode=0o700, exist_ok=True)
    cfg = CODEX_HOME / "config.toml"
    tmp = CODEX_HOME / "config.toml.tmp"
    tmp.write_text("# らく助の runner が起動のたびに書き直します（ここに書いても消えます）" + chr(10), encoding="utf-8")
    os.replace(tmp, cfg)
    rules = CODEX_HOME / "rules"
    rules.mkdir(mode=0o700, exist_ok=True)
    for f in rules.iterdir():
        if f.name != CODEX_RULES.name:
            if f.is_file() or f.is_symlink():
                f.unlink()
    rtmp = rules / (CODEX_RULES.name + ".tmp")
    rtmp.write_text(CODEX_RULES.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(rtmp, rules / CODEX_RULES.name)


def codex_auth_status() -> str:
    """``codex login status`` の結果を「ok / missing / api_key / unknown」に（文は返さない＝鍵の一部も出さない）。"""
    import subprocess

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(HOME_DIR), "CODEX_HOME": str(CODEX_HOME),
           "LANG": "C.UTF-8"}
    try:
        r = subprocess.run(["codex", "login", "status"], env=env, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    out = (r.stdout + " " + r.stderr).lower()
    if "not logged in" in out:
        return "missing"
    if "chatgpt" in out:
        return "ok"
    if "api key" in out:
        return "api_key"
    return "unknown"


def prepare_session_dir(session_id: str) -> Path:
    d = SESSIONS_DIR / session_id
    (d / "work" / "scratch").mkdir(parents=True, exist_ok=True, mode=0o700)
    iso = d / "isolation.json"
    tmp = d / "isolation.json.tmp"
    tmp.write_text(ISOLATION_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(tmp, iso)
    os.chmod(iso, 0o400)  # CLI からは読めるが書き換えにくくする（同じ uid なので完全ではない・守りは出口と --settings）
    return d


class Session:
    def __init__(self, session_id: str, proc: asyncio.subprocess.Process) -> None:
        self.session_id = session_id
        self.proc = proc
        self.stderr_tail = b""

    async def kill(self) -> None:
        if self.proc.returncode is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.proc.wait(), KILL_GRACE_SEC)
        except asyncio.TimeoutError:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


async def send(writer: asyncio.StreamWriter, obj: dict) -> None:
    writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()


def validate_spawn(msg: dict) -> tuple[str, str, str, str | None, str]:
    session_id = msg.get("session_id")
    provider = msg.get("provider")
    model = msg.get("model")
    resume_id = msg.get("resume_id")
    token = msg.get("relay_token")
    allowed = {"op", "session_id", "provider", "model", "resume_id", "relay_token"}
    if set(msg) - allowed:
        raise ValueError("unknown field")
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError("bad session_id")
    if provider not in ("claude", "codex"):
        raise ValueError("bad provider")
    if not isinstance(model, str):
        raise ValueError("bad model")
    if provider == "claude" and not CLAUDE_MODELS.match(model):
        raise ValueError("bad model")
    if provider == "codex" and not CODEX_MODELS.match(model):
        raise ValueError("bad model")
    if resume_id is not None:
        # claude の会話 id も codex の thread id（UUIDv7）も UUID の形
        if not isinstance(resume_id, str) or not UUID_RE.match(resume_id):
            raise ValueError("bad resume_id")
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise ValueError("bad relay_token")
    return session_id, provider, model, resume_id, token


async def pump_stdout(sess: Session, writer: asyncio.StreamWriter) -> None:
    assert sess.proc.stdout is not None
    while True:
        try:
            line = await sess.proc.stdout.readuntil(b"\n")
        except asyncio.IncompleteReadError as e:
            line = e.partial
            if line:
                await send(writer, {"ev": "out", "line": line.decode("utf-8", "replace").rstrip("\n")})
            return
        except asyncio.LimitOverrunError:
            # 1 行が上限を超えた: 会話を止める（中継は error として扱う）
            await send(writer, {"ev": "error", "message": "stdout line too long"})
            await sess.kill()
            return
        await send(writer, {"ev": "out", "line": line.decode("utf-8", "replace").rstrip("\n")})


async def pump_stderr(sess: Session) -> None:
    assert sess.proc.stderr is not None
    while True:
        chunk = await sess.proc.stderr.read(4096)
        if not chunk:
            return
        sess.stderr_tail = (sess.stderr_tail + chunk)[-STDERR_TAIL:]


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    try:
        uid = peer_uid(sock)
    except OSError:
        uid = -1
    if uid != ALLOWED_PEER_UID:
        log(f"refused peer uid={uid}")
        writer.close()
        return

    sess: Session | None = None
    tasks: list[asyncio.Task] = []
    try:
        while True:
            try:
                raw = await reader.readuntil(b"\n")
            except (asyncio.IncompleteReadError, ConnectionError):
                break
            except asyncio.LimitOverrunError:
                await send(writer, {"ev": "error", "message": "request too long"})
                break
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await send(writer, {"ev": "error", "message": "bad json"})
                break
            op = msg.get("op")

            if op == "status":
                await send(writer, {"ev": "status", "running": sorted(_running), "max": MAX_PROCS})
                continue

            if op == "auth_status":
                # ChatGPT のログインの様子だけ（トークン・文は返さない）。中継の「ログイン済み/未」の表示用
                state = await asyncio.get_running_loop().run_in_executor(None, codex_auth_status)
                await send(writer, {"ev": "auth_status", "provider": "codex", "state": state})
                continue

            if op == "spawn":
                if sess is not None:
                    await send(writer, {"ev": "error", "message": "already spawned on this connection"})
                    continue
                try:
                    session_id, provider, model, resume_id, token = validate_spawn(msg)
                except ValueError as e:
                    await send(writer, {"ev": "error", "message": str(e)})
                    continue
                if session_id in _running:
                    await send(writer, {"ev": "error", "message": "session already running"})
                    continue
                if len(_running) >= MAX_PROCS:
                    await send(writer, {"ev": "error", "message": "busy"})
                    continue
                session_dir = prepare_session_dir(session_id)
                if provider == "claude":
                    argv = claude_argv(session_dir, model, resume_id)
                else:
                    prepare_codex_home()
                    argv = codex_argv()  # 続き（resume_id）は中継が thread/resume で頼む
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(AGENT_DIR),
                    env=build_env(session_dir, provider, token),
                    start_new_session=True,       # プロセスグループごと止められるように
                    limit=MAX_OUT_LINE,
                    close_fds=True,
                )
                sess = Session(session_id, proc)
                _running[session_id] = sess
                log(f"spawn session={session_id} provider={provider} model={model} pid={proc.pid} resume={'y' if resume_id else 'n'}")
                await send(writer, {"ev": "spawned", "pid": proc.pid})
                tasks = [asyncio.create_task(pump_stdout(sess, writer)), asyncio.create_task(pump_stderr(sess))]

                async def _wait_exit(s: Session = sess) -> None:
                    code = await s.proc.wait()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    _running.pop(s.session_id, None)
                    log(f"exit session={s.session_id} code={code}")
                    try:
                        await send(writer, {"ev": "exit", "code": code,
                                            "stderr_tail": s.stderr_tail.decode("utf-8", "replace")})
                    except (ConnectionError, RuntimeError):
                        pass

                asyncio.create_task(_wait_exit())
                continue

            if sess is None:
                await send(writer, {"ev": "error", "message": "spawn first"})
                continue

            if op == "stdin":
                data = msg.get("data")
                if not isinstance(data, str) or set(msg) - {"op", "data"}:
                    await send(writer, {"ev": "error", "message": "bad stdin"})
                    continue
                if (sess.proc.returncode is not None or sess.proc.stdin is None
                        or sess.proc.stdin.is_closing() or len(data) > MAX_IN_LINE):
                    await send(writer, {"ev": "error", "message": "stdin closed"})
                    continue
                if not data.endswith("\n"):
                    data += "\n"
                try:
                    sess.proc.stdin.write(data.encode("utf-8"))
                    await sess.proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    await send(writer, {"ev": "error", "message": "stdin closed"})
            elif op == "close_stdin":
                if sess.proc.stdin is not None and not sess.proc.stdin.is_closing():
                    sess.proc.stdin.close()
            elif op == "kill":
                await sess.kill()
            else:
                await send(writer, {"ev": "error", "message": "unknown op"})
    finally:
        # 中継との接続が切れたら CLI を残さない
        if sess is not None:
            await sess.kill()
            _running.pop(sess.session_id, None)
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    dropped = scrub_own_env()
    if dropped:
        log("dropped forbidden env from runner: " + ",".join(dropped))
    path = Path(SOCKET_PATH)
    if path.exists() or path.is_symlink():
        path.unlink()
    server = await asyncio.start_unix_server(handle, path=str(path), limit=MAX_IN_LINE)
    os.chmod(path, 0o660)  # グループ 10000（中継も入る）。それでも SO_PEERCRED で uid を確かめる
    log(f"listening {path} allowed_uid={ALLOWED_PEER_UID} max_procs={MAX_PROCS}")
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    for s in list(_running.values()):
        await s.kill()


if __name__ == "__main__":
    asyncio.run(main())
