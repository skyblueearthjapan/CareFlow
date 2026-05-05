"""Normalise legacy ``patients.weekly_pattern`` JSONB values (Wave 4-G).

Two historical inconsistencies snuck into the production database before the
weekly-pattern editor enforced its enum/Optional contract:

1. ``weekday_priority = ""`` — empty string instead of the proper "低"
   default. The allocation engine maps this to "低" defensively, but the
   editor UI shows it as "(unset)" which confuses operators.
2. ``frequency_per_week = 0`` — zero instead of NULL when the field is
   intentionally unset (Optional). Zero gets treated as "no visits this
   week" by downstream readers and silently suppresses the patient.

This script walks every (non-deleted) patient row, applies both fixes in
memory, and either reports the deltas (default = ``--dry-run``) or commits
them when ``--apply`` is passed. Patients with ``frequency_per_week = NULL``
are left untouched — that's the intentional "no constraint" state.

Usage::

    docker exec carelink-backend python scripts/normalize_legacy_weekly_pattern.py
    docker exec carelink-backend python scripts/normalize_legacy_weekly_pattern.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure backend/ on sys.path so `app.*` imports resolve when the script is
# invoked via `python scripts/...`.
_BACKEND_ROOT = _SCRIPTS_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402


_PRIORITY_DEFAULT = "低"


def _normalize_pattern(raw: Any) -> tuple[dict[str, Any] | None, bool, bool]:
    """Return (new_pattern, priority_changed, frequency_changed).

    new_pattern is None when no changes were necessary. Frequency is only
    rewritten when it is exactly ``0`` — NULL stays NULL.
    """
    if not isinstance(raw, dict):
        return None, False, False
    new = deepcopy(raw)
    priority_changed = False
    frequency_changed = False

    priority = new.get("weekday_priority")
    if isinstance(priority, str) and priority.strip() == "":
        new["weekday_priority"] = _PRIORITY_DEFAULT
        priority_changed = True

    if "frequency_per_week" in new and new["frequency_per_week"] == 0:
        # Distinguish from None (untouched) — only rewrite the literal 0.
        new["frequency_per_week"] = None
        frequency_changed = True

    if not (priority_changed or frequency_changed):
        return None, False, False
    return new, priority_changed, frequency_changed


async def normalize(*, apply: bool) -> dict[str, int]:
    factory = get_session_factory()
    summary = {
        "scanned": 0,
        "priority_fixed": 0,
        "frequency_fixed": 0,
        "rows_changed": 0,
    }
    async with factory() as session:
        rows = (
            await session.scalars(
                select(Patient).where(Patient.deleted_at.is_(None))
            )
        ).all()
        for patient in rows:
            summary["scanned"] += 1
            new_pattern, prio, freq = _normalize_pattern(patient.weekly_pattern)
            if new_pattern is None:
                continue
            summary["rows_changed"] += 1
            if prio:
                summary["priority_fixed"] += 1
            if freq:
                summary["frequency_fixed"] += 1
            if apply:
                patient.weekly_pattern = new_pattern
        if apply:
            await session.commit()

    mode = "APPLIED" if apply else "DRY-RUN"
    print(
        f"[normalize_weekly_pattern:{mode}] scanned={summary['scanned']} "
        f"rows_changed={summary['rows_changed']} "
        f"priority_fixed={summary['priority_fixed']} "
        f"frequency_fixed={summary['frequency_fixed']}"
    )
    if not apply and summary["rows_changed"]:
        print("  (re-run with --apply to commit)")
    return summary


async def _main(apply: bool) -> dict[str, int]:
    try:
        return await normalize(apply=apply)
    finally:
        if apply:
            await dispose_engine()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Normalise legacy weekly_pattern JSONB values "
        "(weekday_priority='' → '低', frequency_per_week=0 → NULL)"
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="report counts without writing (default)",
    )
    grp.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        default=False,
        help="commit the normalisation to the DB",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    asyncio.run(_main(apply=args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
