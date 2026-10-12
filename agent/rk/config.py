"""設定（環境変数）と資格情報・トークンの置き場所。

環境変数:
  RK_ENV             prod | staging（既定 prod）。staging は VPS の使い捨て試験環境（agent/staging）:
                     ポート 18101・コンテナ stg-postgres・バックアップ /opt/carelink/agent-staging/backups・
                     資格情報/トークンは ~/.rakusuke/*-staging.json・実行記録は agent/runs/staging
  RK_MODE            pc | vps | relay（既定 pc）。relay = 中継（agent/relay）の内側口だけに話す（箱の中の CLI 用・
                     設計 docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md §2-2）
  RK_RELAY_URL       relay のときの内側口（http://host:port か unix:/path/to.sock）
  RK_RELAY_TOKEN     relay のときの会話ごとの合言葉（中継が CLI を起こすときに渡す）
  RK_WORK_DIR        relay のときの作業フォルダ（runs/・帳票の置き場所。箱の中で書ける所）
  RK_SSH             ssh の行き先（既定 root@72.60.211.213・pc のときだけ使う）
  RK_API_PORT        VPS の中の API のポート（既定 18001）
  RK_PG_CONTAINER    Postgres のコンテナ名（既定 carelink-postgres）
  RK_PG_USER/RK_PG_DB（既定 carelink）
  RK_REMOTE_DIR      VPS のアプリのフォルダ（既定 /opt/carelink。バックアップは <ここ>/backups）
  RK_IDENTIFIER/RK_PASSWORD  ログインの資格情報（無ければ ~/.rakusuke/credentials.json）
  RK_RUNS_DIR / RK_SQL_DIR / RK_REPORTS_DIR  置き場所の差し替え（主にテスト用）
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from rk import RkError

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent
ICON_PATH = REPO_ROOT / "frontend" / "public" / "brand" / "rakusuke-icon-round.png"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SSH_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_RELAY_URL_RE = re.compile(r"^(https?://[A-Za-z0-9_.-]+(:[0-9]+)?/?|unix:/[A-Za-z0-9_./-]+)$")


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def rakusuke_dir() -> Path:
    """~/.rakusuke（資格情報・トークンの置き場所）。"""
    return Path.home() / ".rakusuke"


@dataclass
class Config:
    env: str = "prod"
    mode: str = "pc"
    ssh: str = "root@72.60.211.213"
    api_port: int = 18001
    pg_container: str = "carelink-postgres"
    pg_user: str = "carelink"
    pg_db: str = "carelink"
    remote_dir: str = "/opt/carelink"
    runs_dir: Path = field(default_factory=lambda: AGENT_DIR / "runs")
    sql_dir: Path = field(default_factory=lambda: AGENT_DIR / "sql")
    reports_dir: Path = field(default_factory=lambda: REPO_ROOT / "docs" / "reports" / "agent")
    home: Path = field(default_factory=rakusuke_dir)
    relay_url: str = ""
    relay_token: str = ""

    @property
    def api_base_remote(self) -> str:
        return f"http://127.0.0.1:{self.api_port}/api/v1"

    @property
    def _suffix(self) -> str:
        return "" if self.env == "prod" else f"-{self.env}"

    @property
    def credentials_path(self) -> Path:
        return self.home / f"credentials{self._suffix}.json"

    @property
    def token_path(self) -> Path:
        return self.home / f"token{self._suffix}.json"

    @property
    def login_log_path(self) -> Path:
        return self.home / f"login-attempts{self._suffix}.json"


_ENV_DEFAULTS = {
    "prod": {"port": "18001", "container": "carelink-postgres", "remote_dir": "/opt/carelink"},
    "staging": {"port": "18101", "container": "stg-postgres", "remote_dir": "/opt/carelink/agent-staging"},
}


def load_config() -> Config:
    env = _env("RK_ENV", "prod").lower()
    if env not in _ENV_DEFAULTS:
        raise RkError(f"RK_ENV は prod か staging です（今: {env}）")
    d = _ENV_DEFAULTS[env]
    mode = _env("RK_MODE", "pc").lower()
    if mode not in ("pc", "vps", "relay"):
        raise RkError(f"RK_MODE は pc・vps・relay のどれかです（今: {mode}）")
    try:
        port = int(_env("RK_API_PORT", d["port"]))
    except ValueError as e:
        raise RkError("RK_API_PORT は数字です") from e
    cfg = Config(
        env=env,
        mode=mode,
        ssh=_env("RK_SSH", "root@72.60.211.213"),
        api_port=port,
        pg_container=_env("RK_PG_CONTAINER", d["container"]),
        pg_user=_env("RK_PG_USER", "carelink"),
        pg_db=_env("RK_PG_DB", "carelink"),
        remote_dir=_env("RK_REMOTE_DIR", d["remote_dir"]),
        home=rakusuke_dir(),
    )
    if env != "prod":
        cfg.runs_dir = cfg.runs_dir / env
    if mode == "relay":
        cfg.relay_url = _env("RK_RELAY_URL", "")
        cfg.relay_token = _env("RK_RELAY_TOKEN", "")
        if not _RELAY_URL_RE.match(cfg.relay_url):
            raise RkError("RK_MODE=relay では RK_RELAY_URL（http://host:port か unix:/path）が要ります")
        if not cfg.relay_token:
            raise RkError("RK_MODE=relay では RK_RELAY_TOKEN が要ります（中継が渡します）")
        work = os.environ.get("RK_WORK_DIR")
        if work:
            cfg.runs_dir = Path(work) / "runs"
            cfg.reports_dir = Path(work) / "reports"
    if os.environ.get("RK_RUNS_DIR"):
        cfg.runs_dir = Path(os.environ["RK_RUNS_DIR"])
    if os.environ.get("RK_SQL_DIR"):
        cfg.sql_dir = Path(os.environ["RK_SQL_DIR"])
    if os.environ.get("RK_REPORTS_DIR"):
        cfg.reports_dir = Path(os.environ["RK_REPORTS_DIR"])
    for key, val in (("RK_PG_CONTAINER", cfg.pg_container), ("RK_PG_USER", cfg.pg_user), ("RK_PG_DB", cfg.pg_db)):
        if not _NAME_RE.match(val):
            raise RkError(f"{key} に使えない文字があります")
    if not _SSH_RE.match(cfg.ssh):
        raise RkError("RK_SSH に使えない文字があります")
    if not re.match(r"^/[A-Za-z0-9_./-]+$", cfg.remote_dir):
        raise RkError("RK_REMOTE_DIR は / から始まる英数字のパスです")
    return cfg


def load_credentials(cfg: Config) -> tuple[str, str] | None:
    """(identifier, password)。環境変数が先、次に ~/.rakusuke/credentials.json。無ければ None。"""
    ident = os.environ.get("RK_IDENTIFIER")
    pw = os.environ.get("RK_PASSWORD")
    if ident and pw:
        return ident, pw
    p = cfg.credentials_path
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RkError(f"{p} を読めません（JSON の形を確かめてください）") from e
        ident = d.get("identifier") or d.get("email")
        pw = d.get("password")
        if ident and pw:
            return str(ident), str(pw)
    return None


def write_private_json(path: Path, data: dict) -> None:
    """本人だけが読めるように書く（Windows では chmod は効かないが、ホームの下なので可）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
