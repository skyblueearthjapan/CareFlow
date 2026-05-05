"""Tests for OfficeAssigner (W1-BE3).

設計仕様書 `docs/plans/v2-allocation-redesign.md` v0.9 §4.3 / 実装手順書
W1-BE3 受入基準:
  (1) 千葉市稲毛区の住所で「稲毛」自動取得
  (2) 千葉市若葉区都賀の住所で「都賀」
  (3) 範囲外住所で警告 (None 返却)

加えて以下のエッジケースも検証:
  - address が None / 空文字 → None
  - 都道府県無しの住所 (fuzzy match)
  - 該当 city はあるが office なし → None + matched_city_id は埋まる
  - cities テーブルが「千葉市」と「千葉市稲毛区」を同時に持つときの最長一致
"""

from __future__ import annotations

import pytest

from app.models import City, Office, OfficeCity
from app.services.office_assigner import OfficeAssigner

# ---------------------------------------------------------------------------
# helpers (function-scoped; rely on conftest's `db` fixture)
# ---------------------------------------------------------------------------


async def _seed_cities(db) -> dict[str, City]:
    """Seed minimal city rows used across tests."""
    rows = {
        "inage": City(prefecture="千葉県", name="千葉市稲毛区", jis_code="12103"),
        "wakaba": City(prefecture="千葉県", name="千葉市若葉区", jis_code="12104"),
        "chuo": City(prefecture="千葉県", name="千葉市中央区", jis_code="12101"),
        "chiyoda": City(prefecture="東京都", name="千代田区", jis_code="13101"),
    }
    for c in rows.values():
        db.add(c)
    await db.commit()
    for c in rows.values():
        await db.refresh(c)
    return rows


async def _seed_offices(db, cities: dict[str, City]) -> dict[str, Office]:
    """Seed 稲毛 (担当: 稲毛区) / 都賀 (担当: 若葉区) と M2M を作る."""
    inage = Office(
        code="INAGE",
        name="稲毛",
        prefecture="千葉県",
        address="千葉県千葉市稲毛区穴川4丁目12-4",
        lat=35.6371,
        lng=140.1218,
    )
    tsuga = Office(
        code="TSUGA",
        name="都賀",
        prefecture="千葉県",
        address="千葉県千葉市若葉区都賀3丁目19-3",
        lat=35.6440,
        lng=140.1535,
    )
    db.add_all([inage, tsuga])
    await db.commit()
    await db.refresh(inage)
    await db.refresh(tsuga)

    db.add_all(
        [
            OfficeCity(office_id=inage.id, city_id=cities["inage"].id),
            OfficeCity(office_id=tsuga.id, city_id=cities["wakaba"].id),
        ]
    )
    await db.commit()
    return {"inage": inage, "tsuga": tsuga}


# ---------------------------------------------------------------------------
# core acceptance cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_inage_address_returns_inage_office(db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)

    office = await OfficeAssigner.resolve_for_address(db, "千葉県千葉市稲毛区稲毛東1丁目")
    assert office is not None
    assert office.id == offices["inage"].id
    assert office.name == "稲毛"


@pytest.mark.asyncio
async def test_resolve_tsuga_address_returns_tsuga_office(db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)

    office = await OfficeAssigner.resolve_for_address(db, "千葉県千葉市若葉区都賀1丁目")
    assert office is not None
    assert office.id == offices["tsuga"].id
    assert office.name == "都賀"


@pytest.mark.asyncio
async def test_resolve_out_of_range_returns_none(db) -> None:
    """東京都千代田区: cities にはあるが office_cities リンクが無い → None.

    受入基準 (3) の「範囲外住所で警告」に対応する。
    """
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    office = await OfficeAssigner.resolve_for_address(db, "東京都千代田区")
    assert office is None


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_empty_or_none_address_returns_none(db) -> None:
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    assert await OfficeAssigner.resolve_for_address(db, None) is None
    assert await OfficeAssigner.resolve_for_address(db, "") is None
    assert await OfficeAssigner.resolve_for_address(db, "   ") is None


@pytest.mark.asyncio
async def test_resolve_with_details_exact_match_confidence(db) -> None:
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    res = await OfficeAssigner.resolve_with_details(db, "千葉県千葉市稲毛区稲毛東1丁目")
    assert res.office is not None
    assert res.office.name == "稲毛"
    assert res.matched_city_id == cities["inage"].id
    assert res.confidence == "exact"


@pytest.mark.asyncio
async def test_resolve_with_details_no_prefecture_falls_back_to_fuzzy(db) -> None:
    """都道府県表記が無い住所でも市区町村名でマッチする (fuzzy)."""
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)

    res = await OfficeAssigner.resolve_with_details(db, "千葉市稲毛区稲毛東1丁目")
    assert res.office is not None
    assert res.office.id == offices["inage"].id
    assert res.confidence == "fuzzy"


@pytest.mark.asyncio
async def test_resolve_out_of_range_returns_none_with_details(db) -> None:
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    res = await OfficeAssigner.resolve_with_details(db, "東京都千代田区")
    assert res.office is None
    # city 自体は登録されているので matched_city_id は埋まる
    assert res.matched_city_id == cities["chiyoda"].id
    assert res.confidence == "none"


@pytest.mark.asyncio
async def test_resolve_unknown_pref_and_city_returns_pure_none(db) -> None:
    """cities にもまったく該当しない住所は matched_city_id も None."""
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    res = await OfficeAssigner.resolve_with_details(db, "北海道札幌市中央区")
    assert res.office is None
    assert res.matched_city_id is None
    assert res.confidence == "none"


@pytest.mark.asyncio
async def test_resolve_prefers_longer_city_match(db) -> None:
    """住所に「千葉市」と「千葉市稲毛区」の両方を含む文字列で、
    cities テーブルにも両方ある場合、長い方 (稲毛区) を優先する.
    """
    # 「千葉市」(政令市そのもの) を追加でシード
    cities = await _seed_cities(db)
    chiba_city = City(prefecture="千葉県", name="千葉市", jis_code="12100")
    db.add(chiba_city)
    await db.commit()
    await db.refresh(chiba_city)

    offices = await _seed_offices(db, cities)

    res = await OfficeAssigner.resolve_with_details(db, "千葉県千葉市稲毛区穴川4丁目")
    assert res.office is not None
    assert res.office.id == offices["inage"].id
    assert res.matched_city_id == cities["inage"].id


@pytest.mark.asyncio
async def test_resolve_skips_soft_deleted_office(db) -> None:
    """deleted_at が設定された office は候補から除外される."""
    from datetime import UTC, datetime

    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    offices["inage"].deleted_at = datetime.now(UTC)
    await db.commit()

    office = await OfficeAssigner.resolve_for_address(db, "千葉県千葉市稲毛区稲毛東1丁目")
    assert office is None


@pytest.mark.asyncio
async def test_resolve_skips_soft_deleted_city(db) -> None:
    """deleted_at が設定された city は候補から除外される."""
    from datetime import UTC, datetime

    cities = await _seed_cities(db)
    await _seed_offices(db, cities)
    cities["inage"].deleted_at = datetime.now(UTC)
    await db.commit()

    office = await OfficeAssigner.resolve_for_address(db, "千葉県千葉市稲毛区稲毛東1丁目")
    assert office is None


# ---------------------------------------------------------------------------
# /api/v1/offices/resolve endpoint smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_endpoint_admin_returns_office(client, db) -> None:
    from app.core.security import create_access_token, hash_password
    from app.models import User

    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)

    admin = User(
        email="resolve-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token = create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)
    res = await client.post(
        "/api/v1/offices/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"address": "千葉県千葉市稲毛区稲毛東1丁目"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["office_id"] == str(offices["inage"].id)
    assert body["office_name"] == "稲毛"
    assert body["matched_city_id"] == str(cities["inage"].id)
    assert body["confidence"] == "exact"


@pytest.mark.asyncio
async def test_resolve_endpoint_out_of_range_returns_null(client, db) -> None:
    from app.core.security import create_access_token, hash_password
    from app.models import User

    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    admin = User(
        email="resolve-admin2@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token = create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)
    res = await client.post(
        "/api/v1/offices/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"address": "東京都千代田区"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["office_id"] is None
    assert body["office_name"] is None
    assert body["matched_city_id"] == str(cities["chiyoda"].id)
    assert body["confidence"] == "none"


@pytest.mark.asyncio
async def test_resolve_endpoint_staff_role_forbidden(client, db) -> None:
    from app.core.security import create_access_token, hash_password
    from app.models import User

    cities = await _seed_cities(db)
    await _seed_offices(db, cities)

    staff_user = User(
        email="resolve-staff@example.com",
        password_hash=hash_password("does-not-matter"),
        role="staff",
    )
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)

    token = create_access_token(
        subject=staff_user.id, role=staff_user.role, staff_id=staff_user.staff_id
    )
    res = await client.post(
        "/api/v1/offices/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"address": "千葉県千葉市稲毛区稲毛東1丁目"},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# seed_offices_v2 smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_offices_v2_inserts_two_offices_with_links(db) -> None:
    """`scripts/seed_offices_v2.py` の `_apply` を直接呼んで動作確認."""
    from sqlalchemy import select

    # Ensure required cities exist (seed_offices_v2 expects pre-seeded cities).
    await _seed_cities(db)

    # Import using the same path the script uses.
    from scripts import seed_offices_v2

    report = await seed_offices_v2._apply(seed_offices_v2.SEEDS, dry_run=False)
    assert report.inserted_offices == 2
    assert report.inserted_office_cities == 2
    assert not report.missing_cities

    rows = (await db.scalars(select(Office).order_by(Office.code))).all()
    codes = {o.code for o in rows}
    assert {"INAGE", "TSUGA"}.issubset(codes)

    # Idempotency: 二度目の実行で挿入が増えない
    report2 = await seed_offices_v2._apply(seed_offices_v2.SEEDS, dry_run=False)
    assert report2.inserted_offices == 0
    assert report2.skipped_offices == 2
    assert report2.inserted_office_cities == 0
    assert report2.skipped_office_cities == 2
