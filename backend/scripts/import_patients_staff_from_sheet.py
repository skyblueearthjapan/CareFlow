"""Import patients / staff master from spreadsheet CSV exports.

スプレッドシート「訪問看護：よりより様／訪問スケジュール最適化」の
**患者マスタ** / **スタッフマスタ** 2 シートを CSV エクスポートし、
現行 v2 スキーマに合うように DB へ投入する。

**App コードは一切変更せず、データ投入のみ行う。** スキーマ変更 (Alembic
migration) も行わない。シート固有でアプリ側に居場所がない項目 (エリア /
曜日NG / 指定スタッフID / NGスタッフID / 継続希望 / スタッフのシフト /
得意エリア / 最大訪問件数 / スキル詳細 / 割当量 など) は本スクリプトでは
取り込まない (将来の自動算出ロジック設計後に別途検討)。

冪等性 (A 案 = code キー upsert):
  1) シート上の各 code について
     - 既存あり (deleted_at 問わず): UUID 保持で UPDATE、deleted_at=NULL に
     - 既存なし: INSERT
  2) シートに無い既存レコード: deleted_at=now() で論理削除
  3) FK 整合性は 100% 保たれる (visit / course / pending_request など無傷)

CSV 配置 (.gitignore で除外する想定 — **個人情報のため git commit 厳禁**):
  backend/data/import/patients_master.csv
  backend/data/import/staff_master.csv

Usage:
    # 計画だけ表示 (DB を変更しない)
    python scripts/import_patients_staff_from_sheet.py --dry-run

    # 本投入
    python scripts/import_patients_staff_from_sheet.py

    # 片方のみ
    python scripts/import_patients_staff_from_sheet.py --patients-only
    python scripts/import_patients_staff_from_sheet.py --staff-only

    # Geocoding をスキップ (lat/lng は NULL のまま)
    python scripts/import_patients_staff_from_sheet.py --no-geocode

    # CSV のディレクトリを明示
    python scripts/import_patients_staff_from_sheet.py --csv-dir path/to/dir

設計仕様議論メモ (2026-05-15 imaizumi 承認版):
  - status マップ: 稼働→active / 休止→suspended / 入院→admitted /
                   未開始→pending / 未契約→pending / 解約→cancelled
  - sex マップ: 女性→female / 男性→male
  - sex_restriction: 女性のみ→female_only / 男性のみ→male_only / 選択なし→NULL
  - insurance: 医療保険→medical / 空欄→NULL
  - primary_office_id: 住所に 中央区 / 若葉区 / 四街道市 を含めば TSUGA
                       それ以外は INAGE
  - lat/lng: シート値は破棄、address から Google Geocoding API で取得
  - weekly_pattern: 希望曜日 + サービス時間 + 時間タイプ + 希望時間帯 +
                    必要スタッフ数 + 週訪問回数 を JSONB に詰める
  - 捨てる列: エリア / 訪問頻度 / 訪問週 / 曜日優先度 / 曜日NG /
              指定スタッフID / 指定タイプ / NGスタッフID / 継続希望
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.office import Office  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.staff import Staff  # noqa: E402
from app.services.geocoding.client import (  # noqa: E402
    GeocodingServiceError,
    geocode_address,
)

logger = logging.getLogger("import_master")

# --- 値マッピング --------------------------------------------------------

STATUS_MAP_PATIENT = {
    "稼働": "active",
    "休止": "suspended",
    "入院": "admitted",
    "未開始": "pending",
    "未契約": "pending",
    "解約": "cancelled",
}

SEX_MAP = {
    "女性": "female",
    "男性": "male",
}

SEX_RESTRICTION_MAP = {
    "女性のみ": "female_only",
    "男性のみ": "male_only",
}

INSURANCE_MAP = {
    "医療保険": "medical",
    "介護保険": "care",
}

# シート上の "未入力" を表すセンチネル文字列。これらは NULL に正規化する。
SENTINEL_NULL = frozenset({"選択なし", "空白", "空欄", "どちらでもよい"})

# 都賀拠点が担当する区市町村名 (住所文字列に部分一致したら TSUGA)
TSUGA_DISTRICTS = ("中央区", "若葉区", "四街道市")

# WeeklyPatternV2 の time_type は日本語そのまま使う。シートの値もそのまま。
VALID_TIME_TYPES = frozenset({"固定", "時間帯", "午前", "午後", "終日"})

# シートの曜日表記 (英略, カンマ区切り) はそのまま採用
VALID_WEEKDAYS = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})


# --- ユーティリティ ------------------------------------------------------


def _is_blank(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    return stripped == "" or stripped in SENTINEL_NULL


def _clean(value: str | None) -> str | None:
    if _is_blank(value):
        return None
    return value.strip() if value is not None else None


def _normalize_hhmm(value: str | None) -> str | None:
    """'13:00:00' / '13:00' → 'HH:MM'. 不正値は None。"""
    cleaned = _clean(value)
    if cleaned is None:
        return None
    parts = cleaned.split(":")
    if len(parts) < 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _normalize_weekdays(value: str | None) -> list[str]:
    """'Mon, Wed, Fri' → ['Mon', 'Wed', 'Fri']. 不明な要素は捨てる。"""
    cleaned = _clean(value)
    if cleaned is None:
        return []
    parts = [p.strip() for p in cleaned.split(",")]
    return [p for p in parts if p in VALID_WEEKDAYS]


def _parse_int(value: str | None) -> int | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_staff_count(value: str | None) -> int:
    """'1人' / '2人' / '1' → int. 不明は 1 (既定)。"""
    cleaned = _clean(value)
    if cleaned is None:
        return 1
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits:
        return 1
    try:
        n = int(digits)
        return max(1, min(2, n))
    except ValueError:
        return 1


def _map_status_patient(value: str | None) -> str:
    cleaned = _clean(value)
    if cleaned is None:
        return "active"
    return STATUS_MAP_PATIENT.get(cleaned, "active")


def _map_sex(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return SEX_MAP.get(cleaned)


def _map_sex_restriction(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return SEX_RESTRICTION_MAP.get(cleaned)


def _map_insurance(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return INSURANCE_MAP.get(cleaned)


def _determine_office_code(address: str | None) -> str | None:
    """住所文字列から拠点コードを決定 (TSUGA / INAGE)。住所空なら None。"""
    if not address or not address.strip():
        return None
    for district in TSUGA_DISTRICTS:
        if district in address:
            return "TSUGA"
    return "INAGE"


def _build_weekly_pattern(row: dict[str, str]) -> dict[str, Any] | None:
    """シート行から weekly_pattern JSONB 構造を組み立てる。

    希望曜日が無い (= 稼働以外のレコード) なら None を返す。
    """
    weekdays = _normalize_weekdays(row.get("希望曜日（複数可）"))
    if not weekdays:
        return None

    time_type = _clean(row.get("時間タイプ"))
    if time_type not in VALID_TIME_TYPES:
        time_type = None

    preferred_start = _normalize_hhmm(row.get("希望時間帯（開始）"))
    preferred_end = _normalize_hhmm(row.get("希望時間帯（終了）"))
    service_minutes = _parse_int(row.get("サービス時間"))
    staff_count = _parse_staff_count(row.get("必要スタッフ数"))
    frequency_per_week = _parse_int(row.get("週訪問回数"))

    entries: list[dict[str, Any]] = []
    for wd in weekdays:
        entry: dict[str, Any] = {"weekday": wd, "staff_count": staff_count}
        if time_type is not None:
            entry["time_type"] = time_type
        if preferred_start is not None:
            entry["preferred_start"] = preferred_start
        if preferred_end is not None:
            entry["preferred_end"] = preferred_end
        if service_minutes is not None:
            entry["service_minutes"] = service_minutes
        entries.append(entry)

    pattern: dict[str, Any] = {
        "preferred_weekdays": weekdays,
        "entries": entries,
    }
    if frequency_per_week is not None:
        pattern["frequency_per_week"] = frequency_per_week
    if time_type is not None:
        pattern["time_type"] = time_type
    if preferred_start is not None:
        pattern["preferred_start"] = preferred_start
    if preferred_end is not None:
        pattern["preferred_end"] = preferred_end
    if service_minutes is not None:
        pattern["service_minutes"] = service_minutes
    return pattern


# --- CSV 読込 -----------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [{(k or "").strip(): (v or "") for k, v in row.items()} for row in reader]
    return rows


# --- 結果集計 -----------------------------------------------------------


@dataclass
class ImportReport:
    patients_inserted: int = 0
    patients_updated: int = 0
    patients_soft_deleted: int = 0
    patients_geocoded: int = 0
    patients_geocode_failed: list[str] = field(default_factory=list)
    patients_skipped: list[str] = field(default_factory=list)

    staff_inserted: int = 0
    staff_updated: int = 0
    staff_soft_deleted: int = 0
    staff_skipped: list[str] = field(default_factory=list)

    def summary(self, dry_run: bool) -> str:
        prefix = "[dry-run] would " if dry_run else ""
        lines = [
            "",
            "---- summary ----",
            f"{prefix}insert patients: {self.patients_inserted}",
            f"{prefix}update patients: {self.patients_updated}",
            f"{prefix}soft-delete patients: {self.patients_soft_deleted}",
            f"{prefix}geocoded addresses: {self.patients_geocoded}",
            f"{prefix}insert staff: {self.staff_inserted}",
            f"{prefix}update staff: {self.staff_updated}",
            f"{prefix}soft-delete staff: {self.staff_soft_deleted}",
        ]
        if self.patients_geocode_failed:
            lines.append(
                "geocoding failures: "
                + ", ".join(self.patients_geocode_failed[:10])
                + (
                    f" (+{len(self.patients_geocode_failed) - 10} more)"
                    if len(self.patients_geocode_failed) > 10
                    else ""
                )
            )
        if self.patients_skipped:
            lines.append(f"skipped patient rows (no code): {len(self.patients_skipped)}")
        if self.staff_skipped:
            lines.append(f"skipped staff rows (no code): {len(self.staff_skipped)}")
        return "\n".join(lines)


# --- Geocoding ----------------------------------------------------------


async def _geocode_or_none(address: str) -> tuple[float | None, float | None]:
    try:
        result = await geocode_address(address)
    except GeocodingServiceError as exc:
        logger.warning("geocode failed for %r: %s", address, exc)
        return None, None
    if result is None:
        return None, None
    return result.lat, result.lng


# --- Patient upsert -----------------------------------------------------


async def _upsert_patients(
    session,
    rows: list[dict[str, str]],
    office_id_by_code: dict[str, Any],
    *,
    dry_run: bool,
    do_geocode: bool,
    report: ImportReport,
) -> None:
    sheet_codes: set[str] = set()

    for row in rows:
        code = _clean(row.get("patient_id"))
        name = _clean(row.get("患者名"))
        if code is None or name is None:
            report.patients_skipped.append(str(row.get("patient_id") or ""))
            continue
        sheet_codes.add(code)

        kana = _clean(row.get("フリガナ"))
        sex = _map_sex(row.get("性別"))
        status = _map_status_patient(row.get("稼働状況"))
        address = _clean(row.get("住所"))
        insurance = _map_insurance(row.get("保険区分"))
        sex_restriction = _map_sex_restriction(row.get("性別制限"))
        note = _clean(row.get("備考"))
        weekly_pattern = _build_weekly_pattern(row)

        # 拠点判定: 住所文字列ベース
        office_code = _determine_office_code(address)
        primary_office_id = office_id_by_code.get(office_code) if office_code else None

        # Geocoding (シートの lat/lng は破棄)
        lat: float | None = None
        lng: float | None = None
        if do_geocode and address is not None:
            lat, lng = await _geocode_or_none(address)
            if lat is None or lng is None:
                report.patients_geocode_failed.append(code)
            else:
                report.patients_geocoded += 1

        # upsert: code をキーに既存検索 (deleted_at 問わず)
        existing = await session.scalar(select(Patient).where(Patient.code == code))

        if existing is None:
            if dry_run:
                report.patients_inserted += 1
                print(
                    f"[dry-run] INSERT patient code={code} name={name} "
                    f"office={office_code} status={status}"
                )
                continue
            patient = Patient(
                code=code,
                name=name,
                kana=kana,
                sex=sex,
                status=status,
                address=address,
                lat=lat,
                lng=lng,
                insurance=insurance,
                primary_office_id=primary_office_id,
                sex_restriction=sex_restriction,
                requires_multiple_staff=False,
                weekly_pattern=weekly_pattern,
                special_weekly_pattern=None,
                special_week_active=[],
                note=note,
            )
            session.add(patient)
            report.patients_inserted += 1
            print(f"INSERT patient code={code} name={name} office={office_code}")
        else:
            if dry_run:
                report.patients_updated += 1
                action = "restore+UPDATE" if existing.deleted_at is not None else "UPDATE"
                print(
                    f"[dry-run] {action} patient code={code} id={existing.id} "
                    f"name={name} office={office_code} status={status}"
                )
                continue
            existing.name = name
            existing.kana = kana
            existing.sex = sex
            existing.status = status
            existing.address = address
            if lat is not None and lng is not None:
                existing.lat = lat
                existing.lng = lng
            existing.insurance = insurance
            existing.primary_office_id = primary_office_id
            existing.sex_restriction = sex_restriction
            existing.requires_multiple_staff = False
            existing.weekly_pattern = weekly_pattern
            existing.special_weekly_pattern = None
            existing.special_week_active = []
            existing.note = note
            existing.deleted_at = None
            report.patients_updated += 1
            print(f"UPDATE patient code={code} id={existing.id} name={name}")

    # シートに無い既存患者を論理削除
    now = datetime.now(tz=UTC)
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    alive_patients = (await session.scalars(stmt)).all()
    for p in alive_patients:
        if p.code not in sheet_codes:
            if dry_run:
                report.patients_soft_deleted += 1
                print(f"[dry-run] SOFT-DELETE patient code={p.code} id={p.id} name={p.name}")
            else:
                p.deleted_at = now
                report.patients_soft_deleted += 1
                print(f"SOFT-DELETE patient code={p.code} id={p.id} name={p.name}")


# --- Staff upsert -------------------------------------------------------


async def _upsert_staff(
    session,
    rows: list[dict[str, str]],
    office_id_by_code: dict[str, Any],
    *,
    dry_run: bool,
    report: ImportReport,
) -> None:
    sheet_codes: set[str] = set()

    for row in rows:
        code = _clean(row.get("staff_id"))
        name = _clean(row.get("スタッフ名"))
        if code is None or name is None:
            report.staff_skipped.append(str(row.get("staff_id") or ""))
            continue
        sheet_codes.add(code)

        sex = _map_sex(row.get("性別"))
        # 拠点判定 (スタッフは「拠点住所」列を使う)
        base_address = _clean(row.get("拠点住所"))
        office_code = _determine_office_code(base_address)
        primary_office_id = office_id_by_code.get(office_code) if office_code else None

        skill = _clean(row.get("スキル"))
        is_trainee = skill == "新人"

        existing = await session.scalar(select(Staff).where(Staff.code == code))

        if existing is None:
            if dry_run:
                report.staff_inserted += 1
                print(
                    f"[dry-run] INSERT staff code={code} name={name} "
                    f"office={office_code} is_trainee={is_trainee}"
                )
                continue
            staff = Staff(
                code=code,
                name=name,
                kana=None,
                sex=sex,
                status="active",
                role="staff",
                primary_office_id=primary_office_id,
                is_trainee=is_trainee,
                note=None,
            )
            session.add(staff)
            report.staff_inserted += 1
            print(f"INSERT staff code={code} name={name} office={office_code}")
        else:
            if dry_run:
                report.staff_updated += 1
                action = "restore+UPDATE" if existing.deleted_at is not None else "UPDATE"
                print(
                    f"[dry-run] {action} staff code={code} id={existing.id} "
                    f"name={name} office={office_code}"
                )
                continue
            existing.name = name
            existing.sex = sex
            existing.primary_office_id = primary_office_id
            existing.is_trainee = is_trainee
            # role / status は既存値を尊重 (admin/manager が手動設定されている可能性)
            existing.deleted_at = None
            report.staff_updated += 1
            print(f"UPDATE staff code={code} id={existing.id} name={name}")

    # シートに無い既存スタッフを論理削除
    now = datetime.now(tz=UTC)
    stmt = select(Staff).where(Staff.deleted_at.is_(None))
    alive_staff = (await session.scalars(stmt)).all()
    for s in alive_staff:
        if s.code is None or s.code not in sheet_codes:
            if dry_run:
                report.staff_soft_deleted += 1
                print(f"[dry-run] SOFT-DELETE staff code={s.code} id={s.id} name={s.name}")
            else:
                s.deleted_at = now
                report.staff_soft_deleted += 1
                print(f"SOFT-DELETE staff code={s.code} id={s.id} name={s.name}")


# --- 拠点 ID 解決 -------------------------------------------------------


async def _load_office_map(session) -> dict[str, Any]:
    offices = (await session.scalars(select(Office))).all()
    by_code = {o.code: o.id for o in offices}
    missing = [c for c in ("INAGE", "TSUGA") if c not in by_code]
    if missing:
        raise RuntimeError(
            f"required office codes not found: {missing}. "
            "Run `python scripts/seed_offices_v2.py` first."
        )
    return by_code


# --- main ---------------------------------------------------------------


async def _run(
    *,
    csv_dir: Path,
    dry_run: bool,
    patients_only: bool,
    staff_only: bool,
    do_geocode: bool,
) -> ImportReport:
    report = ImportReport()
    factory = get_session_factory()

    async with factory() as session:
        office_id_by_code = await _load_office_map(session)

        if not staff_only:
            patient_csv = csv_dir / "patients_master.csv"
            print(f"reading {patient_csv}")
            patient_rows = _read_csv(patient_csv)
            print(f"  -> {len(patient_rows)} rows")
            await _upsert_patients(
                session,
                patient_rows,
                office_id_by_code,
                dry_run=dry_run,
                do_geocode=do_geocode,
                report=report,
            )

        if not patients_only:
            staff_csv = csv_dir / "staff_master.csv"
            print(f"reading {staff_csv}")
            staff_rows = _read_csv(staff_csv)
            print(f"  -> {len(staff_rows)} rows")
            await _upsert_staff(
                session,
                staff_rows,
                office_id_by_code,
                dry_run=dry_run,
                report=report,
            )

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    return report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Import patient/staff masters from spreadsheet CSV. "
            "CSV files contain PII -- do NOT commit them to git."
        )
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=_BACKEND_ROOT / "data" / "import",
        help="directory containing patients_master.csv / staff_master.csv",
    )
    parser.add_argument("--dry-run", action="store_true", help="print plan only, no DB writes")
    parser.add_argument("--patients-only", action="store_true", help="import patients only")
    parser.add_argument("--staff-only", action="store_true", help="import staff only")
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="skip Google Geocoding (lat/lng will be NULL for new rows)",
    )
    args = parser.parse_args()

    if args.patients_only and args.staff_only:
        print("error: --patients-only and --staff-only are mutually exclusive", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(
            _run(
                csv_dir=args.csv_dir,
                dry_run=args.dry_run,
                patients_only=args.patients_only,
                staff_only=args.staff_only,
                do_geocode=not args.no_geocode,
            )
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            f"hint: export the sheets as CSV and place them under "
            f"{args.csv_dir} as patients_master.csv / staff_master.csv",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        asyncio.run(dispose_engine())

    print(report.summary(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
