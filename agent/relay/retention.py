"""保存期間（retention）と片づけ・ディスクの見張り（PO 決定 2026-10-12）。

| もの | 置き場所 | 期間 |
|---|---|---|
| 案・実行の記録（JSON） | 中継 /data/runs/<run-id>/・/data/runs/plans/*.json・会話の記録 DB の plans/runs/approvals | 1 年 |
| 会話・イベント | 会話の記録 DB の events・tool_calls（案も実行も無い会話は会話ごと） | 90 日 |
| 反映前の pg_dump | agent-*.sql.gz（/opt/carelink/backups と /opt/carelink/backups/agent） | 14 日・ただし新しい 5 個は必ず残す |
| CLI の会話の写し・作業フォルダー | 箱 ~/.claude/projects・~/.codex/{sessions,log,logs}・/data/sessions/<id>/、中継 /data/sessions・/data/reports | 7 日 |
| 作業の記録（journal） | 中継の data volume の journal/work-journal.jsonl（名前なし） | ずっと |
| アプリの既存バックアップ（PO 了承 2026-10-12） | /opt/carelink/backups の daily-*.sql.gz | 30 日＋各月の最初の 1 個を 12 か月 |
| 〃 | /opt/carelink/backups の pre-deploy-*・pre-*（.sql/.sql.gz） | 60 日 |
| 〃 | 種類ごと（daily・pre-deploy・pre・agent）に新しい 3 個（agent は 5 個）は必ず残す | — |

時刻はすべて UTC（ファイル名の YYYYMMDD-HHMM[SS] を UTC として読む。読めなければ更新時刻）。
「N 日」は「ちょうど N 日前は残す・それより古いものを消す」。

使い方（既定は表示だけ＝dry-run。消すのは --apply のときだけ。消したら件数とバイト数を journal に書く）:
  python3 -I agent/relay/retention.py app-backups   --dir /opt/carelink/backups [--apply] [--journal F]
  python3 -I agent/relay/retention.py agent-backups --dir D [--dir D2] [--apply] [--journal F]
  python3 -I agent/relay/retention.py sandbox [--apply] [--json]          （箱の中: CLI の写し・作業フォルダー）
  python -m relay.retention relay [--apply] [--journal F]                （中継の中: runs・reports・DB）
  python3 -I agent/relay/retention.py journal-append --journal F < summary.json
  python3 -I agent/relay/retention.py disk [--path P] [--min-free-gb 10]  （空きが少なければ終了コード 2）

標準ライブラリだけ・Python 3.10（VPS のホストの python3）で動く。DB の片づけ（relay）だけ SQLAlchemy を使う。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

UTC = timezone.utc

# ---- 期間（PO 決定 2026-10-12） --------------------------------------------------------------
PLAN_RUN_DAYS = 365
CONVERSATION_DAYS = 90
AGENT_BACKUP_DAYS = 14
AGENT_BACKUP_KEEP = 5
SCRATCH_DAYS = 7
APP_DAILY_DAYS = 30
APP_MONTHLY_MONTHS = 12
APP_PRE_DAYS = 60
APP_KEEP_NEWEST = 3
MIN_FREE_GB = 10

DISK_WARNING_JA = "サーバーの空き容量が少なくなっています（{free:.1f} GB）。古い記録の片づけを早めに動かします。"

_STAMP = re.compile(r"(?<![0-9])(20[0-9]{6})-([0-9]{4})([0-9]{2})?(?![0-9])")
_RUN_DIR = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


# ---- 選ぶ（純粋な関数・テストの対象） -----------------------------------------------------------
@dataclass
class Item:
    path: str
    ts: datetime          # UTC
    size: int = 0
    kind: str = ""
    reason: str = ""


def parse_stamp(name: str) -> datetime | None:
    """ファイル名の YYYYMMDD-HHMM[SS] を UTC として読む。"""
    m = _STAMP.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2) + (m.group(3) or "00"), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _aware(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def is_expired(ts: datetime, now: datetime, days: int) -> bool:
    """ちょうど days 日前は残す（境目は残す側）。"""
    return _aware(now) - _aware(ts) > timedelta(days=days)


def select_older_than(items: Iterable[Item], now: datetime, days: int) -> list[Item]:
    out = []
    for it in items:
        if is_expired(it.ts, now, days):
            it.reason = f"older than {days}d"
            out.append(it)
    return out


def select_keep_newest(items: Iterable[Item], now: datetime, days: int, keep: int) -> list[Item]:
    """days より古いものを消す。ただし新しい keep 個は古くても残す。"""
    ordered = sorted(items, key=lambda i: (_aware(i.ts), i.path), reverse=True)
    return select_older_than(ordered[keep:], now, days)


def classify_app_backup(name: str) -> str | None:
    """アプリの既存バックアップの種類。これ以外（pre_・predeploy_・carelink_pre_・フォルダー等）は触らない。"""
    if name.startswith("agent-"):
        return None  # エージェントの決まり（agent-backups）で扱う
    if name.startswith("daily-") and name.endswith(".sql.gz"):
        return "daily"
    if not (name.endswith(".sql.gz") or name.endswith(".sql")):
        return None
    if name.startswith("pre-deploy-"):
        return "pre-deploy"
    if name.startswith("pre-"):
        return "pre"
    return None


def _month_index(d: datetime) -> int:
    d = _aware(d)
    return d.year * 12 + d.month - 1


def select_app_backups(items: Iterable[Item], now: datetime) -> list[Item]:
    """daily: 30 日＋各月の最初の 1 個を 12 か月（今月を含めて 12 か月）。pre-deploy/pre: 60 日。種類ごとに新しい 3 個は残す。"""
    by_kind: dict[str, list[Item]] = {}
    for it in items:
        if it.kind:
            by_kind.setdefault(it.kind, []).append(it)
    out: list[Item] = []
    for kind, lst in sorted(by_kind.items()):
        ordered = sorted(lst, key=lambda i: (_aware(i.ts), i.path), reverse=True)
        rest = ordered[APP_KEEP_NEWEST:]
        if kind == "daily":
            first_of_month: dict[int, Item] = {}
            for it in sorted(lst, key=lambda i: (_aware(i.ts), i.path)):
                first_of_month.setdefault(_month_index(it.ts), it)
            keep_monthly = {id(it) for mi, it in first_of_month.items()
                            if _month_index(now) - mi < APP_MONTHLY_MONTHS}
            for it in rest:
                if id(it) in keep_monthly:
                    continue
                if is_expired(it.ts, now, APP_DAILY_DAYS):
                    it.reason = f"daily older than {APP_DAILY_DAYS}d (not first-of-month within {APP_MONTHLY_MONTHS} months)"
                    out.append(it)
        elif kind in ("pre-deploy", "pre"):
            for it in rest:
                if is_expired(it.ts, now, APP_PRE_DAYS):
                    it.reason = f"{kind} older than {APP_PRE_DAYS}d"
                    out.append(it)
    return out


# ---- 読む（ファイル） ---------------------------------------------------------------------------
def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC)


def scan_files(d: Path, classify: Callable[[str], str | None]) -> list[Item]:
    """d の直下の普通のファイルだけ（フォルダー・リンクは見ない）。"""
    out: list[Item] = []
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if p.is_symlink() or not p.is_file():
            continue
        kind = classify(p.name)
        if not kind:
            continue
        out.append(Item(str(p), parse_stamp(p.name) or _mtime(p), p.stat().st_size, kind))
    return out


def classify_agent_backup(name: str) -> str | None:
    return "agent" if name.startswith("agent-") and name.endswith(".sql.gz") else None


def _tree_size_and_newest(p: Path) -> tuple[int, datetime]:
    size, newest = 0, _mtime(p)
    for root, _dirs, files in os.walk(p):
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.lstat()
            except OSError:
                continue
            size += st.st_size
            newest = max(newest, datetime.fromtimestamp(st.st_mtime, UTC))
    return size, newest


def scan_run_records(runs_dir: Path) -> list[Item]:
    """runs/<run-id>/（名前の時刻）と runs/plans/*.json（更新時刻）。journal・backups.jsonl は見ない。"""
    out: list[Item] = []
    if not runs_dir.is_dir():
        return out
    for p in sorted(runs_dir.iterdir()):
        if p.is_dir() and not p.is_symlink() and _RUN_DIR.match(p.name):
            size, _ = _tree_size_and_newest(p)
            out.append(Item(str(p), parse_stamp(p.name) or _mtime(p), size, "run"))
    plans = runs_dir / "plans"
    if plans.is_dir():
        for p in sorted(plans.glob("*.json")):
            if p.is_file() and not p.is_symlink():
                out.append(Item(str(p), _mtime(p), p.stat().st_size, "plan"))
    return out


def scan_folders(d: Path, kind: str) -> list[Item]:
    """d の直下のフォルダーを 1 つずつ（中のいちばん新しい更新時刻で）。ファイルも 1 つずつ。"""
    out: list[Item] = []
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if p.is_symlink():
            continue
        if p.is_dir():
            size, newest = _tree_size_and_newest(p)
            out.append(Item(str(p), newest, size, kind))
        elif p.is_file():
            out.append(Item(str(p), _mtime(p), p.stat().st_size, kind))
    return out


def scan_tree_files(d: Path, kind: str) -> list[Item]:
    """d の下の普通のファイルを全部（CLI の会話の写し・ログ）。"""
    out: list[Item] = []
    if not d.is_dir():
        return out
    for root, _dirs, files in os.walk(d):
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.lstat()
            except OSError:
                continue
            if fp.is_symlink() or not fp.is_file():
                continue
            out.append(Item(str(fp), datetime.fromtimestamp(st.st_mtime, UTC), st.st_size, kind))
    return out


# ---- 消す ---------------------------------------------------------------------------------------
def delete_items(items: list[Item], apply: bool) -> dict[str, dict[str, int]]:
    """消す（apply のときだけ）。戻り値: 種類ごとの {count, bytes, errors}。"""
    summary: dict[str, dict[str, int]] = {}
    for it in items:
        s = summary.setdefault(it.kind or "other", {"count": 0, "bytes": 0, "errors": 0})
        if apply:
            p = Path(it.path)
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError:
                s["errors"] += 1
                continue
        s["count"] += 1
        s["bytes"] += it.size
    return summary


def prune_empty_dirs(root: Path, apply: bool) -> int:
    """root の下の空のフォルダーを消す（root は残す）。"""
    n = 0
    if not root.is_dir():
        return 0
    for cur, dirs, files in os.walk(root, topdown=False):
        p = Path(cur)
        if p == root or p.is_symlink():
            continue
        try:
            if not any(p.iterdir()):
                if apply:
                    p.rmdir()
                n += 1
        except OSError:
            pass
    return n


#: これを下回ったら新しい会話の CLI を起こさない（作業中のものは続ける）。10 GB 未満は warning だけ。
MIN_FREE_START_GB = 5
DISK_BLOCK_JA = "いまサーバーの空きが少ないため、新しい相談を始められません。管理者に連絡してください（いまの相談は続けられます）。"


# ---- ディスクの見張り ----------------------------------------------------------------------------
def disk_status(path: str | Path = "/", min_free_gb: float = MIN_FREE_GB,
                usage: Callable[[str], object] = shutil.disk_usage) -> dict:
    """空きが min_free_gb 未満なら low=True と日本語の warning（中継の状態表示・早めの片づけに使う）。"""
    u = usage(str(path))
    free_gb = u.free / (1024 ** 3)
    low = free_gb < min_free_gb
    return {"path": str(path), "free_bytes": u.free, "total_bytes": u.total, "free_gb": round(free_gb, 2),
            "min_free_gb": min_free_gb, "low": low, "blocked": free_gb < MIN_FREE_START_GB,
            "warning": DISK_WARNING_JA.format(free=free_gb) if low else None}


# ---- 記録 ---------------------------------------------------------------------------------------
def _journal_append(rec: dict, path: str | None) -> bool:
    here = Path(__file__).resolve().parents[1]
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from rk import journal  # noqa: PLC0415  rk は標準ライブラリだけ
    return journal.append(rec, Path(path) if path else None)


def _report(scope: str, items: list[Item], summary: dict, apply: bool, out=None, as_json: bool = False) -> None:
    out = out or sys.stdout
    if as_json:
        out.write(json.dumps({"scope": scope, "dry_run": not apply, "deleted": summary,
                              "items": [{"path": i.path, "size": i.size, "kind": i.kind,
                                         "ts": i.ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": i.reason}
                                        for i in items]}, ensure_ascii=False) + "\n")
        return
    head = "DELETE" if apply else "WOULD DELETE (dry-run)"
    for i in sorted(items, key=lambda x: x.path):
        out.write(f"{head}\t{i.size:>12}\t{i.ts.strftime('%Y-%m-%dT%H:%MZ')}\t{i.kind}\t{i.path}\t{i.reason}\n")
    total_n = sum(s["count"] for s in summary.values())
    total_b = sum(s["bytes"] for s in summary.values())
    out.write(f"[{scope}] {'deleted' if apply else 'would delete'} {total_n} items, "
              f"{total_b / 1024 / 1024:.1f} MB {json.dumps(summary)}\n")


def _finish(scope: str, items: list[Item], apply: bool, args, extra_summary: dict | None = None) -> dict:
    summary = delete_items(items, apply)
    if extra_summary:
        summary.update(extra_summary)
    _report(scope, items, summary, apply, as_json=getattr(args, "json", False))
    if apply:
        _journal_append({"kind": "retention", "scope": scope, "dry_run": False, "deleted": summary,
                         "trigger": getattr(args, "trigger", None) or "scheduled"}, args.journal)
    return summary


# ---- 範囲ごとの片づけ ----------------------------------------------------------------------------
def run_app_backups(args, now: datetime) -> dict:
    items: list[Item] = []
    for d in args.dir:
        items += scan_files(Path(d), classify_app_backup)
    return _finish("app-backups", select_app_backups(items, now), args.apply, args)


def run_agent_backups(args, now: datetime) -> dict:
    sel: list[Item] = []
    for d in args.dir:  # 置き場所ごとに「新しい 5 個」を残す（残す側に倒す）
        sel += select_keep_newest(scan_files(Path(d), classify_agent_backup), now, AGENT_BACKUP_DAYS, AGENT_BACKUP_KEEP)
    return _finish("agent-backups", sel, args.apply, args)


SANDBOX_TRANSCRIPT_DIRS = (".claude/projects", ".codex/sessions", ".codex/log", ".codex/logs")


def run_sandbox(args, now: datetime) -> dict:
    home = Path(args.home)
    sel: list[Item] = []
    roots = []
    for rel in SANDBOX_TRANSCRIPT_DIRS:
        root = home / rel
        roots.append(root)
        sel += select_older_than(scan_tree_files(root, "transcript"), now, SCRATCH_DAYS)
    for d in args.scratch:
        sel += select_older_than(scan_folders(Path(d), "scratch"), now, SCRATCH_DAYS)
    summary = delete_items(sel, args.apply)
    emptied = sum(prune_empty_dirs(r, args.apply) for r in roots)
    if emptied:
        summary["empty-dirs"] = {"count": emptied, "bytes": 0, "errors": 0}
    _report("sandbox", sel, summary, args.apply, as_json=args.json)
    if args.apply and args.journal:
        _journal_append({"kind": "retention", "scope": "sandbox", "dry_run": False, "deleted": summary,
                         "trigger": args.trigger or "scheduled"}, args.journal)
    return summary


def prune_store(db_url: str, now: datetime, apply: bool) -> dict[str, dict[str, int]]:
    """会話の記録 DB（agent スキーマ）: events・tool_calls は 90 日、案も実行も無い会話は 90 日で会話ごと、
    案・承認・実行は 1 年、1 年使っていない会話は会話ごと。件数だけ（バイト数は分からないので 0）。"""
    import sqlalchemy as sa  # noqa: PLC0415

    here = Path(__file__).resolve().parents[1]
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from relay.config import sqlalchemy_url  # noqa: PLC0415
    from relay.db import ApprovalRow, EventRow, PlanRow, RunRow, SessionRow, ToolCallRow  # noqa: PLC0415

    eng = sa.create_engine(sqlalchemy_url(db_url))
    if eng.dialect.name == "sqlite":
        eng = eng.execution_options(schema_translate_map={"agent": None})
    c90 = _aware(now) - timedelta(days=CONVERSATION_DAYS)
    c365 = _aware(now) - timedelta(days=PLAN_RUN_DAYS)
    S, E, T, P, A, R = (SessionRow.__table__, EventRow.__table__, ToolCallRow.__table__, PlanRow.__table__,
                        ApprovalRow.__table__, RunRow.__table__)
    has_plan = sa.exists().where(P.c.session_id == S.c.id)
    has_run = sa.exists().where(R.c.session_id == S.c.id)
    steps = [
        ("events", E, E.c.created_at < c90),
        ("tool_calls", T, T.c.created_at < c90),
        ("runs", R, R.c.started_at < c365),
        ("approvals", A, A.c.created_at < c365),
        ("plans", P, P.c.created_at < c365),
    ]
    doomed_cond = sa.or_(S.c.last_active_at < c365, sa.and_(S.c.last_active_at < c90, ~has_plan, ~has_run))
    out: dict[str, dict[str, int]] = {}
    with eng.begin() as conn:
        for name, table, cond in steps:
            n = conn.execute(sa.select(sa.func.count()).select_from(table).where(cond)).scalar_one()
            if apply and n:
                conn.execute(table.delete().where(cond))
            out[f"db-{name}"] = {"count": int(n), "bytes": 0, "errors": 0}
        # 会話ごと消す: 子の行も明示して消す（SQLite の試験では外部キーの連鎖が効かないことがあるため）
        doomed = [r[0] for r in conn.execute(sa.select(S.c.id).where(doomed_cond))]
        if apply and doomed:
            for table in (E, T, A, R, P):
                conn.execute(table.delete().where(table.c.session_id.in_(doomed)))
            conn.execute(S.delete().where(S.c.id.in_(doomed)))
        out["db-sessions"] = {"count": len(doomed), "bytes": 0, "errors": 0}
    eng.dispose()
    return out


def run_relay(args, now: datetime) -> dict:
    data = Path(args.data_dir)
    sel = select_older_than(scan_run_records(data / "runs"), now, PLAN_RUN_DAYS)
    sel += select_older_than(scan_folders(data / "reports", "report"), now, SCRATCH_DAYS)
    sel += select_older_than(scan_folders(data / "sessions", "scratch"), now, SCRATCH_DAYS)
    db_summary: dict = {}
    if args.db_url:
        try:
            db_summary = prune_store(args.db_url, now, args.apply)
        except Exception as e:  # DB に届かなくてもファイルの片づけは続ける
            db_summary = {"db-error": {"count": 0, "bytes": 0, "errors": 1}}
            sys.stderr.write(f"[retention] db: {type(e).__name__}\n")
    for k, v in db_summary.items():
        sys.stdout.write(f"{'DELETE' if args.apply else 'WOULD DELETE (dry-run)'}\t{k}\t{v['count']} rows\n")
    return _finish("relay", sel, args.apply, args, extra_summary=db_summary)


def run_journal_append(args, now: datetime) -> dict:
    """箱の片づけの要約（--json の出力）を中継の journal に書く（ホストのスクリプトが使う）。"""
    rec = json.loads(sys.stdin.read() or "{}")
    if rec.get("dry_run", True):
        return {}
    ok = _journal_append({"kind": "retention", "scope": rec.get("scope"), "dry_run": False,
                          "deleted": rec.get("deleted") or {}, "trigger": args.trigger or "scheduled"}, args.journal)
    return {"written": {"count": int(ok), "bytes": 0, "errors": int(not ok)}}


def run_disk(args, now: datetime) -> dict:
    st = disk_status(args.path, args.min_free_gb)
    print(json.dumps(st, ensure_ascii=False))
    if st["low"] and args.journal:
        _journal_append({"kind": "disk-low", "free_gb": st["free_gb"], "trigger": args.trigger or "diskguard"},
                        args.journal)
    return st


def _relay_env_defaults() -> tuple[str, str]:
    data = os.environ.get("AGENT_RELAY_DATA_DIR") or "/data"
    db = os.environ.get("AGENT_RELAY_DB_URL") or os.environ.get("AGENT_RELAY_DSN") or ""
    return data, db


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="retention", description="らく助エージェントの保存期間の片づけ（既定は表示だけ）")
    ap.add_argument("--apply", action="store_true", help="本当に消す（無ければ表示だけ）")
    ap.add_argument("--journal", default=None, help="作業の記録（JSON Lines）のファイル")
    ap.add_argument("--now", default=None, help="試験用: 今の時刻（ISO・UTC）")
    ap.add_argument("--trigger", default=None, help="scheduled / low-disk（journal に書く）")
    ap.add_argument("--json", action="store_true", help="結果を JSON 1 行で")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("app-backups")
    p.add_argument("--dir", action="append", default=[])
    p = sub.add_parser("agent-backups")
    p.add_argument("--dir", action="append", default=[])
    p = sub.add_parser("sandbox")
    p.add_argument("--home", default=os.environ.get("HOME", "/home/agent"))
    p.add_argument("--scratch", action="append", default=None)
    data, db = _relay_env_defaults()
    p = sub.add_parser("relay")
    p.add_argument("--data-dir", default=data)
    p.add_argument("--db-url", default=db)
    sub.add_parser("journal-append")
    p = sub.add_parser("disk")
    p.add_argument("--path", default="/")
    p.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB)
    # 共通の旗はどこに書いてもよい（サブコマンドの後ろでも）
    for name, sp in sub.choices.items():
        sp.add_argument("--apply", action="store_true", default=argparse.SUPPRESS)
        sp.add_argument("--journal", default=argparse.SUPPRESS)
        sp.add_argument("--now", default=argparse.SUPPRESS)
        sp.add_argument("--trigger", default=argparse.SUPPRESS)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.cmd == "sandbox" and args.scratch is None:
        args.scratch = ["/data/sessions"]
    if args.cmd in ("app-backups", "agent-backups") and not args.dir:
        ap.error("--dir が要ります")
    now = _aware(datetime.fromisoformat(args.now.replace("Z", "+00:00"))) if args.now else datetime.now(UTC)
    fn = {"app-backups": run_app_backups, "agent-backups": run_agent_backups, "sandbox": run_sandbox,
          "relay": run_relay, "journal-append": run_journal_append, "disk": run_disk}[args.cmd]
    res = fn(args, now)
    if args.cmd == "disk":
        return 2 if res.get("low") else 0
    return 1 if any(v.get("errors") for v in res.values() if isinstance(v, dict)) else 0


if __name__ == "__main__":
    sys.exit(main())
