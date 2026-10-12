"""箱（carelink-agent-sandbox）の runner — 決まった形の CLI 起動だけを受ける小さな常駐。

設計: docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md §1-2・§1-3・§1-4・§2-1・§2-5

守りの要点
- unix ソケット（既定 /run/agent/runner.sock）で待つ。接続ごとに SO_PEERCRED で相手の uid を見て、
  中継の uid（RUNNER_ALLOWED_PEER_UID・既定 10001）以外は即切る。箱の中のモデル（uid 10002）からは使えない。
- 受けるのは次の 4 つだけ（1 行 1 JSON）。任意のコマンド・引数・環境変数・作業フォルダーは受けない。
    {"op":"spawn","session_id":..,"provider":"claude"|"codex","model":..,"resume_id":..?,"relay_token":..,"user_key":..}
    {"op":"auth_status","provider"?:"codex"|"claude","user_key"} … ログインの様子（ok/missing/api_key/unknown の 1 語・既定 codex）
    {"op":"login_start","provider":"codex"|"claude","user_key"} … 画面からのサインイン（箱で同時に 1 つだけ・15 分で打ち切り）
    {"op":"login_poll","user_key"} / {"op":"login_cancel","user_key"} … その人のサインインの様子・やめる（他人のものは idle）
    {"op":"login_input","data":"<貼り付けたコード>","user_key"} … Claude だけ（ブラウザーで出たコードを CLI の stdin へ）
    {"op":"logout","provider":"codex"|"claude","user_key"} … サインアウト（その人の席だけ）
    {"op":"adopt_legacy","user_key"}               … 前の共有のログイン（/home/agent/.codex・.claude）をその人の席へ移す
                                                      （席が空のときだけ・移す元が無くなるので 1 回きり）
      サインインの返事は {"ev":"login","provider","state","url","code","needs_input","expires_in","error"} だけ。
      CLI の文・トークン・メールは返さない（URL は決まったホストのものだけ・コードは決まった形のものだけ）。
- 人ごとの席（PO 2026-10-13: ChatGPT Business/Team は 1 席 1 人・設計 docs/plans/agent-ui/per-user-seats-2026-10-13.md）:
  ログイン情報は HOME_DIR/u/<user_key>/.codex・.claude（CODEX_HOME・CLAUDE_CONFIG_DIR・HOME をその人の席に）。
  user_key は中継が user id から作る不透明な 32 桁の 16 進（[a-f0-9]{32} 以外は断る）。runner は中継からパスを受けない。
  席は 0700・持ち主 10002（HOME_DIR 自体も 0700＝実行係 10004 は入れない）。
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
import stat
import struct
import sys
import time
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
#: --settings の置き場。実行係（uid 10004）が書き換えられない所（/run/agent は agent 持ち・0750）。会話の作業フォルダー側には置かない
SETTINGS_DIR = Path(os.environ.get("RUNNER_SETTINGS_DIR", "/run/agent"))
#: 作業フォルダーを CLI と実行係の両方で書くためのグループ
WORK_GID = int(os.environ.get("RUNNER_WORK_GID", "10005"))
#: Codex のコマンドの決まり（execpolicy）。起動のたびに CODEX_HOME/rules/ に書き直す（モデルが足したものは消す）
CODEX_RULES = RUNTIME_DIR / "rakusuke.rules"
#: Codex の起こし方の決まり（-c の締め付け）。中継と同じファイルをパスで読む（/srv/agent は読み取り専用）
CODEX_CONFIG_PY = AGENT_DIR / "relay" / "codex_config.py"
#: 人ごとの席（ログイン情報）の置き場の名前（HOME_DIR/u/<user_key>/）。前の共有のログインは HOME_DIR/.codex・.claude
SEATS_NAME = "u"
USER_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
#: ログイン・状態を見る CLI の PATH（試験は偽の CLI の置き場を前に足す）
CLI_PATH = os.environ.get("RUNNER_CLI_PATH", "/usr/local/bin:/usr/bin:/bin")
#: 認証情報の隔離（deploy/README.md「認証情報の隔離」）: モデルのコマンドは別 uid の実行係（execd / codex exec-server）で
#: 走らせる。この変数があると、実行係が見えないときは CLI を起こさない（閉じる側に倒す）。無ければ（手元の試験）従来どおり。
EXEC_SOCKET = os.environ.get("RUNNER_EXEC_SOCKET", "")
EXEC_SERVER_URL = os.environ.get("RUNNER_EXEC_SERVER_URL", "ws://127.0.0.1:9911")
BX = RUNTIME_DIR / "bx"
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


def _mode(path: str) -> int:
    try:
        return os.stat(path).st_mode
    except OSError:
        return 0


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def check_user_key(v: object) -> str:
    """中継から来た席の鍵（不透明な 32 桁の 16 進）。形が違えば断る（パスは受けない）。"""
    if not isinstance(v, str) or not USER_KEY_RE.match(v):
        raise ValueError("bad user_key")
    return v


def seat_dir(user_key: str) -> Path:
    return HOME_DIR / SEATS_NAME / check_user_key(user_key)


def provider_home(provider: str, user_key: str) -> Path:
    """その人の CODEX_HOME / CLAUDE_CONFIG_DIR。"""
    return seat_dir(user_key) / (".claude" if provider == "claude" else ".codex")


def ensure_seat(user_key: str) -> Path:
    """席（HOME_DIR/u と HOME_DIR/u/<key>）を 0700 で作る。リンクなら使わない（差し替え防止）。"""
    seat = seat_dir(user_key)
    old = os.umask(0o077)
    try:
        for d in (seat.parent, seat):
            if d.is_symlink():
                raise ValueError("seat is a symlink")
            d.mkdir(mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
    finally:
        os.umask(old)
    return seat


def adopt_legacy(user_key: str) -> list[str]:
    """前の共有のログイン（HOME_DIR/.codex・.claude）を、その人の席へ移す（席に無い・空のときだけ）。
    移したものの名前（codex / claude）を返す。一度でも走ったら印（HOME_DIR/u/.legacy-adopted）を残し、2 回目からは
    だれの鍵で頼まれても何もしない（移さなかった残りがほかの人の席へ移ることはない）。"""
    seat = ensure_seat(user_key)
    mark = seat.parent / ".legacy-adopted"
    if mark.exists() or mark.is_symlink():
        return []
    moved = []
    for name in (".codex", ".claude"):
        src, dst = HOME_DIR / name, seat / name
        if src.is_symlink() or not src.is_dir() or dst.is_symlink():
            continue
        if dst.exists():
            if dst.is_dir() and not any(dst.iterdir()):
                dst.rmdir()
            else:
                continue  # 席にもうログインがある（その人が先にサインインした）＝触らない
        os.rename(src, dst)
        os.chmod(dst, 0o700)
        moved.append(name[1:])
    fd = os.open(mark, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(",".join(moved) + "\n")
    return moved


def build_env(session_dir: Path, provider: str, relay_token: str, user_key: str) -> dict[str, str]:
    """CLI に渡す環境（許可リストで一から作る）。ログイン情報はその会話の持ち主の席（user_key）だけ。"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(seat_dir(user_key)),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "Asia/Tokyo",
        "TMPDIR": "/tmp",
        "HTTPS_PROXY": EGRESS_PROXY,
        "https_proxy": EGRESS_PROXY,
        "HTTP_PROXY": EGRESS_PROXY,
        "http_proxy": EGRESS_PROXY,
        # 中継の内側口と、codex の実行係（ws://127.0.0.1:9911）はプロキシを通さない（同じ箱の中・網の中）。
        # 127.0.0.1 が無いと app-server が実行係へのつなぎをプロキシに投げて 403 になる（2026-10-12 staging で実測）
        "NO_PROXY": "agent-relay,127.0.0.1,localhost",
        "no_proxy": "agent-relay,127.0.0.1,localhost",
        "PYTHONPATH": str(AGENT_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "RK_MODE": "relay",
        "RK_RELAY_URL": RELAY_URL,
        "RK_RELAY_TOKEN": relay_token,
        "RK_WORK_DIR": str(session_dir / "work"),
    }
    if provider == "claude":
        env.update({
            "CLAUDE_CONFIG_DIR": str(provider_home("claude", user_key)),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
        })
    else:
        env.update({
            "CODEX_HOME": str(provider_home("codex", user_key)),
            # rk の包み（/srv/agent/bin/rk）を PATH に（rakusuke.rules が allow する「いちばん楽な道」）
            "PATH": f"{AGENT_DIR}/bin:/usr/local/bin:/usr/bin:/bin",
            # その場の script・計算の置き場所（会話ごと・書ける）
            "TMPDIR": str(session_dir / "work" / "scratch"),
        })
    if EXEC_SOCKET:
        if provider == "claude":
            # Bash の道具のコマンドは全部 bx（→ execd・uid 10004）経由。bx は実行係に届かなければ走らせない
            env.update({"CLAUDE_CODE_SHELL_PREFIX": str(BX), "RUNNER_EXEC_SOCKET": EXEC_SOCKET})
        else:
            # app-server は自分では何も走らせず、実行係（codex exec-server・uid 10004）の環境「remote」だけを使う
            env["CODEX_EXEC_SERVER_URL"] = EXEC_SERVER_URL
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
        "--settings", str(settings_path(session_dir.name)),
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


def codex_exec_env_args(env: dict[str, str]) -> list[str]:
    """codex の実行係（別 uid）は app-server の環境を継がない。rk に要る RK_*・作業の置き場所だけを
    shell_environment_policy.set で渡す（-c の TOML。値は JSON 文字列＝TOML の基本文字列として有効）。"""
    keys = ("RK_MODE", "RK_RELAY_URL", "RK_RELAY_TOKEN", "RK_WORK_DIR", "TMPDIR", "PYTHONPATH", "PATH", "TZ", "LANG")
    pairs = ", ".join(f"{k} = {json.dumps(env[k])}" for k in keys if k in env)
    # ログインシェル（bash -l）は /etc/profile で PATH を既定に戻す＝/srv/agent/bin の rk が見つからなくなるので使わない
    return ["-c", f"shell_environment_policy.set={{{pairs}}}", "-c", "allow_login_shell=false"]


def prepare_codex_home(home: Path) -> None:
    """CODEX_HOME（その人の席・ログイン情報 auth.json がある）を起動のたびに決まった形に戻す。

    - config.toml は空に近い決まった中身に書き直す（モデルが MCP・別のつなぎ先などを書き足していても消える。
      -c の上書きが勝つが、二重に。auth.json には触らない）
    - rules/ は rakusuke.rules だけにする
    """
    home.mkdir(mode=0o700, exist_ok=True)
    cfg = home / "config.toml"
    tmp = home / "config.toml.tmp"
    tmp.write_text("# らく助の runner が起動のたびに書き直します（ここに書いても消えます）" + chr(10), encoding="utf-8")
    os.replace(tmp, cfg)
    rules = home / "rules"
    rules.mkdir(mode=0o700, exist_ok=True)
    for f in rules.iterdir():
        if f.name != CODEX_RULES.name:
            if f.is_file() or f.is_symlink():
                f.unlink()
    rtmp = rules / (CODEX_RULES.name + ".tmp")
    rtmp.write_text(CODEX_RULES.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(rtmp, rules / CODEX_RULES.name)


def auth_env(provider: str, user_key: str) -> dict[str, str]:
    """ログイン・ログアウト・状態を見る CLI の環境（許可リストで一から作る・会話の RK_* は入れない）。その人の席だけ。"""
    env = {
        "PATH": CLI_PATH, "HOME": str(seat_dir(user_key)), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "Asia/Tokyo",
        "TMPDIR": "/tmp", "NO_COLOR": "1",
        "HTTPS_PROXY": EGRESS_PROXY, "https_proxy": EGRESS_PROXY, "HTTP_PROXY": EGRESS_PROXY, "http_proxy": EGRESS_PROXY,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }
    if provider == "claude":
        env.update({"CLAUDE_CONFIG_DIR": str(provider_home("claude", user_key)), "DISABLE_AUTOUPDATER": "1",
                    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
    else:
        env["CODEX_HOME"] = str(provider_home("codex", user_key))
    leaked = [k for k in FORBIDDEN_ENV if k in env]
    if leaked:
        raise ValueError("forbidden env: " + ",".join(leaked))
    return env


def codex_auth_status(user_key: str) -> str:
    """``codex login status`` の結果を「ok / missing / api_key / unknown」に（文は返さない＝鍵の一部も出さない）。"""
    import subprocess

    if not provider_home("codex", user_key).is_dir():
        return "missing"  # まだ一度もログインしていない（置き場が無いと codex は状態を言えない・席も作らない）
    try:
        r = subprocess.run(["codex", "login", "status"], env=auth_env("codex", user_key), capture_output=True,
                           text=True, timeout=20, cwd=str(seat_dir(user_key)))
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


def claude_status_word(stdout: str) -> str:
    """``claude auth status``（JSON）を 1 語に。メール・組織名などは読まずに捨てる（2.1.296 で形を実測）。"""
    try:
        d = json.loads(stdout)
    except ValueError:
        return "unknown"
    if not isinstance(d, dict):
        return "unknown"
    method = d.get("authMethod")
    if method in ("api_key", "api_key_helper", "third_party"):
        return "api_key"
    if not d.get("loggedIn"):
        return "missing"
    if method == "claude.ai":
        return "ok"
    return "unknown"  # oauth_token（環境変数のトークン）などは段 3 では使わない


def claude_auth_status(user_key: str) -> str:
    import subprocess

    if not provider_home("claude", user_key).is_dir():
        return "missing"  # その人はまだ一度もサインインしていない（CLI を起こさず・席も作らない）
    try:
        r = subprocess.run(["claude", "auth", "status"], env=auth_env("claude", user_key), capture_output=True,
                           text=True, timeout=20, cwd=str(seat_dir(user_key)))
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return claude_status_word(r.stdout)


def auth_status(provider: str, user_key: str) -> str:
    return claude_auth_status(user_key) if provider == "claude" else codex_auth_status(user_key)


def run_logout(provider: str, user_key: str) -> bool:
    """``codex logout`` / ``claude auth logout``（その人の席だけ）。ログイン情報のファイルは CLI 自身が消す。"""
    import subprocess

    if not provider_home(provider, user_key).is_dir():
        return True  # 一度もサインインしていない＝もうサインアウトしている
    argv = ["claude", "auth", "logout"] if provider == "claude" else ["codex", "logout"]
    try:
        r = subprocess.run(argv, env=auth_env(provider, user_key), capture_output=True, text=True, timeout=30,
                           cwd=str(seat_dir(user_key)))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# ---------------------------------------------------------------- 画面からのサインイン
#: 1 回のサインインの寿命（codex のデバイスコードは 15 分。claude も同じだけ待つ）
LOGIN_TTL_SEC = float(os.environ.get("RUNNER_LOGIN_TTL", "900"))
#: URL（と codex のコード）が出るまで待つ秒
LOGIN_URL_WAIT_SEC = float(os.environ.get("RUNNER_LOGIN_URL_WAIT", "25"))
LOGIN_BUF_MAX = 64 << 10
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
#: 画面に出してよい URL（決まったホストだけ。CLI の文はほかには返さない）
LOGIN_URL_RE = {
    "codex": re.compile(r"https://auth\.openai\.com/[A-Za-z0-9/_\-.?=&%]{1,200}"),
    "claude": re.compile(r"https://(?:claude\.com|claude\.ai|platform\.claude\.com)/[A-Za-z0-9/_\-.?=&%:+~]{1,2000}"),
}
DEVICE_CODE_RE = re.compile(r"^\s*([A-Z0-9]{4,6}-[A-Z0-9]{4,6})\s*$", re.M)
#: Claude のブラウザーに出るコード（code#state の形）。貼り付けで紛れた空白は前後だけ落とす
PASTE_CODE_RE = re.compile(r"^[A-Za-z0-9_\-#.~]{8,1024}$")
LOGIN_ACTIVE = ("starting", "waiting", "verifying")


class Login:
    def __init__(self, provider: str, proc: asyncio.subprocess.Process, user_key: str = "") -> None:
        self.provider = provider
        self.user_key = user_key
        self.proc = proc
        self.state = "starting"
        self.url: str | None = None
        self.code: str | None = None
        self.error: str | None = None
        self.buf = ""
        self.input_mark = 0
        self.deadline = time.monotonic() + LOGIN_TTL_SEC
        self.task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.state in LOGIN_ACTIVE

    def feed(self, text: str) -> None:
        self.buf = (self.buf + ANSI_RE.sub("", text))[-LOGIN_BUF_MAX:]
        if self.url is None:
            m = LOGIN_URL_RE[self.provider].search(self.buf)
            if m:
                self.url = m.group(0)
        if self.provider == "codex" and self.code is None:
            m = DEVICE_CODE_RE.search(self.buf)
            if m:
                self.code = m.group(1)
        if self.state == "starting" and self.url and (self.provider == "claude" or self.code):
            self.state = "waiting"
        if self.state == "verifying" and "invalid code" in self.buf[self.input_mark:].lower():
            self.state, self.error = "waiting", "invalid_code"   # 形の違うコード（CLI はまだ待っている）

    def public(self) -> dict:
        left = max(0, int(self.deadline - time.monotonic())) if self.active else 0
        return {"ev": "login", "provider": self.provider, "state": self.state, "url": self.url, "code": self.code,
                "needs_input": self.provider == "claude" and self.state == "waiting", "expires_in": left,
                "error": self.error}

    async def kill(self) -> None:
        if self.proc.returncode is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.proc.wait(), 3.0)
        except asyncio.TimeoutError:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


_login: Login | None = None


class LoginRefused(Exception):
    pass


def login_snapshot(user_key: str | None = None) -> dict:
    """その人のサインインの様子（他人のサインインは見せない＝idle）。user_key=None は試験・内部用（いまのもの）。"""
    lg = _login
    if lg is None or (user_key is not None and lg.user_key != user_key):
        return {"ev": "login", "state": "idle"}
    return lg.public()


async def _login_reader(lg: Login) -> None:
    assert lg.proc.stdout is not None
    while True:
        chunk = await lg.proc.stdout.read(4096)
        if not chunk:
            return
        lg.feed(chunk.decode("utf-8", "replace"))


async def _login_watch(lg: Login) -> None:
    reader = asyncio.create_task(_login_reader(lg))
    try:
        code = await asyncio.wait_for(lg.proc.wait(), max(0.0, lg.deadline - time.monotonic()))
    except asyncio.TimeoutError:
        if lg.active:
            lg.state = "timeout"
        await lg.kill()
        code = None
    try:
        await asyncio.wait_for(reader, 3.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        reader.cancel()
    if lg.active:
        lg.state = "done" if code == 0 else "failed"
    log(f"login provider={lg.provider} state={lg.state} code={code}")


async def login_start(provider: str, user_key: str) -> dict:
    global _login
    if provider not in ("codex", "claude"):
        raise LoginRefused("bad provider")
    try:
        check_user_key(user_key)
    except ValueError:
        raise LoginRefused("bad user_key") from None
    if _login is not None and _login.active:
        raise LoginRefused("login busy")  # 箱の中で同時に 1 つだけ（だれのものでも）
    seat = ensure_seat(user_key)
    home = provider_home(provider, user_key)
    if home.is_symlink():
        raise LoginRefused("bad seat")
    old = os.umask(0o077)  # ログイン情報の置き場は持ち主だけ
    try:
        if provider == "codex":
            argv = ["codex", "login", "--device-auth"]
        else:
            argv = ["claude", "auth", "login", "--claudeai"]   # サブスク（Claude アカウント）だけ。--console は使わない
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(home, 0o700)  # 状態を見た CLI が先に作っていても持ち主だけに
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(seat), env=auth_env(provider, user_key), start_new_session=True, close_fds=True)
    finally:
        os.umask(old)
    lg = _login = Login(provider, proc, user_key)
    lg.task = asyncio.create_task(_login_watch(lg))
    end = time.monotonic() + LOGIN_URL_WAIT_SEC
    while lg.state == "starting" and time.monotonic() < end:
        await asyncio.sleep(0.05)
    if lg.state == "starting":  # URL が出ない（つながらない・版が違う）
        lg.state, lg.error = "failed", "no_url"
        await lg.kill()
    log(f"login start provider={provider} state={lg.state}")
    return lg.public()


async def login_input(data: object, user_key: str | None = None) -> dict:
    lg = _login
    if (lg is None or lg.provider != "claude" or lg.state != "waiting"
            or (user_key is not None and lg.user_key != user_key)):
        raise LoginRefused("not waiting for a code")
    if not isinstance(data, str) or not PASTE_CODE_RE.match(data.strip()):
        lg.error = "invalid_code"
        return lg.public()
    if lg.proc.stdin is None or lg.proc.stdin.is_closing():
        raise LoginRefused("stdin closed")
    lg.input_mark = len(lg.buf)
    lg.state, lg.error = "verifying", None
    try:
        lg.proc.stdin.write((data.strip() + "\n").encode("utf-8"))   # コードは記録に残さない
        await lg.proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        raise LoginRefused("stdin closed") from None
    return lg.public()


async def login_cancel(user_key: str | None = None) -> dict:
    lg = _login
    if lg is not None and lg.active and (user_key is None or lg.user_key == user_key):
        lg.state = "cancelled"
        await lg.kill()
    return login_snapshot(user_key)


def settings_path(session_id: str) -> Path:
    return SETTINGS_DIR / f"{session_id}.settings.json"


def _write_settings(session_id: str) -> None:
    """--settings を実行係の手が届かない所へ、毎回消して O_EXCL|O_NOFOLLOW で新しく書く（差し替え・リンクの先書き防止）。"""
    path = settings_path(session_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ISOLATION_TEMPLATE.read_text(encoding="utf-8"))


def prepare_session_dir(session_id: str) -> Path:
    """会話のフォルダー: d は agent 持ち 0750（実行係は入れるが d の中身を消す・付け替えることはできない）。
    実行係が書けるのは d/work（グループ 10005・2770）の中だけ。"""
    d = SESSIONS_DIR / session_id
    work = d / "work"
    (work / "scratch").mkdir(parents=True, exist_ok=True)
    for path, mode in ((d, 0o750), (work, 0o2770), (work / "scratch", 0o2770)):
        try:
            os.chown(path, -1, WORK_GID)
        except PermissionError:  # グループに入っていない開発環境
            pass
        os.chmod(path, mode)
    _write_settings(session_id)
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


def validate_spawn(msg: dict) -> tuple[str, str, str, str | None, str, str]:
    session_id = msg.get("session_id")
    provider = msg.get("provider")
    model = msg.get("model")
    resume_id = msg.get("resume_id")
    token = msg.get("relay_token")
    allowed = {"op", "session_id", "provider", "model", "resume_id", "relay_token", "user_key"}
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
    user_key = check_user_key(msg.get("user_key"))  # 会話の持ち主の席（無い・形が違うなら起こさない）
    return session_id, provider, model, resume_id, token, user_key


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

            if op in ("auth_status", "login_start", "login_poll", "login_input", "login_cancel", "logout",
                      "adopt_legacy"):
                # ログイン・状態はその人の席（user_key）だけ。CLI は runner と同じ uid（10002）で起こす＝実行係（10004）からは見えない
                try:
                    key = check_user_key(msg.get("user_key"))
                except ValueError:
                    await send(writer, {"ev": "error", "message": "bad user_key"})
                    continue
                loop = asyncio.get_running_loop()
                try:
                    if op == "auth_status":
                        # ログインの様子の 1 語だけ（トークン・メール・文は返さない）。中継の「ログイン済み/未」の表示用
                        prov = msg.get("provider") or "codex"
                        if prov not in ("codex", "claude"):
                            raise LoginRefused("bad provider")
                        state = await loop.run_in_executor(None, auth_status, prov, key)
                        await send(writer, {"ev": "auth_status", "provider": prov, "state": state})
                    elif op == "login_start":
                        await send(writer, await login_start(str(msg.get("provider") or ""), key))
                    elif op == "login_poll":
                        await send(writer, login_snapshot(key))
                    elif op == "login_input":
                        await send(writer, await login_input(msg.get("data"), key))
                    elif op == "login_cancel":
                        await send(writer, await login_cancel(key))
                    elif op == "adopt_legacy":
                        moved = await loop.run_in_executor(None, adopt_legacy, key)
                        log(f"adopt_legacy moved={','.join(moved) or '-'}")
                        await send(writer, {"ev": "adopted", "moved": moved})
                    else:
                        prov = msg.get("provider")
                        if prov not in ("codex", "claude"):
                            raise LoginRefused("bad provider")
                        if _login is not None and _login.active and _login.user_key == key:
                            raise LoginRefused("login busy")
                        ok = await loop.run_in_executor(None, run_logout, prov, key)
                        state = await loop.run_in_executor(None, auth_status, prov, key)
                        log(f"logout provider={prov} ok={ok} state={state}")
                        await send(writer, {"ev": "logout", "provider": prov, "ok": ok, "state": state})
                except LoginRefused as e:
                    await send(writer, {"ev": "error", "message": str(e)})
                except (OSError, ValueError):
                    await send(writer, {"ev": "error", "message": "seat unavailable"})
                continue

            if op == "spawn":
                if sess is not None:
                    await send(writer, {"ev": "error", "message": "already spawned on this connection"})
                    continue
                try:
                    session_id, provider, model, resume_id, token, user_key = validate_spawn(msg)
                except ValueError as e:
                    await send(writer, {"ev": "error", "message": str(e)})
                    continue
                if session_id in _running:
                    await send(writer, {"ev": "error", "message": "session already running"})
                    continue
                if len(_running) >= MAX_PROCS:
                    await send(writer, {"ev": "error", "message": "busy"})
                    continue
                if EXEC_SOCKET and not stat.S_ISSOCK(_mode(EXEC_SOCKET)):
                    await send(writer, {"ev": "error", "message": "exec service unavailable"})
                    continue
                try:
                    ensure_seat(user_key)
                except (OSError, ValueError):
                    await send(writer, {"ev": "error", "message": "seat unavailable"})
                    continue
                session_dir = prepare_session_dir(session_id)
                spawn_env = build_env(session_dir, provider, token, user_key)
                if provider == "claude":
                    argv = claude_argv(session_dir, model, resume_id)
                else:
                    prepare_codex_home(provider_home("codex", user_key))
                    argv = codex_argv()  # 続き（resume_id）は中継が thread/resume で頼む
                    if EXEC_SOCKET:
                        argv = [argv[0], *codex_exec_env_args(spawn_env), *argv[1:]]
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(AGENT_DIR),
                    env=spawn_env,
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
    os.umask(0o007)  # 作業フォルダー・/tmp に作る物はグループ（実行係）まで・他人には見せない
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
    if _login is not None:
        await _login.kill()


if __name__ == "__main__":
    asyncio.run(main())
