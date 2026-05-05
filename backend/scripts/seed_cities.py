"""Seed Japanese cities (市区町村) into the cities table — Phase 3-13.

Reads ``backend/data/cities_jp.json`` and performs an INSERT .. ON CONFLICT
DO NOTHING upsert on ``cities.jis_code``. Existing rows are skipped.

Usage:
    python scripts/seed_cities.py            # apply seed
    python scripts/seed_cities.py --dry-run  # only print counts

TODO(Phase 5): replace static JSON with the 総務省 全国地方公共団体コード
API (CSV/JSON) for the full ~1,700-row coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Ensure backend/ is importable when invoked as `python scripts/seed_cities.py`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.city import City  # noqa: E402

DATA_FILE = _BACKEND_ROOT / "data" / "cities_jp.json"


def load_seed_rows() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"seed file not found: {DATA_FILE}")
    with DATA_FILE.open("r", encoding="utf-8") as fp:
        rows = json.load(fp)
    if not isinstance(rows, list):
        raise ValueError("cities_jp.json must be a JSON array")
    return rows


def normalize_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map JSON shape (`code`) onto the City model column (`jis_code`)."""
    out: list[dict[str, Any]] = []
    for row in raw:
        prefecture = (row.get("prefecture") or "").strip()
        name = (row.get("name") or "").strip()
        jis_code = (row.get("code") or row.get("jis_code") or "").strip() or None
        if not prefecture or not name:
            continue
        out.append({"prefecture": prefecture, "name": name, "jis_code": jis_code})
    return out


async def seed(rows: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    """Insert rows. Returns (inserted, skipped)."""
    factory = get_session_factory()
    async with factory() as session:
        existing = await session.scalars(select(City.jis_code))
        existing_codes = {code for code in existing.all() if code}

        to_insert = [r for r in rows if r["jis_code"] and r["jis_code"] not in existing_codes]
        skipped = len(rows) - len(to_insert)

        if dry_run:
            return len(to_insert), skipped

        if to_insert:
            stmt = pg_insert(City).values(to_insert).on_conflict_do_nothing(
                index_elements=[City.jis_code]
            )
            await session.execute(stmt)
            await session.commit()

        return len(to_insert), skipped


async def _main(dry_run: bool) -> int:
    raw = load_seed_rows()
    rows = normalize_rows(raw)
    print(f"loaded: {len(rows)} rows from {DATA_FILE.name}")
    try:
        inserted, skipped = await seed(rows, dry_run)
    finally:
        await dispose_engine()
    label = "would insert" if dry_run else "inserted"
    print(f"{label}: {inserted} rows, skipped: {skipped} rows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed cities from cities_jp.json")
    parser.add_argument("--dry-run", action="store_true", help="print counts only")
    args = parser.parse_args()
    return asyncio.run(_main(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
