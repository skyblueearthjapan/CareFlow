"""Import special-week headers + items from Sample 2.

Sheets:
  '特別訪問週間_ヘッダ' (parent: SpecialWeek)
  '特別訪問週間_明細'   (child : SpecialWeekItem)

Linkage is via the Excel-side ``special_week_id`` (a string like
``SW_<uuid>``) shared between the two sheets, which we hold in a temporary
``_external_id`` map; the actual DB UUID is freshly assigned by SQLAlchemy.

If a (patient_id, week_start) pair already exists in ``special_weeks`` it is
treated as an update target -- existing items are wiped and re-inserted from
the Excel detail rows.

Usage:
    python scripts/import_special_weeks.py /path/to/sample2.xlsx [--dry-run]
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
    parse_datetime,
    parse_int,
    parse_time,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.special_week import SpecialWeek, SpecialWeekItem  # noqa: E402

HEADER_SHEET = "特別訪問週間_ヘッダ"
ITEM_SHEET = "特別訪問週間_明細"

_STATUS_MAP = {
    "有効": "applied",
    "適用済": "applied",
    "applied": "applied",
    "下書き": "draft",
    "draft": "draft",
    "無効": "cancelled",
    "取消": "cancelled",
    "cancelled": "cancelled",
}

_TIME_TYPES = {"固定", "午前", "午後", "終日", "時間帯"}


def _norm_status(value: Any) -> str:
    s = clean_str(value)
    if s is None:
        return "draft"
    return _STATUS_MAP.get(s, "draft")


def _norm_apply_mode(value: Any) -> str:
    s = (clean_str(value) or "ADD").upper()
    return s if s in {"ADD", "REPLACE"} else "ADD"


def _norm_time_type(value: Any) -> str:
    s = clean_str(value)
    return s if s in _TIME_TYPES else "時間帯"


def build_header(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    ext_id = clean_str(cell(row, idx, "special_week_id"))
    pcode = clean_str(cell(row, idx, "patient_id"))
    week_start = parse_date(cell(row, idx, "週開始日"))
    week_end = parse_date(cell(row, idx, "週終了日"))
    if not ext_id or not pcode or not week_start or not week_end:
        return None
    return {
        "_external_id": ext_id,
        "_patient_code": pcode,
        "week_start": week_start,
        "week_end": week_end,
        "apply_mode": _norm_apply_mode(cell(row, idx, "適用モード(ADD/REPLACE)")),
        "reason": clean_str(cell(row, idx, "理由")),
        "status": _norm_status(cell(row, idx, "状態")),
        "registered_at": parse_datetime(cell(row, idx, "登録日時")),
    }


def build_item(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    ext_id = clean_str(cell(row, idx, "special_week_id"))
    pcode = clean_str(cell(row, idx, "patient_id"))
    visit_date = parse_date(cell(row, idx, "日付"))
    if not ext_id or not pcode or not visit_date:
        return None
    return {
        "_external_id": ext_id,
        "_patient_code": pcode,
        "visit_date": visit_date,
        "weekday": visit_date.weekday(),
        "row_label": clean_str(cell(row, idx, "行ラベル")),
        "time_type": _norm_time_type(cell(row, idx, "時間タイプ")),
        "start_time": parse_time(cell(row, idx, "開始時刻")),
        "end_time": parse_time(cell(row, idx, "終了時刻")),
        "preferred_earliest": parse_time(cell(row, idx, "希望最早")),
        "preferred_latest": parse_time(cell(row, idx, "希望最遅")),
        "service_minutes": parse_int(cell(row, idx, "サービス時間")) or 30,
        "required_staff_count": parse_int(cell(row, idx, "必要スタッフ数")) or 1,
        "individual_change_handling": clean_str(cell(row, idx, "個別変更の扱い")),
        "note": clean_str(cell(row, idx, "備考")),
    }


async def import_special_weeks(xlsx: Path, dry_run: bool) -> dict[str, int]:
    h_idx, h_rows = iter_rows(xlsx, HEADER_SHEET)
    i_idx, i_rows = iter_rows(xlsx, ITEM_SHEET)

    header_payloads: list[dict[str, Any]] = []
    item_payloads: list[dict[str, Any]] = []
    failures: list[str] = []

    for row_no, row in enumerate(h_rows, start=2):
        try:
            p = build_header(row, h_idx)
            if p:
                header_payloads.append(p)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"header row {row_no}: {exc}")

    for row_no, row in enumerate(i_rows, start=2):
        try:
            p = build_item(row, i_idx)
            if p:
                item_payloads.append(p)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"item row {row_no}: {exc}")

    summary = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": len(failures),
    }

    if dry_run:
        print(f"[special_weeks] headers parsed={len(header_payloads)} "
              f"items parsed={len(item_payloads)}")
        print_summary("special_weeks", **summary, errors=failures)
        return summary

    factory = get_session_factory()
    async with factory() as session:
        # Resolve patient codes
        codes = {p["_patient_code"] for p in header_payloads}
        codes.update(p["_patient_code"] for p in item_payloads)
        if codes:
            res = await session.execute(
                select(Patient.code, Patient.id).where(Patient.code.in_(codes))
            )
            code_to_uuid = {c: i for c, i in res.all()}
        else:
            code_to_uuid = {}

        ext_to_dbid: dict[str, Any] = {}

        for h in header_payloads:
            patient_uuid = code_to_uuid.get(h["_patient_code"])
            if patient_uuid is None:
                summary["skipped"] += 1
                continue

            existing = await session.scalar(
                select(SpecialWeek).where(
                    SpecialWeek.patient_id == patient_uuid,
                    SpecialWeek.week_start == h["week_start"],
                )
            )
            data = {
                "patient_id": patient_uuid,
                "week_start": h["week_start"],
                "week_end": h["week_end"],
                "apply_mode": h["apply_mode"],
                "reason": h["reason"],
                "status": h["status"],
            }
            if h["registered_at"]:
                data["registered_at"] = h["registered_at"]

            if existing is None:
                obj = SpecialWeek(**data)
                session.add(obj)
                summary["created"] += 1
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
                obj = existing
                summary["updated"] += 1
                # Wipe old items so we can re-seed cleanly.
                await session.execute(
                    SpecialWeekItem.__table__.delete().where(
                        SpecialWeekItem.special_week_id == existing.id
                    )
                )
            await session.flush()
            ext_to_dbid[h["_external_id"]] = (obj.id, patient_uuid)

        for it in item_payloads:
            mapped = ext_to_dbid.get(it["_external_id"])
            if mapped is None:
                summary["skipped"] += 1
                continue
            sw_id, patient_uuid = mapped
            session.add(SpecialWeekItem(
                special_week_id=sw_id,
                patient_id=patient_uuid,
                visit_date=it["visit_date"],
                weekday=it["weekday"],
                row_label=it["row_label"],
                time_type=it["time_type"],
                start_time=it["start_time"],
                end_time=it["end_time"],
                preferred_earliest=it["preferred_earliest"],
                preferred_latest=it["preferred_latest"],
                service_minutes=it["service_minutes"],
                required_staff_count=it["required_staff_count"],
                individual_change_handling=it["individual_change_handling"],
                note=it["note"],
            ))

        await session.commit()

    print_summary("special_weeks", **summary, errors=failures)
    return summary


def main() -> int:
    args = build_parser(
        "Import special weeks (Sample 2 / 特別訪問週間_ヘッダ + 明細)"
    ).parse_args()
    try:
        asyncio.run(import_special_weeks(args.xlsx, args.dry_run))
    finally:
        if not args.dry_run:
            asyncio.run(dispose_engine())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
