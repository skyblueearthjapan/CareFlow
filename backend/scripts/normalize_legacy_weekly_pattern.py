"""Normalise legacy ``patients.weekly_pattern`` JSONB values (Wave 4-G).

A historical inconsistency snuck into the production database before the
weekly-pattern editor enforced its Optional contract:

* ``frequency_per_week = 0`` — zero instead of NULL when the field is
  intentionally unset (Optional). Zero gets treated as "no visits this
  week" by downstream readers and silently suppresses the patient.

This script walks every (non-deleted) patient row, applies the fix in
memory, and either reports the deltas (default = ``--dry-run``) or commits
them when ``--apply`` is passed. Patients with ``frequency_per_week = NULL``
are left untouched — that's the intentional "no constraint" state.

Note (v2 移行): かつて ``weekday_priority = ""`` も同時に正規化していたが、
``weekday_priority`` は v2 スキーマで廃止されたため、このスクリプトでは
扱わない。残存キーは何もしない (本スクリプトの責務外)。

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


def _normalize_pattern(raw: Any) -> tuple[dict[str, Any] | None, bool]:
    """Return (new_pattern, frequency_changed).

    new_pattern is None when no changes were necessary. Frequency is only
    rewritten when it is exactly ``0`` — NULL stays NULL.
    """
    if not isinstance(raw, dict):
        return None, False
    new = deepcopy(raw)
    frequency_changed = False

    if "frequency_per_week" in new and new["frequency_per_week"] == 0:
        # Distinguish from None (untouched) — only rewrite the literal 0.
        new["frequency_per_week"] = None
        frequency_changed = True

    if not frequency_changed:
        return None, False
    return new, frequency_changed


async def normalize(*, apply: bool) -> dict[str, int]:
    factory = get_session_factory()
    summary = {
        "scanned": 0,
        "frequency_fixed": 0,
        "rows_changed": 0,
    }
    async with factory() as session:
        rows = (await session.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
        for patient in rows:
            summary["scanned"] += 1
            new_pattern, freq = _normalize_pattern(patient.weekly_pattern)
            if new_pattern is None:
                continue
            summary["rows_changed"] += 1
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
        description="Normalise legacy weekly_pattern JSONB values (frequency_per_week=0 → NULL)"
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
