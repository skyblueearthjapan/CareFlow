"""作業の記録（work journal・ずっと残す・PO 決定 2026-10-12）。

JSON Lines を 1 行ずつ足すだけ。**利用者さん・職員の名前は入れない**: 決まった項目だけを、
英数字と ``_.:-`` だけの値で書く（日本語の文字列・案の題名・ラベル・エラー文は落とす）。

置き場所（上から順に最初に決まったもの）:
  RK_JOURNAL_PATH                     そのファイル
  AGENT_RELAY_DATA_DIR（中継の /data） <それ>/journal/work-journal.jsonl（中継の data volume）
  cfg.runs_dir                         <runs>/journal/work-journal.jsonl（手元 = agent/runs・git 対象外）

書けなくても例外は出さない（記録の失敗で反映を止めない）。標準ライブラリだけ・Python 3.10 で動く
（VPS のホストの python3 からも使う: agent/deploy/cleanup）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE_NAME = "work-journal.jsonl"

#: 書いてよい項目（これ以外は落とす）
ALLOWED_KEYS = frozenset({
    "ts", "kind", "user_id", "session_id", "plan_id", "plan_kind", "plan_version", "run_id", "undo_of",
    "ops", "total", "approved", "applied", "unknown", "result", "backup",
    "scope", "trigger", "dry_run", "deleted", "free_gb", "host", "errors",
})
_SAFE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")


def _utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def journal_path(cfg: Any = None) -> Path:
    p = os.environ.get("RK_JOURNAL_PATH")
    if p:
        return Path(p)
    d = os.environ.get("AGENT_RELAY_DATA_DIR")
    if d:
        return Path(d) / "journal" / FILE_NAME
    runs = getattr(cfg, "runs_dir", None)
    if runs is not None:
        return Path(runs) / "journal" / FILE_NAME
    return Path(__file__).resolve().parents[1] / "runs" / "journal" / FILE_NAME


def _clean(v: Any, depth: int = 0) -> Any:
    """名前が入らない形だけ通す: bool・数・英数字の文字列・それらの dict（キーも英数字）。"""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v if _SAFE.match(v) else None
    if isinstance(v, dict) and depth < 2:
        out = {}
        for k, x in v.items():
            if isinstance(k, str) and _SAFE.match(k):
                c = _clean(x, depth + 1)
                if c is not None:
                    out[k] = c
        return out
    return None


def sanitize(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if k in ALLOWED_KEYS:
            c = _clean(v)
            if c is not None:
                out[k] = c
    return out


def append(rec: dict, path: Path | None = None, cfg: Any = None, now: datetime | None = None) -> bool:
    """1 行足す。戻り値: 書けたか。例外は出さない。"""
    try:
        p = Path(path) if path else journal_path(cfg)
        line = sanitize({**rec, "ts": _utc_iso(now)})
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=True, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


_USER_IN_APPROVED = re.compile(r"user ([A-Za-z0-9_.:-]{1,64})")


def _slug(s: Any) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(s or "").lower()).strip("-")[:40] or "plan"


def apply_record(plan: dict, approved: str | None, source: dict | None, run_id: str,
                 applied: int, unknown: int, error: str | None, backup: bool = True) -> dict:
    """反映 1 回分の記録（名前を含まない）。中身の文（題名・ラベル・エラー文）は入れない。"""
    src = source or {}
    ops = plan.get("ops") or []
    user = src.get("user_id")
    if not user and approved:
        m = _USER_IN_APPROVED.search(approved)
        user = m.group(1) if m else None
    canon = json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    if error is None:
        result = "ok"
    else:
        result = "stopped" if (applied or unknown) else "failed"
    return {
        "kind": "undo" if src.get("undo_of") else "apply",
        "user_id": str(user) if user else None,
        "session_id": src.get("relay_session"),
        "plan_id": src.get("plan_id") or "sha256:" + hashlib.sha256(canon).hexdigest()[:16],
        "plan_version": src.get("plan_version"),
        "plan_kind": _slug(plan.get("kind")),
        "run_id": run_id,
        "undo_of": src.get("undo_of"),
        "ops": dict(Counter(str(o.get("op", "?")) for o in ops)),
        "total": len(ops),
        "approved": bool((approved or "").strip()),
        "applied": applied,
        "unknown": unknown,
        "result": result,
        "backup": bool(backup),
    }


def record_apply(cfg: Any, plan: dict, approved: str | None, source: dict | None, run_id: str,
                 applied: int, unknown: int, error: str | None, backup: bool = True) -> bool:
    """rk.applyrun.apply_plan の終わりに 1 回呼ぶ。失敗しても例外を出さない。"""
    try:
        return append(apply_record(plan, approved, source, run_id, applied, unknown, error, backup), cfg=cfg)
    except Exception:
        return False
