"""スタッフ週間シフトをスプレッドシートから DB に直接投入.

通常の Excel インポート機能 (``import_staff.py`` / ``import_patients_staff_from_sheet.py``)
を経由せず、staff_shifts のみを純粋に再構築する裏側用スクリプト。

自動算出 (Phase 1+2) でスタッフの週間シフトが必須になったが ``staff_shifts``
が空 (0 件) のため、最新のスタッフマスタシートから一括投入する目的で追加。

仕様:
  - 全 active スタッフ × 7 曜日 = 7 rows / staff を生成
  - 月〜金 (weekday 0..4): is_on=true, 09:30-18:00 (固定)
  - 土 (weekday 5): スプレッドシート「勤務曜日」列に Sat があれば
                    is_on=true, 09:30-18:00。なければ is_on=false, NULL
  - 日 (weekday 6): is_on=false, NULL (常に休み)

冪等性:
  - 対象 staff_id の既存 staff_shifts を **全削除 → 7 rows 再 INSERT**
  - (staff_id, weekday) は複合 PK なので ON CONFLICT 戦略ではなく
    削除+再挿入で完全置換 (重複や古い値が残らない)
  - シートに存在しない staff の shifts には触れない (スコープ最小化)

Usage:
    # plan のみ
    python -m scripts.load_staff_shifts_from_sheet \\
        --xlsx /app/data/import/master.xlsx --dry-run

    # 本投入
    python -m scripts.load_staff_shifts_from_sheet \\
        --xlsx /app/data/import/master.xlsx --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    cell,
    clean_str,
    iter_rows,
    parse_weekdays,
)
from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.staff import Staff, StaffShift  # noqa: E402

SHEET_NAME = "スタッフマスタ"
# weekday integers per StaffShift model (0=Mon ... 6=Sun)
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DEFAULT_START = time(9, 30)
_DEFAULT_END = time(18, 0)


@dataclass
class _SheetEntry:
    code: str
    name: str
    saturday_on: bool
    raw_weekdays: list[str]


@dataclass
class _Report:
    staff_total_sheet: int = 0
    staff_matched: int = 0
    staff_missing_in_db: list[str] = field(default_factory=list)
    sat_on_codes: list[str] = field(default_factory=list)
    sat_off_codes: list[str] = field(default_factory=list)
    shifts_deleted: int = 0
    shifts_inserted: int = 0


def _read_sheet(xlsx: Path) -> list[_SheetEntry]:
    idx_map, rows = iter_rows(xlsx, SHEET_NAME)
    if not idx_map:
        raise RuntimeError(f"empty header row in sheet '{SHEET_NAME}'")
    required = ("staff_id", "勤務曜日")
    missing_headers = [h for h in required if h not in idx_map]
    if missing_headers:
        raise RuntimeError(
            f"required header(s) missing from '{SHEET_NAME}': {missing_headers}. "
            f"present headers: {sorted(idx_map.keys())}"
        )

    out: list[_SheetEntry] = []
    for row in rows:
        code = clean_str(cell(row, idx_map, "staff_id"))
        name = clean_str(cell(row, idx_map, "スタッフ名")) or ""
        if code is None:
            continue
        weekdays = parse_weekdays(cell(row, idx_map, "勤務曜日"))
        out.append(
            _SheetEntry(
                code=code,
                name=name,
                saturday_on="Sat" in weekdays,
                raw_weekdays=weekdays,
            )
        )
    return out


def _format_shift_row(weekday: int, is_on: bool) -> str:
    label = _WEEKDAYS[weekday]
    if is_on:
        return f"weekday={weekday}({label}) is_on=True 09:30-18:00"
    return f"weekday={weekday}({label}) is_on=False NULL"


async def _run(*, xlsx: Path, apply: bool) -> _Report:
    report = _Report()
    entries = _read_sheet(xlsx)
    report.staff_total_sheet = len(entries)
    if not entries:
        print(f"[warn] no staff rows found in sheet '{SHEET_NAME}' of {xlsx}")
        return report

    factory = get_session_factory()
    async with factory() as session:
        sheet_codes = [e.code for e in entries]
        existing = await session.scalars(select(Staff).where(Staff.code.in_(sheet_codes)))
        staff_by_code: dict[str, Staff] = {s.code: s for s in existing if s.code}

        targets: list[tuple[_SheetEntry, Staff]] = []
        for entry in entries:
            staff = staff_by_code.get(entry.code)
            if staff is None:
                report.staff_missing_in_db.append(entry.code)
                continue
            targets.append((entry, staff))
            report.staff_matched += 1
            if entry.saturday_on:
                report.sat_on_codes.append(f"{entry.code} {entry.name}".strip())
            else:
                report.sat_off_codes.append(f"{entry.code} {entry.name}".strip())

        if not targets:
            print("[warn] no matching active staff in DB; nothing to do")
            return report

        target_ids = [s.id for _, s in targets]

        # plan preview
        mode = "APPLY" if apply else "DRY-RUN"
        print(
            f"---- {mode} plan (sheet={xlsx.name}, sheet_rows={len(entries)}, "
            f"matched={len(targets)}) ----"
        )
        for entry, _staff in targets:
            print(
                f"  {entry.code:<6s} {entry.name:<14s} -> "
                f"sat_on={entry.saturday_on} (raw={entry.raw_weekdays})"
            )

        if not apply:
            # Count what would happen without touching DB.
            del_stmt = select(StaffShift).where(StaffShift.staff_id.in_(target_ids))
            existing_shifts = (await session.scalars(del_stmt)).all()
            report.shifts_deleted = len(existing_shifts)
            report.shifts_inserted = 7 * len(targets)
            await session.rollback()
            return report

        # Apply: wholesale replace (DELETE + 7-row INSERT per staff).
        del_result = await session.execute(
            StaffShift.__table__.delete().where(StaffShift.staff_id.in_(target_ids))
        )
        report.shifts_deleted = del_result.rowcount or 0

        for entry, staff in targets:  # noqa: B007 — staff is used below
            for wd_idx, _wd_name in enumerate(_WEEKDAYS):
                if wd_idx <= 4:  # Mon-Fri
                    is_on = True
                elif wd_idx == 5:  # Sat
                    is_on = entry.saturday_on
                else:  # Sun
                    is_on = False
                session.add(
                    StaffShift(
                        staff_id=staff.id,
                        weekday=wd_idx,
                        is_on=is_on,
                        start_time=_DEFAULT_START if is_on else None,
                        end_time=_DEFAULT_END if is_on else None,
                    )
                )
                report.shifts_inserted += 1

        await session.commit()
    return report


def _print_summary(report: _Report, *, apply: bool) -> None:
    prefix = "" if apply else "[dry-run] would "
    print()
    print("---- summary ----")
    print(f"sheet staff rows           : {report.staff_total_sheet}")
    print(f"matched against active DB  : {report.staff_matched}")
    if report.staff_missing_in_db:
        print(
            f"sheet codes NOT in DB      : {len(report.staff_missing_in_db)} "
            f"({', '.join(report.staff_missing_in_db[:10])}"
            f"{' ...' if len(report.staff_missing_in_db) > 10 else ''})"
        )
    print(f"{prefix}delete existing shift rows : {report.shifts_deleted}")
    print(f"{prefix}insert shift rows          : {report.shifts_inserted}")
    print()
    print(f"Saturday ON  ({len(report.sat_on_codes)} staff):")
    for c in report.sat_on_codes:
        print(f"  - {c}")
    print(f"Saturday OFF ({len(report.sat_off_codes)} staff):")
    for c in report.sat_off_codes:
        print(f"  - {c}")


async def _main(xlsx: Path, apply: bool) -> int:
    try:
        report = await _run(xlsx=xlsx, apply=apply)
    finally:
        await dispose_engine()
    _print_summary(report, apply=apply)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load staff weekly shifts directly into staff_shifts from the "
            "spreadsheet (master.xlsx / sheet 'スタッフマスタ'). "
            "Mon-Fri = 09:30-18:00, Sun = off, Sat = per 勤務曜日 column."
        )
    )
    parser.add_argument(
        "--xlsx",
        type=Path,
        required=True,
        help="path to master.xlsx (inside container e.g. /app/data/import/master.xlsx)",
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--dry-run",
        action="store_true",
        help="(default) print plan only; no DB writes",
    )
    grp.add_argument(
        "--apply",
        action="store_true",
        help="actually DELETE existing shift rows for matched staff and INSERT new ones",
    )
    args = parser.parse_args()

    # default behaviour = dry-run
    apply = bool(args.apply)
    try:
        return asyncio.run(_main(args.xlsx, apply))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
