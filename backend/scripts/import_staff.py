"""Import staff master from Sample 2 (sheet 'スタッフマスタ').

Upserts ``staff`` keyed on ``code`` (Excel ``staff_id``). Also seeds the 7-row
``staff_shifts`` block with ``勤務曜日`` controlling on/off and the global
``シフト開始``/``シフト終了`` filling all on-days. ``primary_office_id`` is left
NULL: dedicated office mapping is out of scope for W3-B.

Usage:
    python scripts/import_staff.py /path/to/sample2.xlsx [--dry-run]
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
    normalize_sex,
    parse_areas,
    parse_float,
    parse_int,
    parse_time,
    parse_weekdays,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.staff import Staff, StaffShift  # noqa: E402

SHEET_NAME = "スタッフマスタ"
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _norm_skill(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    return s  # ベテラン/中堅/新人 stored verbatim


def _norm_volume(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    return s  # 多め/均等/少なめ verbatim


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    code = clean_str(cell(row, idx, "staff_id"))
    name = clean_str(cell(row, idx, "スタッフ名"))
    if not code or not name:
        return None

    work_days = parse_weekdays(cell(row, idx, "勤務曜日"))
    payload: dict[str, Any] = {
        "code": code,
        "name": name,
        "sex": normalize_sex(cell(row, idx, "性別")),
        "home_address": clean_str(cell(row, idx, "拠点住所")),
        "home_lat": parse_float(cell(row, idx, "緯度")),
        "home_lng": parse_float(cell(row, idx, "経度")),
        "areas": parse_areas(cell(row, idx, "得意エリア")),
        "max_per_day": parse_int(cell(row, idx, "最大訪問件数/日")) or 6,
        "skill_level": _norm_skill(cell(row, idx, "スキル")),
        "assignment_volume": _norm_volume(cell(row, idx, "割当量")),
        "note": clean_str(cell(row, idx, "備考")),
    }
    payload["_shift_start"] = parse_time(cell(row, idx, "シフト開始"))
    payload["_shift_end"] = parse_time(cell(row, idx, "シフト終了"))
    payload["_work_days"] = work_days
    return payload


async def import_staff(xlsx: Path, dry_run: bool) -> dict[str, int]:
    idx_map, rows = iter_rows(xlsx, SHEET_NAME)
    if not idx_map:
        raise RuntimeError(f"empty header row in sheet '{SHEET_NAME}'")

    payloads: list[dict[str, Any]] = []
    failures: list[str] = []
    for row_no, row in enumerate(rows, start=2):
        try:
            payload = build_payload(row, idx_map)
            if payload is None:
                continue
            payloads.append(payload)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"row {row_no}: {exc}")

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": len(failures)}

    if dry_run:
        print(f"[staff] parsed={len(payloads)} from sheet '{SHEET_NAME}'")
        if payloads:
            s = payloads[0]
            print(f"  sample: code={s['code']} name={s['name']} "
                  f"areas={s['areas']} skill={s['skill_level']}")
        print_summary("staff", **summary, errors=failures)
        return summary

    factory = get_session_factory()
    async with factory() as session:
        existing = await session.execute(
            select(Staff).where(Staff.code.in_([p["code"] for p in payloads]))
        )
        by_code = {s.code: s for s in existing.scalars()}

        for payload in payloads:
            shift_start = payload.pop("_shift_start")
            shift_end = payload.pop("_shift_end")
            work_days = payload.pop("_work_days")
            obj = by_code.get(payload["code"])

            if obj is None:
                obj = Staff(**payload)
                session.add(obj)
                summary["created"] += 1
            else:
                for k, v in payload.items():
                    setattr(obj, k, v)
                summary["updated"] += 1
            await session.flush()

            # Replace shifts wholesale.
            await session.execute(
                StaffShift.__table__.delete().where(StaffShift.staff_id == obj.id)
            )
            for wd_idx, wd_name in enumerate(_WEEKDAYS):
                is_on = wd_name in work_days
                session.add(StaffShift(
                    staff_id=obj.id,
                    weekday=wd_idx,
                    is_on=is_on,
                    start_time=shift_start if is_on else None,
                    end_time=shift_end if is_on else None,
                ))

        await session.commit()

    print_summary("staff", **summary, errors=failures)
    return summary


async def _main(xlsx: Path, dry_run: bool) -> dict[str, int]:
    try:
        return await import_staff(xlsx, dry_run)
    finally:
        # Dispose inside the same loop that owns asyncpg connections to
        # avoid "RuntimeError: Event loop is closed" on shutdown.
        if not dry_run:
            await dispose_engine()


def main() -> int:
    args = build_parser("Import staff master (Sample 2 / スタッフマスタ)").parse_args()
    asyncio.run(_main(args.xlsx, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
