"""Import patient master from Sample 1 ('スケジュール手動.xlsx' / sheet '元データ').

Each row is upserted into ``patients`` keyed on ``code``. Fields touched:
code/name/kana/sex/status/insurance/address/lat/lng/area/required_staff_count/
sex_restriction/specified_type/continuous_request/ng_staff_ids/preferred_staff_ids.
``weekly_pattern`` JSONB is populated by ``import_weekly_pattern.py`` in the
orchestrated run -- this script only writes the master columns.

Usage:
    python scripts/import_patients.py /path/to/sample1.xlsx [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Bootstrap sys.path via shared helper.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    build_parser,
    cell,
    clean_str,
    iter_rows,
    normalize_sex,
    parse_bool,
    parse_float,
    parse_id_list,
    parse_int,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.staff import Staff  # noqa: E402

SHEET_NAME = "元データ"


def _norm_status(value: Any) -> str:
    s = clean_str(value)
    if s is None:
        return "active"
    if s in {"稼働", "active", "有効"}:
        return "active"
    if s in {"停止", "休止", "inactive", "無効"}:
        return "inactive"
    return "active"


def _norm_insurance(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    if "医療" in s:
        return "medical"
    if "介護" in s:
        return "care"
    return None


def _norm_specified_type(value: Any) -> str | None:
    s = clean_str(value)
    if s is None:
        return None
    if s in {"全員", "ALL", "all"}:
        return "ALL"
    if s in {"複数", "MULTI", "multi"}:
        return "MULTI"
    if s in {"単独", "SINGLE", "single"}:
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


def _norm_sex_restriction(value: Any) -> str | None:
    s = clean_str(value)
    if s is None or s == "選択なし":
        return None
    return normalize_sex(s)


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    code = clean_str(cell(row, idx, "patient_id"))
    name = clean_str(cell(row, idx, "患者名"))
    if not code or not name:
        return None

    payload: dict[str, Any] = {
        "code": code,
        "name": name,
        "kana": clean_str(cell(row, idx, "フリガナ")),
        "sex": normalize_sex(cell(row, idx, "性別")),
        "status": _norm_status(cell(row, idx, "稼働状況")),
        "insurance": _norm_insurance(cell(row, idx, "保険区分")),
        "address": clean_str(cell(row, idx, "住所")),
        "lat": parse_float(cell(row, idx, "緯度")),
        "lng": parse_float(cell(row, idx, "経度")),
        "area": clean_str(cell(row, idx, "エリア")),
        "required_staff_count": parse_int(cell(row, idx, "必要スタッフ数")) or 1,
        "sex_restriction": _norm_sex_restriction(cell(row, idx, "性別制限")),
        "specified_type": _norm_specified_type(cell(row, idx, "指定タイプ")),
        "continuous_request": _norm_continuous(cell(row, idx, "継続希望")),
        "note": clean_str(cell(row, idx, "備考")),
    }
    payload["_specified_codes"] = parse_id_list(cell(row, idx, "指定スタッフID"))
    payload["_ng_codes"] = parse_id_list(cell(row, idx, "NGスタッフID"))
    return payload


async def _resolve_staff_codes(session, codes: set[str]) -> dict[str, Any]:
    if not codes:
        return {}
    rows = await session.execute(
        select(Staff.code, Staff.id).where(Staff.code.in_(codes))
    )
    return {c: i for c, i in rows.all() if c}


async def import_patients(xlsx: Path, dry_run: bool) -> dict[str, int]:
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
        print(f"[patients] parsed={len(payloads)} from sheet '{SHEET_NAME}'")
        if payloads:
            sample = payloads[0]
            print(f"  sample: code={sample['code']} name={sample['name']} "
                  f"area={sample['area']} sex={sample['sex']}")
        print_summary("patients", **summary, errors=failures)
        return summary

    factory = get_session_factory()
    async with factory() as session:
        all_codes = set()
        for p in payloads:
            all_codes.update(p["_specified_codes"])
            all_codes.update(p["_ng_codes"])
        code_to_uuid = await _resolve_staff_codes(session, all_codes)

        existing = await session.execute(
            select(Patient).where(
                Patient.code.in_([p["code"] for p in payloads])
            )
        )
        by_code = {p.code: p for p in existing.scalars()}

        for payload in payloads:
            spec_codes = payload.pop("_specified_codes")
            ng_codes = payload.pop("_ng_codes")
            payload["preferred_staff_ids"] = [
                code_to_uuid[c] for c in spec_codes if c in code_to_uuid
            ]
            payload["ng_staff_ids"] = [
                code_to_uuid[c] for c in ng_codes if c in code_to_uuid
            ]

            obj = by_code.get(payload["code"])
            if obj is None:
                session.add(Patient(**payload))
                summary["created"] += 1
            else:
                for k, v in payload.items():
                    setattr(obj, k, v)
                summary["updated"] += 1

        await session.commit()

    print_summary("patients", **summary, errors=failures)
    return summary


async def _main(xlsx: Path, dry_run: bool) -> dict[str, int]:
    try:
        return await import_patients(xlsx, dry_run)
    finally:
        # Dispose inside the same loop that owns asyncpg connections to
        # avoid "RuntimeError: Event loop is closed" on shutdown.
        if not dry_run:
            await dispose_engine()


def main() -> int:
    args = build_parser("Import patient master (Sample 1 / 元データ)").parse_args()
    asyncio.run(_main(args.xlsx, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
