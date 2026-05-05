"""Orchestrate the W3-B initial Excel import (7 stages).

Runs every importer in the canonical order, sharing a single async event-loop
and SQLAlchemy engine. Failures inside a stage are logged but do **not** abort
the run -- this is intentional ("continue mode") so the worst case is a
partial seed rather than a half-rolled-back database.

Usage:
    python scripts/run_initial_import.py /path/to/sample1.xlsx \\
        /path/to/sample2.xlsx [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.session import dispose_engine  # noqa: E402

from import_patients import import_patients  # noqa: E402
from import_special_weeks import import_special_weeks  # noqa: E402
from import_staff import import_staff  # noqa: E402
from import_staff_events import import_staff_events  # noqa: E402
from import_staff_overrides import import_staff_overrides  # noqa: E402
from import_users import import_users  # noqa: E402
from import_weekly_pattern import import_weekly_pattern  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run all initial Excel import stages in order"
    )
    p.add_argument("sample1", type=Path, help="path to Sample 1 (スケジュール手動.xlsx)")
    p.add_argument(
        "sample2",
        type=Path,
        help="path to Sample 2 (訪問看護：よりより様 ... 最適化 Ver.1.xlsx)",
    )
    p.add_argument("--dry-run", action="store_true", help="parse-only, no DB writes")
    return p.parse_args()


async def _run_stage(name: str, coro) -> dict[str, int]:
    print(f"\n===== stage: {name} =====")
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] FATAL: {exc}")
        traceback.print_exc()
        return {"created": 0, "updated": 0, "skipped": 0, "failed": 1}


async def _main(sample1: Path, sample2: Path, dry_run: bool) -> int:
    try:
        stages: list[tuple[str, dict[str, int]]] = []

        stages.append(("patients", await _run_stage(
            "patients", import_patients(sample1, dry_run))))
        stages.append(("staff", await _run_stage(
            "staff", import_staff(sample2, dry_run))))
        stages.append(("users", await _run_stage(
            "users", import_users(sample2, dry_run))))
        stages.append(("weekly_pattern", await _run_stage(
            "weekly_pattern", import_weekly_pattern(sample1, dry_run))))
        stages.append(("special_weeks", await _run_stage(
            "special_weeks", import_special_weeks(sample2, dry_run))))
        stages.append(("staff_events", await _run_stage(
            "staff_events", import_staff_events(sample2, dry_run))))
        stages.append(("staff_overrides", await _run_stage(
            "staff_overrides", import_staff_overrides(sample2, dry_run))))

        print("\n===== summary =====")
        print(f"{'stage':<18} {'created':>8} {'updated':>8} {'skipped':>8} {'failed':>8}")
        total = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        for name, s in stages:
            print(f"{name:<18} {s['created']:>8} {s['updated']:>8} "
                  f"{s['skipped']:>8} {s['failed']:>8}")
            for k in total:
                total[k] += s.get(k, 0)
        print(f"{'TOTAL':<18} {total['created']:>8} {total['updated']:>8} "
              f"{total['skipped']:>8} {total['failed']:>8}")
        return 0 if total["failed"] == 0 else 1
    finally:
        # Dispose inside the same event loop that created asyncpg connections,
        # otherwise asyncpg's finalizers fire against a closed loop and emit
        # "RuntimeError: Event loop is closed" during interpreter shutdown.
        if not dry_run:
            await dispose_engine()


def main() -> int:
    args = parse_args()
    if not args.sample1.exists():
        print(f"sample1 not found: {args.sample1}", file=sys.stderr)
        return 2
    if not args.sample2.exists():
        print(f"sample2 not found: {args.sample2}", file=sys.stderr)
        return 2
    return asyncio.run(_main(args.sample1, args.sample2, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
