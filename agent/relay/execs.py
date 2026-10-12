"""中継の中で本当に DB・backend に触る係（箱には無い）。試験では差し替える。

- ``PsqlExecutor``: 読み取り専用 SQL を agent_ro ロールで（rk.sqlrun の検査・包み方をそのまま使う・docker exec は使わない）
- ``PgDumpBackup``: 反映の直前のバックアップを agent_dump ロールで（記録は rk の backups.jsonl の形 → rk の 30 分の決まりが効く）
- ``backend_api``: エージェント専用の管理者で backend へ（rk.session.RkApi）。User-Agent に承認番号と承認した人
"""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from rk import RkError
from rk.auth import TokenManager
from rk.backup import LABEL_RE, MIN_SIZE, backups_log
from rk.config import Config
from rk.session import RkApi
from rk.sqlrun import parse_output, validate_param, wrap_sql
from rk.transport import Http

from relay.config import PgConn, RelayConfig

USER_AGENT = "rakusuke-agent-relay/1"


def rk_config(cfg: RelayConfig) -> Config:
    """中継の中で rk の部品（applyrun・backup の記録・report）を使うための設定。"""
    data = cfg.data_dir
    c = Config(mode="vps", runs_dir=data / "runs", sql_dir=cfg.agent_dir / "sql", reports_dir=data / "reports",
               home=data / "rakusuke")
    return c


class SqlExecutor(Protocol):
    def run(self, sql: str, params: dict[str, str] | None = None) -> list: ...


class PsqlExecutor:
    def __init__(self, conn: PgConn, row_limit: int = 5000, timeout: float = 60,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.conn, self.row_limit, self.timeout, self.runner = conn, row_limit, timeout, runner

    def argv(self, params: dict[str, str]) -> list[str]:
        a = ["psql", "-h", self.conn.host, "-p", str(self.conn.port), "-U", self.conn.user, "-d", self.conn.database,
             "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At"]
        for k, v in params.items():
            validate_param(k, v)
            a += ["-v", f"{k}={v}"]
        return a

    def run(self, sql: str, params: dict[str, str] | None = None) -> list:
        script = wrap_sql(sql)  # 1 文だけ・メタコマンド無し・読み取り専用トランザクション（rk と同じ）
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PGPASSWORD": self.conn.password,
               "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=30s",
               "PGCONNECT_TIMEOUT": "10"}
        res = self.runner(self.argv(params or {}), input=script.encode("utf-8"), capture_output=True,
                          timeout=self.timeout, env=env)
        if res.returncode != 0:
            raise RkError("SQL の実行に失敗しました: " + (res.stderr or b"").decode("utf-8", "replace").strip()[:800])
        rows = parse_output((res.stdout or b"").decode("utf-8", "replace"))
        if isinstance(rows, list) and len(rows) > self.row_limit:
            raise RkError(f"結果が {len(rows)} 行あります（{self.row_limit} 行まで）。条件を絞ってください")
        return rows


class BackupTaker(Protocol):
    def take(self, label: str) -> dict: ...


def record_backup(rk_cfg: Config, rec: dict) -> None:
    log = backups_log(rk_cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class PgDumpBackup:
    def __init__(self, conn: PgConn, backup_dir: Path, rk_cfg: Config,
                 popen: Callable[..., Any] = subprocess.Popen, min_size: int = MIN_SIZE):
        self.conn, self.dir, self.rk_cfg, self.popen, self.min_size = conn, backup_dir, rk_cfg, popen, min_size

    def take(self, label: str) -> dict:
        if not LABEL_RE.match(label or ""):
            raise RkError("--label は英小文字・数字・- だけです（例 w43-absence）")
        now = datetime.now(timezone.utc)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"agent-{label}-{now.strftime('%Y%m%d-%H%M%S')}.sql.gz"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PGPASSWORD": self.conn.password}
        argv = ["pg_dump", "-h", self.conn.host, "-p", str(self.conn.port), "-U", self.conn.user,
                "-d", self.conn.database, "--no-owner"]
        proc = self.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        with gzip.open(path, "wb") as gz:
            for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
                gz.write(chunk)
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RkError("バックアップに失敗しました: " + err.strip()[:600])
        size = path.stat().st_size
        if size < self.min_size:
            raise RkError(f"バックアップが小さすぎます（{size} バイト）: {path.name}")
        rec = {"label": label, "path": str(path), "at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "size": size}
        record_backup(self.rk_cfg, rec)
        return rec


def user_agent(approval: str | None = None, approver: str | None = None) -> str:
    """監査ログ（audit_logs の User-Agent）から「だれの承認で」を辿れるように。"""
    if not approval:
        return USER_AGENT
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "", f"{approval}")
    who = re.sub(r"[^A-Za-z0-9_.:-]", "", f"{approver or ''}")
    return f"{USER_AGENT} (approval={safe}; approver={who})"


def backend_api(cfg: RelayConfig, approval: str | None = None, approver: str | None = None) -> RkApi:
    http = Http(cfg.backend_url, headers={"User-Agent": user_agent(approval, approver)})
    return RkApi(http, TokenManager(rk_config(cfg), http))
