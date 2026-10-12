"""反映の前のバックアップ（VPS で pg_dump → /opt/carelink/backups/agent-<名前>-<UTC>.sql.gz）。

記録は agent/runs/backups.jsonl（{label, path, at_utc, size}）。rk apply は 30 分以内の記録を求める。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rk import RkError
from rk.config import Config
from rk.transport import Runner, default_runner, remote_cmd

LABEL_RE = re.compile(r"^[a-z0-9-]+$")
FRESH_MINUTES = 30
MIN_SIZE = 100_000  # 本番の dump の gzip は数 MB。これより小さければ失敗とみなす


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backups_log(cfg: Config) -> Path:
    return cfg.runs_dir / "backups.jsonl"


def run_backup(cfg: Config, label: str, runner: Runner = default_runner, now: datetime | None = None) -> dict:
    if not LABEL_RE.match(label or ""):
        raise RkError("--label は英小文字・数字・- だけです（例 w43-absence）")
    now = now or _utcnow()
    rel = f"backups/agent-{label}-{now.strftime('%Y%m%d-%H%M')}.sql.gz"
    path = f"{cfg.remote_dir.rstrip('/')}/{rel}"
    script = (
        f"set -o pipefail; cd {cfg.remote_dir} && mkdir -p backups && "
        f"docker exec {cfg.pg_container} pg_dump -U {cfg.pg_user} -d {cfg.pg_db} | gzip > {rel} && "
        f"stat -c %s {rel} && ls -l {rel}"
    )
    res = runner(remote_cmd(cfg, ["bash", "-c", script]), None, 900)
    out = (res.stdout or b"").decode("utf-8", "replace")
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace")
        raise RkError("バックアップに失敗しました: " + err.strip()[:600])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    try:
        size = int(lines[0].strip())
    except (IndexError, ValueError):
        raise RkError("バックアップの大きさを読めません: " + out[:300]) from None
    if size < MIN_SIZE:
        raise RkError(f"バックアップが小さすぎます（{size} バイト）。中身を確かめてください: {path}")
    rec = {"label": label, "path": path, "at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "size": size}
    log = backups_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    rec["ls"] = lines[-1] if len(lines) > 1 else ""
    return rec


def recent_backup(cfg: Config, now: datetime | None = None, minutes: int = FRESH_MINUTES) -> dict | None:
    log = backups_log(cfg)
    if not log.exists():
        return None
    now = now or _utcnow()
    best = None
    for ln in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
            at = datetime.strptime(rec["at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        if timedelta(0) <= now - at <= timedelta(minutes=minutes) or timedelta(minutes=-2) <= now - at < timedelta(0):
            best = rec
    return best
