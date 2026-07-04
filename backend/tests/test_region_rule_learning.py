"""W-7 地域ルールの学習 BE テスト.

設計書 `docs/plans/region-rule-learning-design.md` §2-3, §5。

検証対象:
  - POST /offices/resolve の W-7 拡張 (matched_city / prompt_dismissed)
  - POST /offices/{office_id}/area-cities (additive・冪等・却下記憶の自動削除・RBAC)
  - POST /offices/area-prompt-dismissals (却下記憶・冪等・RBAC)
  - migration 0053 (単一 head・SQLite roundtrip・UNIQUE)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from app.core.security import create_access_token, hash_password
from app.models import City, Office, OfficeAreaPromptDismissal, OfficeCity, User

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_cities(db) -> dict[str, City]:
    rows = {
        "inage": City(prefecture="千葉県", name="千葉市稲毛区", jis_code="12103"),
        "wakaba": City(prefecture="千葉県", name="千葉市若葉区", jis_code="12104"),
        "chiyoda": City(prefecture="東京都", name="千代田区", jis_code="13101"),
    }
    for c in rows.values():
        db.add(c)
    await db.commit()
    for c in rows.values():
        await db.refresh(c)
    return rows


async def _seed_offices(db, cities: dict[str, City]) -> dict[str, Office]:
    """稲毛 (担当: 稲毛区) のみ紐付ける。千代田区は未カバーのまま。"""
    inage = Office(code="INAGE", name="稲毛", prefecture="千葉県")
    tsuga = Office(code="TSUGA", name="都賀", prefecture="千葉県")
    db.add_all([inage, tsuga])
    await db.commit()
    await db.refresh(inage)
    await db.refresh(tsuga)

    db.add(OfficeCity(office_id=inage.id, city_id=cities["inage"].id))
    await db.commit()
    return {"inage": inage, "tsuga": tsuga}


async def _make_user(db, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# resolve W-7 拡張
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_uncovered_returns_matched_city(client, db) -> None:
    """未カバー地域 (千代田区) で matched_city が返り prompt_dismissed=false."""
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-a1@example.com", "admin")

    res = await client.post(
        "/api/v1/offices/resolve",
        headers=_bearer(admin),
        json={"address": "東京都千代田区丸の内1-1"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confidence"] == "none"
    assert body["office_id"] is None
    assert body["matched_city"] is not None
    assert body["matched_city"]["id"] == str(cities["chiyoda"].id)
    assert body["matched_city"]["name"] == "千代田区"
    assert body["matched_city"]["prefecture"] == "東京都"
    assert body["prompt_dismissed"] is False


@pytest.mark.asyncio
async def test_resolve_uncovered_dismissed_sets_flag(client, db) -> None:
    """却下記憶がある City では prompt_dismissed=true."""
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)
    db.add(OfficeAreaPromptDismissal(city_id=cities["chiyoda"].id))
    await db.commit()
    admin = await _make_user(db, "w7-a2@example.com", "admin")

    res = await client.post(
        "/api/v1/offices/resolve",
        headers=_bearer(admin),
        json={"address": "東京都千代田区丸の内1-1"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confidence"] == "none"
    assert body["matched_city"]["id"] == str(cities["chiyoda"].id)
    assert body["prompt_dismissed"] is True


@pytest.mark.asyncio
async def test_resolve_covered_area_response_unchanged(client, db) -> None:
    """カバー済み地域 (稲毛区) では matched_city=None / prompt_dismissed=False (後方互換)."""
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-a3@example.com", "admin")

    res = await client.post(
        "/api/v1/offices/resolve",
        headers=_bearer(admin),
        json={"address": "千葉県千葉市稲毛区稲毛東1丁目"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confidence"] == "exact"
    assert body["office_id"] == str(offices["inage"].id)
    # 従来フィールドは不変。W-7 フィールドは非活性。
    assert body["matched_city_id"] == str(cities["inage"].id)
    assert body["matched_city"] is None
    assert body["prompt_dismissed"] is False


@pytest.mark.asyncio
async def test_resolve_unknown_city_no_matched_city(client, db) -> None:
    """City を特定できない住所では matched_city=None."""
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-a4@example.com", "admin")

    res = await client.post(
        "/api/v1/offices/resolve",
        headers=_bearer(admin),
        json={"address": "北海道札幌市中央区北1条"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confidence"] == "none"
    assert body["matched_city_id"] is None
    assert body["matched_city"] is None
    assert body["prompt_dismissed"] is False


# ---------------------------------------------------------------------------
# POST /offices/{office_id}/area-cities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_area_cities_add_success(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-ac1@example.com", "admin")

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        headers=_bearer(admin),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["office_id"] == str(offices["inage"].id)
    assert body["city_id"] == str(cities["chiyoda"].id)
    assert body["city_name"] == "千代田区"

    link = await db.scalar(
        select(OfficeCity).where(
            OfficeCity.office_id == offices["inage"].id,
            OfficeCity.city_id == cities["chiyoda"].id,
        )
    )
    assert link is not None


@pytest.mark.asyncio
async def test_area_cities_add_idempotent(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-ac2@example.com", "admin")
    url = f"/api/v1/offices/{offices['inage'].id}/area-cities"
    payload = {"city_id": str(cities["chiyoda"].id)}

    r1 = await client.post(url, headers=_bearer(admin), json=payload)
    r2 = await client.post(url, headers=_bearer(admin), json=payload)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    count = await db.scalar(
        select(sa.func.count())
        .select_from(OfficeCity)
        .where(
            OfficeCity.office_id == offices["inage"].id,
            OfficeCity.city_id == cities["chiyoda"].id,
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_area_cities_allowed_when_other_office_covers(client, db) -> None:
    """別拠点が既に担当している City でも追加できる (複数拠点担当は合法)."""
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-ac3@example.com", "admin")

    # 稲毛区は inage が既に担当。tsuga にも同じ稲毛区を追加する。
    res = await client.post(
        f"/api/v1/offices/{offices['tsuga'].id}/area-cities",
        headers=_bearer(admin),
        json={"city_id": str(cities["inage"].id)},
    )
    assert res.status_code == 200, res.text

    count = await db.scalar(
        select(sa.func.count())
        .select_from(OfficeCity)
        .where(OfficeCity.city_id == cities["inage"].id)
    )
    assert count == 2


@pytest.mark.asyncio
async def test_area_cities_add_deletes_dismissal(client, db) -> None:
    """追加成功時に該当 City の却下記憶が自動削除される."""
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    db.add(OfficeAreaPromptDismissal(city_id=cities["chiyoda"].id))
    await db.commit()
    admin = await _make_user(db, "w7-ac4@example.com", "admin")

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        headers=_bearer(admin),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 200, res.text

    remaining = await db.scalar(
        select(OfficeAreaPromptDismissal).where(
            OfficeAreaPromptDismissal.city_id == cities["chiyoda"].id
        )
    )
    assert remaining is None


@pytest.mark.asyncio
async def test_area_cities_unknown_office_404(client, db) -> None:
    cities = await _seed_cities(db)
    await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-ac5@example.com", "admin")

    res = await client.post(
        f"/api/v1/offices/{uuid4()}/area-cities",
        headers=_bearer(admin),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_area_cities_unknown_city_404(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    admin = await _make_user(db, "w7-ac6@example.com", "admin")

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        headers=_bearer(admin),
        json={"city_id": str(uuid4())},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_area_cities_staff_403(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    staff = await _make_user(db, "w7-ac7@example.com", "staff")

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        headers=_bearer(staff),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_area_cities_no_token_401(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_area_cities_manager_allowed(client, db) -> None:
    cities = await _seed_cities(db)
    offices = await _seed_offices(db, cities)
    manager = await _make_user(db, "w7-ac8@example.com", "manager")

    res = await client.post(
        f"/api/v1/offices/{offices['inage'].id}/area-cities",
        headers=_bearer(manager),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# POST /offices/area-prompt-dismissals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismissals_create_204(client, db) -> None:
    cities = await _seed_cities(db)
    admin = await _make_user(db, "w7-d1@example.com", "admin")

    res = await client.post(
        "/api/v1/offices/area-prompt-dismissals",
        headers=_bearer(admin),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 204, res.text

    row = await db.scalar(
        select(OfficeAreaPromptDismissal).where(
            OfficeAreaPromptDismissal.city_id == cities["chiyoda"].id
        )
    )
    assert row is not None


@pytest.mark.asyncio
async def test_dismissals_idempotent_204(client, db) -> None:
    cities = await _seed_cities(db)
    admin = await _make_user(db, "w7-d2@example.com", "admin")
    url = "/api/v1/offices/area-prompt-dismissals"
    payload = {"city_id": str(cities["chiyoda"].id)}

    r1 = await client.post(url, headers=_bearer(admin), json=payload)
    r2 = await client.post(url, headers=_bearer(admin), json=payload)
    assert r1.status_code == 204, r1.text
    assert r2.status_code == 204, r2.text

    count = await db.scalar(
        select(sa.func.count())
        .select_from(OfficeAreaPromptDismissal)
        .where(OfficeAreaPromptDismissal.city_id == cities["chiyoda"].id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_dismissals_staff_403(client, db) -> None:
    cities = await _seed_cities(db)
    staff = await _make_user(db, "w7-d3@example.com", "staff")

    res = await client.post(
        "/api/v1/offices/area-prompt-dismissals",
        headers=_bearer(staff),
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_dismissals_no_token_401(client, db) -> None:
    cities = await _seed_cities(db)

    res = await client.post(
        "/api/v1/offices/area-prompt-dismissals",
        json={"city_id": str(cities["chiyoda"].id)},
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# migration 0053
# ---------------------------------------------------------------------------


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / "0053_office_area_prompt_dismissals.py"
    spec = importlib.util.spec_from_file_location("migration_0053", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0053"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_0053_revision_chain(alembic_cfg: Config) -> None:
    """0053 が 0052 から派生し、head が単一であること (ヘッド名は固定しない)."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0053_office_area_prompt_dismissals")
    assert rev is not None
    assert rev.down_revision == "0052_schedule_op_log", (
        f"0053 の down_revision は 0052 のはず, got {rev.down_revision}"
    )
    heads = list(script.get_heads())
    assert len(heads) == 1, f"head は単一のはず, got {heads}"


def test_migration_0053_sqlite_roundtrip(tmp_path: Path) -> None:
    """SQLite で upgrade / downgrade を往復し、テーブル + UNIQUE を確認."""
    db_path = tmp_path / "migration_0053.db"
    engine = create_engine(f"sqlite:///{db_path}")

    migration_mod = _load_migration_module()

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("office_area_prompt_dismissals")}
    assert cols == {"id", "city_id", "created_at"}, cols

    # UNIQUE(city_id) が機能する (同 city_id の 2 行目は拒否).
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO office_area_prompt_dismissals (id, city_id) VALUES ('r1', 'c1')")
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO office_area_prompt_dismissals (id, city_id) VALUES ('r2', 'c1')"
                )
            )

    # downgrade: テーブル削除
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]
    insp2 = inspect(engine)
    assert "office_area_prompt_dismissals" not in insp2.get_table_names()

    engine.dispose()
