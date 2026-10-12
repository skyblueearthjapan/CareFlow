"""つなぎ方: HTTP（urllib）・ssh のポート転送・コマンド実行（テストでは差し替える）。"""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from rk import RkError
from rk.config import Config
from rk.opsbase import ApiError

# runner(cmd, input_bytes, timeout) -> CompletedProcess（stdout/stderr は bytes）
Runner = Callable[[list, "bytes | None", float], subprocess.CompletedProcess]


def default_runner(cmd: list, input_bytes: bytes | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RkError(f"コマンドが見つかりません: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise RkError(f"時間切れ（{timeout:.0f} 秒）: {cmd[0]}") from e


def remote_cmd(cfg: Config, argv: list[str]) -> list[str]:
    """VPS で argv を動かすコマンド。pc は ssh 経由（引数はシェル用に引用）、vps はそのまま。"""
    if cfg.mode == "vps":
        return list(argv)
    return ["ssh", "-o", "BatchMode=yes", cfg.ssh, " ".join(shlex.quote(a) for a in argv)]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SshTunnel:
    """ssh -N -L <空きポート>:127.0.0.1:<API ポート> を 1 回の実行のあいだだけ張る（Windows の OpenSSH は ControlMaster が無い）。"""

    def __init__(self, cfg: Config, popen: Callable = subprocess.Popen, wait_seconds: float = 20.0):
        self.cfg = cfg
        self.popen = popen
        self.wait_seconds = wait_seconds
        self.proc = None
        self.port: int | None = None

    def __enter__(self) -> int:
        self.port = free_port()
        cmd = [
            "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-L", f"{self.port}:127.0.0.1:{self.cfg.api_port}", self.cfg.ssh,
        ]
        try:
            self.proc = self.popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as e:
            raise RkError("ssh が見つかりません（Windows の OpenSSH クライアントを入れてください）") from e
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() if self.proc.stderr else b"") or b""
                raise RkError("ssh のポート転送を始められません: " + err.decode("utf-8", "replace")[:400])
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return self.port
            except OSError:
                time.sleep(0.2)
        self.__exit__(None, None, None)
        raise RkError(f"ssh のポート転送が {self.wait_seconds:.0f} 秒で使えるようになりません")

    def __exit__(self, *exc) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        self.proc = None


class Http:
    """base_url（…/api/v1）への JSON の HTTP。2xx 以外は ApiError。"""

    def __init__(self, base_url: str, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None, params: dict | None = None,
                token: str | None = None) -> Any:
        path = normalize_path(path)
        url = self.base_url + path
        if params:
            sep = "&" if "?" in url else "?"
            url += sep + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, method=method.upper(), data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
                return _parse(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise ApiError(e.code, _parse(raw)) from None
        except urllib.error.URLError as e:
            raise RkError(f"API につながりません（{self.base_url}）: {e.reason}") from None


def _parse(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw[:2000]


def normalize_path(path: str) -> str:
    """/api/v1 の後ろのパスだけを許す（別のホストや上の階層へは行かせない）。"""
    if path.startswith("/api/v1/"):
        path = path[len("/api/v1"):]
    if not path.startswith("/"):
        path = "/" + path
    if "://" in path or ".." in path.split("?")[0].split("/") or path.startswith("//"):
        raise RkError(f"パスが正しくありません: {path}")
    return path
