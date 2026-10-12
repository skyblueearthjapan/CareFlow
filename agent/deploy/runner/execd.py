"""命令の実行係（execd）— 箱の中で uid 10004（agentx）で動き、モデルが出すコマンドをここで走らせる。

なぜ要るか（段 3 のレビュー指摘・認証情報の隔離）:
  CLI（claude / codex）は uid 10002（agent）で動き、ログイン情報（~/.claude/.credentials*・CODEX_HOME/auth.json）を
  持つ。モデルが出すシェルのコマンドを CLI と同じ uid で走らせると `cat $CODEX_HOME/auth.json` で読めてしまう。
  そこで **モデルのコマンドは必ずこの係（uid 10004・ログイン情報は読めない）に頼んで走らせる**。
  - claude: CLAUDE_CODE_SHELL_PREFIX=/opt/runner/bx（Bash の道具の全コマンドが bx 経由）
  - codex : CODEX_EXEC_SERVER_URL（codex の exec-server＝別 uid 10004 の実行係。ここではなく entrypoint.sh が起こす。
            この変数があると app-server は自分では何も走らせず「local environment is not configured」になる）

つなぎ方: unix ソケット（既定 /run/x/exec.sock・0660・グループ 10005）。接続ごとに SO_PEERCRED で相手の uid を見て、
CLI の uid（EXECD_ALLOWED_PEER_UID・既定 10002）以外は即切る。1 回の依頼 = 1 通のメッセージ:
    本文 {"argv": [...], "cwd": "/…", "env": {…}}（1 行の JSON）＋ fd 3 本（相手の stdin / stdout / stderr）
fd をそのまま子に渡すので、出力は流れたまま・終了コードだけ {"exit": N} で返す。相手が切れたら子をプロセスグループごと止める。
標準ライブラリだけ。
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCKET_PATH = os.environ.get("EXECD_SOCKET", "/run/x/exec.sock")
ALLOWED_PEER_UID = int(os.environ.get("EXECD_ALLOWED_PEER_UID", "10002"))
MAX_CONCURRENT = int(os.environ.get("EXECD_MAX_CONCURRENT", "16"))
HOME = os.environ.get("EXECD_HOME", "/home/agentx")

#: 相手の環境から引き継ぐ名前（ここに無いものは捨てる＝CODEX_HOME・CLAUDE_CONFIG_DIR・HOME などは通さない）
ENV_KEEP = re.compile(r"^(RK_[A-Z_]+|TMPDIR|PYTHONPATH|PYTHONIOENCODING|PYTHONDONTWRITEBYTECODE|LANG|LC_ALL|TZ|"
                      r"COLUMNS|LINES|[Hh][Tt][Tt][Pp][Ss]?_[Pp][Rr][Oo][Xx][Yy]|[Nn][Oo]_[Pp][Rr][Oo][Xx][Yy])$")
#: PATH に入れてよい所（相手が渡した PATH はこれに絞る）
PATH_OK = ("/srv/agent/bin", "/usr/local/bin", "/usr/bin", "/bin")
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_HEADER = 1 << 16
KILL_GRACE = 3.0

SESSIONS_DIR = Path(os.environ.get("EXECD_SESSIONS_DIR", "/data/sessions"))
SWEEP_SEC = float(os.environ.get("EXECD_SWEEP_SEC", "2"))
#: 箱の中（uid 10004 専用）だけ true。試験など、ほかのプロセスと同じ uid で動かすときは EXECD_SWEEP=0（無関係なプロセスを止めないため）
SWEEP_ENABLED = os.environ.get("EXECD_SWEEP", "1") != "0"

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_active: set[int] = set()  # 走っている要求の子の pid
_active_lock = threading.Lock()


def become_subreaper() -> None:
    """setsid・二重 fork で親を離れた子孫が execd の子に付け替わるようにする（あとで掃除できる）。"""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        pass


def _proc_info(pid: int) -> tuple[int, int] | None:
    """(uid, ppid)。読めなければ None。"""
    try:
        uid = ppid = -1
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Uid:"):
                uid = int(line.split()[1])
            elif line.startswith("PPid:"):
                ppid = int(line.split()[1])
        return uid, ppid
    except (OSError, ValueError):
        return None


def sweep_orphans() -> int:
    """要求から外れて残った自分の uid のプロセスを止める。
    (1) execd の直接の子で、走っている要求の子ではないもの（subreaper に付け替わった孫）
    (2) 親が init(1) のもの（codex exec-server の子が逃げたもの）。execd 自身・exec-server 本体は親が init ではないので当たらない。"""
    me, uid, killed = os.getpid(), os.getuid(), 0
    if not SWEEP_ENABLED:
        return 0
    with _active_lock:
        active = set(_active)
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        info = _proc_info(pid)
        if info is None or info[0] != uid or pid == me:
            continue
        if (info[1] == me and pid not in active) or info[1] == 1:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
            if info[1] == me:  # subreaper なので、片づけた子の後始末（zombie）は自分でする
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
    return killed


def sweep_links(root: Path) -> int:
    """作業フォルダーの中の、外を指すシンボリックリンクを消す（CLI の uid の道具が /home/agent などへ辿るのを防ぐ）。"""
    removed = 0
    try:
        base = root.resolve()
    except OSError:
        return 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if not p.is_symlink():
                continue
            try:
                target = p.resolve()
                inside = target == base or base in target.parents
            except (OSError, RuntimeError):
                inside = False
            if not inside:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def work_root_of(path: str) -> Path | None:
    """/data/sessions/<id>/work の形の場所を返す（無ければ None）。"""
    try:
        rel = Path(path).resolve().relative_to(SESSIONS_DIR.resolve())
    except (ValueError, OSError):
        return None
    if len(rel.parts) >= 2 and rel.parts[1] == "work":
        return SESSIONS_DIR / rel.parts[0] / "work"
    return None


def sweep_all_links() -> int:
    n = 0
    try:
        for d in SESSIONS_DIR.iterdir():
            w = d / "work"
            if w.is_dir() and not w.is_symlink():
                n += sweep_links(w)
    except OSError:
        pass
    return n


def sweeper_loop() -> None:
    while True:
        time.sleep(SWEEP_SEC)
        try:
            sweep_orphans()
            sweep_all_links()
        except Exception as e:  # noqa: BLE001  掃除が落ちても execd は止めない
            log(f"sweep error: {type(e).__name__}")


def log(msg: str) -> None:
    print(f"[execd] {msg}", file=sys.stderr, flush=True)  # コマンドの中身は出さない（患者情報が入りうる）


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def build_env(req_env: dict) -> dict[str, str]:
    env = {"PATH": DEFAULT_PATH, "HOME": HOME, "LANG": "C.UTF-8", "TZ": "Asia/Tokyo", "SHELL": "/bin/bash"}
    for k, v in (req_env or {}).items():
        if isinstance(k, str) and isinstance(v, str) and ENV_KEEP.match(k) and "\0" not in v:
            env[k] = v
    want = (req_env or {}).get("PATH")
    if isinstance(want, str):
        keep = [p for p in want.split(":") if p in PATH_OK]
        if keep:
            env["PATH"] = ":".join(dict.fromkeys(keep + DEFAULT_PATH.split(":")))
    return env


def parse_request(raw: bytes) -> tuple[list[str], str, dict]:
    msg = json.loads(raw.decode("utf-8"))
    argv = msg.get("argv")
    if (not isinstance(argv, list) or not argv or len(argv) > 64
            or not all(isinstance(a, str) and "\0" not in a for a in argv)):
        raise ValueError("bad argv")
    cwd = msg.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or not Path(cwd).is_dir():
        cwd = "/srv/agent"
    env = msg.get("env")
    return argv, cwd, env if isinstance(env, dict) else {}


def killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def serve(conn: socket.socket) -> None:
    fds: list[int] = []
    proc = None
    root = None
    try:
        uid = peer_uid(conn)
        if uid != ALLOWED_PEER_UID:
            log(f"refused peer uid={uid}")
            return
        raw, fds, _flags, _addr = socket.recv_fds(conn, MAX_HEADER, 3)
        if len(fds) != 3:
            conn.sendall(b'{"error":"need 3 fds"}\n')
            return
        try:
            argv, cwd, req_env = parse_request(raw)
        except (ValueError, UnicodeDecodeError):
            conn.sendall(b'{"error":"bad request"}\n')
            return
        root = work_root_of(cwd) or work_root_of((req_env or {}).get("RK_WORK_DIR", ""))
        if root is not None:
            sweep_links(root)  # 要求の前: 外を指すリンクを消してから走らせる
        try:
            with _active_lock:  # 起こしてから _active に入れるまでの間に、ほかの要求の掃除に当たらないように
                proc = subprocess.Popen(argv, stdin=fds[0], stdout=fds[1], stderr=fds[2], cwd=cwd,
                                        env=build_env(req_env), start_new_session=True, close_fds=True)
                _active.add(proc.pid)
        except OSError as e:
            conn.sendall((json.dumps({"exit": 127, "error": e.strerror or "spawn failed"}) + "\n").encode())
            return
        for fd in fds:  # 子に渡したので手元は閉じる（出力の管が子の終了で閉じるように）
            os.close(fd)
        fds = []
        while proc.poll() is None:
            r, _, _ = select.select([conn], [], [], 0.2)
            if r and not conn.recv(1):  # 相手（bx）が切れた＝タイムアウト・中断。子をまるごと止める
                killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(KILL_GRACE)
                except subprocess.TimeoutExpired:
                    killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                sweep_orphans()
                if root is not None:
                    sweep_links(root)
                return
        killpg(proc.pid, signal.SIGKILL)  # 残りの子（バックグラウンド）を残さない
        sweep_orphans()  # setsid・二重 fork で逃げた子孫（subreaper で execd の子になっている）
        if root is not None:
            sweep_links(root)  # 要求の後: この間に張られた外へのリンクも消す
        rc = proc.returncode
        conn.sendall((json.dumps({"exit": 128 - rc if rc < 0 else rc}) + "\n").encode())
    except OSError:
        pass
    finally:
        with _active_lock:
            if proc is not None:
                _active.discard(proc.pid)
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        conn.close()
        _slots.release()


def main() -> None:
    if SWEEP_ENABLED:
        become_subreaper()
        threading.Thread(target=sweeper_loop, daemon=True).start()
    os.umask(0o007)  # 子が作る物は 660（グループ 10005＝CLI も読める）
    path = Path(SOCKET_PATH)
    if path.exists() or path.is_symlink():
        path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    os.chmod(path, 0o660)
    srv.listen(32)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    log(f"listening {path} allowed_uid={ALLOWED_PEER_UID} uid={os.getuid()}")
    while True:
        conn, _ = srv.accept()
        if not _slots.acquire(blocking=False):
            conn.close()
            continue
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
