"""Seed v2 offices: 稲毛拠点 / 都賀拠点 (W1-BE3).

設計仕様書 `docs/plans/v2-allocation-redesign.md` v0.9 §4.3 に基づく
2 拠点のシード。各拠点に住所・緯度経度・担当市区町村 (M2M) を設定する。

前提:
- `cities` テーブルが既にシード済みであること（`scripts/seed_cities.py`）。
  少なくとも以下の市区町村が必要:
    - 千葉県 千葉市稲毛区 (jis_code=12103)
    - 千葉県 千葉市若葉区 (jis_code=12104)

挙動:
- 拠点 `code` (`INAGE` / `TSUGA`) を一意キーとした冪等 upsert。
- `office_cities` (M2M) は seed 対象 city が見つかれば INSERT、既存は無視。
- 既に存在する拠点には住所・座標を強制更新しない（運用で手動更新済みの可能性が
  あるため）。新規作成時のみ初期値を入れる。

Usage:
    python scripts/seed_offices_v2.py            # apply seed
    python scripts/seed_offices_v2.py --dry-run  # only print plan
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure backend/ is importable when invoked as `python scripts/seed_offices_v2.py`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.city import City  # noqa: E402
from app.models.office import Office, OfficeCity  # noqa: E402


@dataclass(frozen=True)
class OfficeSeed:
    """1 拠点のシード仕様。"""

    code: str
    name: str
    prefecture: str
    address: str
    lat: float
    lng: float
    note: str
    # 担当市区町村: (prefecture, name) のタプルで cities テーブルを引く
    allowed_cities: tuple[tuple[str, str], ...]
    # Phase G-45: 稼働曜日 (0=月..6=日). 稲毛=月-土, 都賀=月-金.
    operating_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5)


# 拠点マスタ シード値
# 座標は地図検索結果に基づく妥当な値。実運用で変更があれば手動更新（本 seed は
# 既存拠点の値は上書きしない）。
SEEDS: tuple[OfficeSeed, ...] = (
    OfficeSeed(
        code="INAGE",
        name="稲毛",
        prefecture="千葉県",
        address="千葉県千葉市稲毛区穴川4丁目12-4",
        lat=35.6371,
        lng=140.1218,
        note="v2 シード (W1-BE3): 千葉市稲毛区を担当",
        allowed_cities=(("千葉県", "千葉市稲毛区"),),
        operating_weekdays=(0, 1, 2, 3, 4, 5),  # 月-土
    ),
    OfficeSeed(
        code="TSUGA",
        name="都賀",
        prefecture="千葉県",
        address="千葉県千葉市若葉区都賀3丁目19-3",
        lat=35.6440,
        lng=140.1535,
        note="v2 シード (W1-BE3): 千葉市若葉区都賀エリアを担当 (区単位は若葉区)",
        allowed_cities=(("千葉県", "千葉市若葉区"),),
        operating_weekdays=(0, 1, 2, 3, 4),  # 月-金
    ),
)


@dataclass
class SeedReport:
    inserted_offices: int = 0
    updated_offices: int = 0
    skipped_offices: int = 0
    inserted_office_cities: int = 0
    skipped_office_cities: int = 0
    missing_cities: list[tuple[str, str]] = field(default_factory=list)


async def _apply(seeds: tuple[OfficeSeed, ...], dry_run: bool) -> SeedReport:
    """Apply or simulate seed. Idempotent on (Office.code, OfficeCity (office,city))."""
    report = SeedReport()
    factory = get_session_factory()
    async with factory() as session:
        for seed in seeds:
            existing = await session.scalar(select(Office).where(Office.code == seed.code))
            if existing is None:
                if dry_run:
                    report.inserted_offices += 1
                    print(
                        f"[dry-run] insert office: code={seed.code} name={seed.name} "
                        f"address={seed.address}"
                    )
                else:
                    office = Office(
                        code=seed.code,
                        name=seed.name,
                        prefecture=seed.prefecture,
                        address=seed.address,
                        lat=seed.lat,
                        lng=seed.lng,
                        note=seed.note,
                        operating_weekdays=list(seed.operating_weekdays),
                    )
                    session.add(office)
                    await session.flush()  # populate office.id
                    report.inserted_offices += 1
                    print(f"inserted office: code={seed.code} id={office.id} name={seed.name}")
                    existing = office
            else:
                # 既存拠点は値を上書きしない（運用変更に配慮）
                report.skipped_offices += 1
                print(
                    f"skipped existing office: code={seed.code} id={existing.id} "
                    f"name={existing.name}"
                )

            # M2M: office_cities
            for pref, city_name in seed.allowed_cities:
                city = await session.scalar(
                    select(City).where(
                        City.prefecture == pref,
                        City.name == city_name,
                        City.deleted_at.is_(None),
                    )
                )
                if city is None:
                    print(
                        f"  warn: city not found: {pref}{city_name} — run "
                        f"scripts/seed_cities.py first"
                    )
                    report.missing_cities.append((pref, city_name))
                    continue

                if dry_run:
                    report.inserted_office_cities += 1
                    print(f"  [dry-run] link office {seed.code} <-> city {pref}{city_name}")
                    continue

                # existing が dry-run でない場合に確実に確定済み
                assert existing is not None
                already = await session.scalar(
                    select(OfficeCity).where(
                        OfficeCity.office_id == existing.id,
                        OfficeCity.city_id == city.id,
                    )
                )
                if already is None:
                    session.add(OfficeCity(office_id=existing.id, city_id=city.id))
                    report.inserted_office_cities += 1
                    print(f"  linked office {seed.code} <-> city {pref}{city_name}")
                else:
                    report.skipped_office_cities += 1
                    print(f"  skipped existing link: {seed.code} <-> {pref}{city_name}")

        if not dry_run:
            await session.commit()

    return report


async def _main(dry_run: bool) -> int:
    try:
        report = await _apply(SEEDS, dry_run)
    finally:
        await dispose_engine()

    label = "would " if dry_run else ""
    print()
    print("---- summary ----")
    print(
        f"{label}insert offices: {report.inserted_offices} / "
        f"updated: {report.updated_offices} / skipped: {report.skipped_offices}"
    )
    print(
        f"{label}insert office_cities: {report.inserted_office_cities} / "
        f"skipped: {report.skipped_office_cities}"
    )
    if report.missing_cities:
        print(
            f"missing cities (run seed_cities.py first): "
            f"{', '.join(f'{p}{n}' for p, n in report.missing_cities)}"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed v2 offices (稲毛 / 都賀)")
    parser.add_argument("--dry-run", action="store_true", help="print plan only")
    args = parser.parse_args()
    return asyncio.run(_main(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
