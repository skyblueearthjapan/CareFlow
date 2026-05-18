"""Backfill ``patients.weekly_pattern`` (JSONB) from Sample 1 ('元データ').

Run **after** ``import_patients.py`` so that ``patients.code → id`` is already
in the DB. Reads weekly pattern columns and bundles them into the JSONB shape
used by the WeeklyPatternEditor frontend (W3-C):

```
{
  "frequency_per_week": int (1-7),
  "preferred_weekdays": ["Mon","Wed","Fri"],
  "service_minutes": int,
  "time_type": "固定|午前|午後|終日|時間帯",
  "preferred_start": "HH:MM" or null,
  "preferred_end": "HH:MM" or null,
  "ng_weekdays": ["Sat","Sun"]
}
```

Also re-applies a few master columns that depend on cross-resolved staff IDs
(area / specified_type / continuous_request / preferred_staff_ids /
ng_staff_ids) so this script is safe to run on its own.

Usage:
    python scripts/import_weekly_pattern.py /path/to/sample1.xlsx [--dry-run]
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
    normalize_frequency,
    parse_bool,
    parse_id_list,
    parse_int,
    parse_time,
    parse_weekdays,
    print_summary,
)
from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.staff import Staff  # noqa: E402

SHEET_NAME = "元データ"
_VALID_TIME_TYPES = {"固定", "午前", "午後", "終日", "時間帯"}


def _hhmm(t: Any) -> str | None:
    parsed = parse_time(t)
    if parsed is None:
        return None
    return parsed.strftime("%H:%M")


def _norm_time_type(value: Any) -> str:
    s = clean_str(value)
    if s in _VALID_TIME_TYPES:
        return s  # type: ignore[return-value]
    return "時間帯"


def _norm_specified_type(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    upper = s.upper()
    if upper in {"ALL", "MULTI", "SINGLE"}:
        return upper
    if s == "全員":
        return "ALL"
    if s == "複数":
        return "MULTI"
    if s == "単独":
        return "SINGLE"
    return None


def _norm_continuous(value: Any) -> bool:
    s = clean_str(value)
    if s is None:
        return False
    if s in {"継続", "希望", "あり"}:
        return True
    if s in {"ローテーション", "なし", "どちらでもよい"}:
        return False
    return parse_bool(value, default=False)


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    code = clean_str(cell(row, idx, "patient_id"))
    if not code:
        return None

    pattern: dict[str, Any] = {
        # Wave 4-G: 0 / negative / blank → None (Optional contract).
        "frequency_per_week": normalize_frequency(cell(row, idx, "週訪問回数")),
        "preferred_weekdays": parse_weekdays(cell(row, idx, "希望曜日（複数可）")),
        "service_minutes": parse_int(cell(row, idx, "サービス時間")),
        "time_type": _norm_time_type(cell(row, idx, "時間タイプ")),
        "preferred_start": _hhmm(cell(row, idx, "希望時間帯（開始）")),
        "preferred_end": _hhmm(cell(row, idx, "希望時間帯（終了）")),
        "ng_weekdays": parse_weekdays(cell(row, idx, "曜日NG")),
    }

    return {
        "code": code,
        "weekly_pattern": pattern,
        "area": clean_str(cell(row, idx, "エリア")),
        "specified_type": _norm_specified_type(cell(row, idx, "指定タイプ")),
        "continuous_request": _norm_continuous(cell(row, idx, "継続希望")),
        "_specified_codes": parse_id_list(cell(row, idx, "指定スタッフID")),
        "_ng_codes": parse_id_list(cell(row, idx, "NGスタッフID")),
    }


async def import_weekly_pattern(xlsx: Path, dry_run: bool) -> dict[str, int]:
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
        print(f"[weekly_pattern] parsed={len(payloads)} from '{SHEET_NAME}'")
        if payloads:
            sample = payloads[0]
            print(f"  sample: code={sample['code']} pattern={sample['weekly_pattern']}")
        print_summary("weekly_pattern", **summary, errors=failures)
        return summary

    factory = get_session_factory()
    async with factory() as session:
        all_codes = set()
        for p in payloads:
            all_codes.update(p["_specified_codes"])
            all_codes.update(p["_ng_codes"])
        code_to_uuid: dict[str, Any] = {}
        if all_codes:
            res = await session.execute(
                select(Staff.code, Staff.id).where(Staff.code.in_(all_codes))
            )
            code_to_uuid = {c: i for c, i in res.all() if c}

        existing = await session.execute(
            select(Patient).where(Patient.code.in_([p["code"] for p in payloads]))
        )
        by_code = {p.code: p for p in existing.scalars()}

        for payload in payloads:
            spec_codes = payload.pop("_specified_codes")
            ng_codes = payload.pop("_ng_codes")
            obj = by_code.get(payload["code"])
            if obj is None:
                summary["skipped"] += 1
                continue
            obj.weekly_pattern = payload["weekly_pattern"]
            obj.area = payload["area"] or obj.area
            if payload["specified_type"] is not None:
                obj.specified_type = payload["specified_type"]
            obj.continuous_request = payload["continuous_request"]
            obj.preferred_staff_ids = [code_to_uuid[c] for c in spec_codes if c in code_to_uuid]
            obj.ng_staff_ids = [code_to_uuid[c] for c in ng_codes if c in code_to_uuid]
            summary["updated"] += 1

        await session.commit()

    print_summary("weekly_pattern", **summary, errors=failures)
    return summary


async def _main(xlsx: Path, dry_run: bool) -> dict[str, int]:
    try:
        return await import_weekly_pattern(xlsx, dry_run)
    finally:
        # Dispose inside the same loop that owns asyncpg connections to
        # avoid "RuntimeError: Event loop is closed" on shutdown.
        if not dry_run:
            await dispose_engine()


def main() -> int:
    args = build_parser("Import weekly_pattern JSONB (Sample 1 / 元データ)").parse_args()
    asyncio.run(_main(args.xlsx, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
