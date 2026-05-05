"""Import staff events from Sample 2 (sheet 'イベントリクエスト').

Each row → ``staff_events``. Excel ``staff_id`` → ``staff.code`` → DB UUID.
Time handling:
  時間指定方法 == '固定時間' → uses 開始時刻 + 終了時刻 from sheet
  時間指定方法 == '所要時間' → uses 開始時刻 + 所要時間(分); if start blank,
                              defaults to 09:00 of 日付.

Existing rows are matched on (staff_id, starts_at, event_type, title);
duplicates are updated in place rather than re-inserted.

Usage:
    python scripts/import_staff_events.py /path/to/sample2.xlsx [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timedelta, timezone
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
    parse_int,
    parse_time,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.staff import Staff, StaffEvent  # noqa: E402

SHEET_NAME = "イベントリクエスト"

_EVENT_TYPE_MAP = {
    "会議": "meeting",
    "役所同行": "errand",
    "初回面談": "interview",
    "打合せ": "meeting",
    "研修": "training",
    "事務": "office",
}

_DEFAULT_START = time(9, 0)
_DEFAULT_DURATION_MIN = 60


def _norm_event_type(value: Any) -> str:
    s = clean_str(value)
    if s is None:
        return "other"
    return _EVENT_TYPE_MAP.get(s, s[:16])


def _combine(d, t: time | None) -> datetime:
    base = t or _DEFAULT_START
    return datetime.combine(d, base, tzinfo=timezone.utc)


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    staff_code = clean_str(cell(row, idx, "staff_id"))
    visit_date = parse_date(cell(row, idx, "日付"))
    if not staff_code or not visit_date:
        return None

    start_time_v = parse_time(cell(row, idx, "開始時刻"))
    end_time_v = parse_time(cell(row, idx, " 終了時刻 ")) or parse_time(
        cell(row, idx, "終了時刻")
    )
    duration = parse_int(cell(row, idx, "所要時間(分)"))

    starts_at = _combine(visit_date, start_time_v)
    if end_time_v is not None:
        ends_at = _combine(visit_date, end_time_v)
        if ends_at <= starts_at:
            ends_at = starts_at + timedelta(minutes=duration or _DEFAULT_DURATION_MIN)
    else:
        ends_at = starts_at + timedelta(minutes=duration or _DEFAULT_DURATION_MIN)

    title = (
        clean_str(cell(row, idx, "タイトル "))
        or clean_str(cell(row, idx, "タイトル"))
    )

    return {
        "_staff_code": staff_code,
        "event_type": _norm_event_type(cell(row, idx, "イベント種別")),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "title": title,
        "note": clean_str(cell(row, idx, "備考"))
                or clean_str(cell(row, idx, "理由")),
    }


async def import_staff_events(xlsx: Path, dry_run: bool) -> dict[str, int]:
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
        print(f"[staff_events] parsed={len(payloads)} from '{SHEET_NAME}'")
        if payloads:
            s = payloads[0]
            print(f"  sample: code={s['_staff_code']} type={s['event_type']} "
                  f"starts={s['starts_at']} ends={s['ends_at']}")
        print_summary("staff_events", **summary, errors=failures)
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
                select(StaffEvent).where(
                    StaffEvent.staff_id == staff_uuid,
                    StaffEvent.starts_at == p["starts_at"],
                    StaffEvent.event_type == p["event_type"],
                )
            )
            data = {
                "staff_id": staff_uuid,
                "event_type": p["event_type"],
                "starts_at": p["starts_at"],
                "ends_at": p["ends_at"],
                "title": p["title"],
                "note": p["note"],
            }
            if existing is None:
                session.add(StaffEvent(**data))
                summary["created"] += 1
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
                summary["updated"] += 1

        await session.commit()

    print_summary("staff_events", **summary, errors=failures)
    return summary


def main() -> int:
    args = build_parser(
        "Import staff events (Sample 2 / イベントリクエスト)"
    ).parse_args()
    try:
        asyncio.run(import_staff_events(args.xlsx, args.dry_run))
    finally:
        if not args.dry_run:
            asyncio.run(dispose_engine())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
