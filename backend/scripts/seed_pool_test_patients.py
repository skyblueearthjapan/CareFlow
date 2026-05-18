"""diff-add テスト用 pool 患者を 3 名 投入 (PFV なし).

Usage:
    python -m scripts.seed_pool_test_patients --dry-run
    python -m scripts.seed_pool_test_patients --apply

特徴:
  - patient_fixed_visits は作らない (= pool 残患者の条件)
  - patients は active, sex_restriction なし, weekly_pattern 設定済み
  - lat/lng は手動値 (千葉市内、Geocoding 不要)
  - code: P-POOL-001 / 002 / 003

冪等性:
  - 既存 P-POOL-* がある場合は UPDATE (deleted_at=NULL に restore)
  - シートと違い code は固定の 3 件のみなので「シートに無い既存」削除はしない
  - PFV は触らない (もし手動で PFV が紐付いていても本スクリプトでは消さない)

セーフティ:
  - 既定は dry-run。--apply を明示しない限り DB へは書かない
  - 全 INSERT/UPDATE は 1 transaction で commit / rollback
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.office import Office  # noqa: E402
from app.models.patient import Patient  # noqa: E402

logger = logging.getLogger("seed_pool_test_patients")


# --- 投入データ ---------------------------------------------------------

PATIENTS: list[dict[str, Any]] = [
    {
        "code": "P-POOL-001",
        "name": "プール 太郎",
        "kana": "プール タロウ",
        "sex": "male",
        "status": "active",
        "insurance": "medical",
        "address": "千葉県千葉市稲毛区プールテスト町1-1-1",
        "lat": 35.6431,
        "lng": 140.0966,
        "primary_office_code": "INAGE",
        "weekly_pattern": {
            "time_type": "時間帯",
            "preferred_start": "09:00",
            "preferred_end": "12:00",
            "service_minutes": 30,
            "frequency_per_week": 2,
            "preferred_weekdays": ["Mon", "Wed"],
            "ng_weekdays": [],
        },
        "special_week_active": [],
        "note": "プール残テスト用 (P-POOL-001)",
    },
    {
        "code": "P-POOL-002",
        "name": "プール 花子",
        "kana": "プール ハナコ",
        "sex": "female",
        "status": "active",
        "insurance": "medical",
        "address": "千葉県千葉市稲毛区プールテスト町2-2-2",
        "lat": 35.6440,
        "lng": 140.1000,
        "primary_office_code": "INAGE",
        "weekly_pattern": {
            "time_type": "固定",
            "preferred_start": "14:00",
            "preferred_end": "14:30",
            "service_minutes": 30,
            "frequency_per_week": 1,
            "preferred_weekdays": ["Tue"],
        },
        "special_week_active": [],
        "note": "プール残テスト用 (P-POOL-002)",
    },
    {
        "code": "P-POOL-003",
        "name": "プール 次郎",
        "kana": "プール ジロウ",
        "sex": "male",
        "status": "active",
        "insurance": "care",
        "address": "千葉県千葉市若葉区プールテスト町3-3-3",
        "lat": 35.6500,
        "lng": 140.1500,
        "primary_office_code": "TSUGA",
        "weekly_pattern": {
            "time_type": "午前",
            "service_minutes": 45,
            "frequency_per_week": 3,
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
        },
        "special_week_active": [],
        "note": "プール残テスト用 (P-POOL-003)",
    },
]


# --- 拠点 ID 解決 -------------------------------------------------------


async def _load_office_map(session) -> dict[str, Any]:
    offices = (await session.scalars(select(Office))).all()
    by_code = {o.code: o.id for o in offices if o.code is not None}
    needed = {p["primary_office_code"] for p in PATIENTS}
    missing = sorted(c for c in needed if c not in by_code)
    if missing:
        raise RuntimeError(
            f"required office codes not found: {missing}. "
            "Run `python scripts/seed_offices_v2.py` first."
        )
    return by_code


# --- upsert -------------------------------------------------------------


async def _upsert_one(
    session,
    spec: dict[str, Any],
    office_id_by_code: dict[str, Any],
    *,
    dry_run: bool,
) -> str:
    """Return one of: 'INSERT' / 'UPDATE' / 'RESTORE+UPDATE'."""
    code = spec["code"]
    primary_office_id = office_id_by_code[spec["primary_office_code"]]

    existing = await session.scalar(select(Patient).where(Patient.code == code))

    if existing is None:
        action = "INSERT"
        if dry_run:
            print(
                f"[dry-run] {action} patient code={code} name={spec['name']} "
                f"office={spec['primary_office_code']} status={spec['status']}"
            )
            return action
        patient = Patient(
            code=code,
            name=spec["name"],
            kana=spec["kana"],
            sex=spec["sex"],
            status=spec["status"],
            address=spec["address"],
            lat=spec["lat"],
            lng=spec["lng"],
            insurance=spec["insurance"],
            primary_office_id=primary_office_id,
            sex_restriction=None,
            requires_multiple_staff=False,
            weekly_pattern=spec["weekly_pattern"],
            special_weekly_pattern=None,
            special_week_active=spec["special_week_active"],
            note=spec["note"],
        )
        session.add(patient)
        print(
            f"{action} patient code={code} name={spec['name']} office={spec['primary_office_code']}"
        )
        return action

    # existing path
    action = "RESTORE+UPDATE" if existing.deleted_at is not None else "UPDATE"
    if dry_run:
        print(
            f"[dry-run] {action} patient code={code} id={existing.id} "
            f"name={spec['name']} office={spec['primary_office_code']} "
            f"status={spec['status']}"
        )
        return action

    existing.name = spec["name"]
    existing.kana = spec["kana"]
    existing.sex = spec["sex"]
    existing.status = spec["status"]
    existing.address = spec["address"]
    existing.lat = spec["lat"]
    existing.lng = spec["lng"]
    existing.insurance = spec["insurance"]
    existing.primary_office_id = primary_office_id
    existing.sex_restriction = None
    existing.requires_multiple_staff = False
    existing.weekly_pattern = spec["weekly_pattern"]
    existing.special_weekly_pattern = None
    existing.special_week_active = spec["special_week_active"]
    existing.note = spec["note"]
    existing.deleted_at = None
    print(f"{action} patient code={code} id={existing.id} name={spec['name']}")
    return action


# --- main ---------------------------------------------------------------


async def _run(*, dry_run: bool) -> dict[str, int]:
    counts = {"INSERT": 0, "UPDATE": 0, "RESTORE+UPDATE": 0}
    factory = get_session_factory()

    try:
        async with factory() as session:
            office_id_by_code = await _load_office_map(session)
            for spec in PATIENTS:
                action = await _upsert_one(session, spec, office_id_by_code, dry_run=dry_run)
                counts[action] = counts.get(action, 0) + 1

            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        # 同一 event loop 内で dispose する (別ループで dispose すると
        # asyncpg が "Event loop is closed" を吐くため).
        await dispose_engine()

    return counts


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Seed 3 pool test patients (P-POOL-001/002/003) without "
            "patient_fixed_visits so they remain in the diff-add candidate pool. "
            "Default is dry-run; pass --apply to actually write."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="print plan only, no DB writes (default behavior)",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="actually INSERT/UPDATE patients in DB",
    )
    args = parser.parse_args()

    # 既定は dry-run。--apply が無ければ書かない。
    dry_run = not args.apply

    try:
        counts = asyncio.run(_run(dry_run=dry_run))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    prefix = "[dry-run] would " if dry_run else ""
    print("")
    print("---- summary ----")
    print(f"{prefix}INSERT:         {counts.get('INSERT', 0)}")
    print(f"{prefix}UPDATE:         {counts.get('UPDATE', 0)}")
    print(f"{prefix}RESTORE+UPDATE: {counts.get('RESTORE+UPDATE', 0)}")
    print(
        f"total target patients: {len(PATIENTS)} (codes: {', '.join(p['code'] for p in PATIENTS)})"
    )
    if dry_run:
        print("\n(dry-run mode. Re-run with --apply to actually write.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
