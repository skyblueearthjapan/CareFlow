"""Import per-week staff overrides from Sample 2 (sheet 'スタッフ個別変更リクエスト').

Each row → ``staff_weekly_overrides``. Excel ``staff_id`` is the staff master
``code`` (e.g. ``S001``); we resolve to the DB UUID. ``制限タイプ`` is mapped
to ``override_type``: ``休み → off``, ``時間変更 → custom_time``.

Uniqueness is (staff_id, iso_year, iso_week, weekday) -- existing rows for
the same key are updated in place.

Usage:
    python scripts/import_staff_overrides.py /path/to/sample2.xlsx [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    build_parser,
    cell,
    clean_str,
    iter_rows,
    parse_date,
    parse_time,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.staff import Staff, StaffWeeklyOverride  # noqa: E402

SHEET_NAME = "スタッフ個別変更リクエスト"

_OVERRIDE_TYPE_MAP = {
    "休み": "off",
    "off": "off",
    "OFF": "off",
    "時間変更": "custom_time",
    "custom_time": "custom_time",
}


def _norm_override_type(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    return _OVERRIDE_TYPE_MAP.get(s, None)


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    staff_code = clean_str(cell(row, idx, "staff_id"))
    visit_date = parse_date(cell(row, idx, "日付"))
    override_type = _norm_override_type(cell(row, idx, "制限タイプ"))
    if not staff_code or not visit_date or not override_type:
        return None
    iso = visit_date.isocalendar()
    return {
        "_staff_code": staff_code,
        "iso_year": iso.year,
        "iso_week": iso.week,
        "weekday": visit_date.weekday(),
        "override_type": override_type,
        "start_time": parse_time(cell(row, idx, "開始時刻")),
        "end_time": parse_time(cell(row, idx, "終了時刻")),
        "reason": clean_str(cell(row, idx, "理由")),
    }


async def import_staff_overrides(xlsx: Path, dry_run: bool) -> dict[str, int]:
    idx_map, rows = iter_rows(xlsx, SHEET_NAME)
    if not idx_map:
        raise RuntimeError(f"empty header row in sheet '{SHEET_NAME}'")

    payloads: list[dict[str, Any]] = []
    failures: list[str] = []
    for row_no, row in enumerate(rows, start=2):
        try:
            p = build_payload(row, idx_map)
            if p:
                payloads.append(p)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"row {row_no}: {exc}")

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": len(failures)}

    if dry_run:
        print(f"[staff_overrides] parsed={len(payloads)} from '{SHEET_NAME}'")
        print_summary("staff_overrides", **summary, errors=failures)
        return summary

    factory = get_session_factory()
    async with factory() as session:
        codes = {p["_staff_code"] for p in payloads}
        if codes:
            res = await session.execute(
                select(Staff.code, Staff.id).where(Staff.code.in_(codes))
            )
            code_to_uuid = {c: i for c, i in res.all() if c}
        else:
            code_to_uuid = {}

        for p in payloads:
            staff_uuid = code_to_uuid.get(p["_staff_code"])
            if staff_uuid is None:
                summary["skipped"] += 1
                continue
            existing = await session.scalar(
                select(StaffWeeklyOverride).where(
                    StaffWeeklyOverride.staff_id == staff_uuid,
                    StaffWeeklyOverride.iso_year == p["iso_year"],
                    StaffWeeklyOverride.iso_week == p["iso_week"],
                    StaffWeeklyOverride.weekday == p["weekday"],
                )
            )
            data = {
                "staff_id": staff_uuid,
                "iso_year": p["iso_year"],
                "iso_week": p["iso_week"],
                "weekday": p["weekday"],
                "override_type": p["override_type"],
                "start_time": p["start_time"],
                "end_time": p["end_time"],
                "reason": p["reason"],
            }
            if existing is None:
                session.add(StaffWeeklyOverride(**data))
                summary["created"] += 1
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
                summary["updated"] += 1

        await session.commit()

    print_summary("staff_overrides", **summary, errors=failures)
    return summary


def main() -> int:
    args = build_parser(
        "Import staff weekly overrides (Sample 2 / スタッフ個別変更リクエスト)"
    ).parse_args()
    try:
        asyncio.run(import_staff_overrides(args.xlsx, args.dry_run))
    finally:
        if not args.dry_run:
            asyncio.run(dispose_engine())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
