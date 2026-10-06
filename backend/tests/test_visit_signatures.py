"""サインで記録 (signature-checkin-design-2026-10-06 §4・§5-1) のテスト.

* migration 0090: CHECK に 'signature'・visit_signatures・往復 (SQLite)。
* ``POST /visits/{id}/checkout-signature``: 記録できる (退出・サイン・画像)・
  ``client_id`` の再送は 1 件・画像でないもの / 大きすぎるもの・位置の不一致でも記録する・
  担当外は 404。
* ``GET /visit-signatures/{id}/image``: 職員も過去の日・担当外を見られる・見るたびに
  ``audit_logs``・消した後は 410。
* ``POST /admin/visit-signatures/purge-images``: 5 年を過ぎた画像だけ消す・下限・admin のみ。
* 打刻履歴の備考「QRなし（サイン）」「サイン」・Excel・A4 の注記・訪問モニター。
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, inspect, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models import (
    AuditLog,
    Patient,
    Staff,
    User,
    Visit,
    VisitCheckin,
    VisitSignature,
    VisitStaffAssignment,
)

JST = ZoneInfo("Asia/Tokyo")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _today() -> date:
    return datetime.now(JST).date()


@pytest.fixture
def sig_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "visit_signatures"
    monkeypatch.setenv("VISIT_SIGNATURES_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _staff_user(db, email: str, name: str = "担当") -> tuple[Staff, User]:
    staff = Staff(name=name)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = User(email=email, password_hash=hash_password("x"), role="staff", staff_id=staff.id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return staff, user


async def _admin(db, email: str = "sig-admin@example.com") -> User:
    user = User(email=email, password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _visit(db, staff: Staff, code: str, *, visit_date: date | None = None) -> Visit:
    patient = Patient(code=code, name=f"利用者{code}", lat=35.0, lng=139.0)
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=visit_date or _today(),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


def _form(**extra: str) -> dict[str, str]:
    return {"lat": "35.0", "lng": "139.0", "accuracy": "10", **extra}


async def _sign(client, user: User, visit: Visit, *, image: bytes = PNG, **form: str):
    return await client.post(
        f"/api/v1/visits/{visit.id}/checkout-signature",
        headers=_bearer(user),
        data=_form(**form),
        files={"image": ("signature.png", image, "image/png")},
    )


# ---------------------------------------------------------------------------
# migration 0090
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_migration() -> object:
    path = _BACKEND_ROOT / "alembic" / "versions" / "0090_visit_signatures.py"
    spec = importlib.util.spec_from_file_location("migration_0090", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0090"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_migration_0090_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0090_visit_signatures")
    assert rev is not None
    assert rev.down_revision == "0089_multi_office_settings"
    assert len(list(script.get_heads())) == 1
    assert len("0090_visit_signatures") <= 32


_CREATE_CHECKINS = """
CREATE TABLE visit_checkins (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    visit_id VARCHAR(36) NOT NULL,
    kind VARCHAR(12) NOT NULL,
    checkin_source VARCHAR(12) NOT NULL DEFAULT 'qr',
    CONSTRAINT ck_visit_checkins_ck_visit_checkins_kind
        CHECK (kind IN ('arrival','departure','no_show')),
    CONSTRAINT ck_visit_checkins_ck_visit_checkins_checkin_source
        CHECK (checkin_source IN ('qr','manual'))
)
"""


def test_migration_0090_sqlite_roundtrip(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0090.db'}")
    with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE visits (id VARCHAR(36) NOT NULL PRIMARY KEY)",
            "CREATE TABLE users (id VARCHAR(36) NOT NULL PRIMARY KEY)",
            "CREATE TABLE staff (id VARCHAR(36) NOT NULL PRIMARY KEY)",
            _CREATE_CHECKINS,
        ):
            conn.execute(sa.text(ddl))
        conn.execute(sa.text("INSERT INTO visit_checkins VALUES ('c0', 'v1', 'arrival', 'qr')"))
    migration = _load_migration()

    def run(step) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()

    insert = "INSERT INTO visit_checkins VALUES ('{id}', 'v1', 'departure', '{src}')"
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(insert.format(id="c1", src="signature")))

    run(migration.upgrade)
    insp = inspect(engine)
    assert {c["name"] for c in insp.get_columns("visit_signatures")} == {
        "id",
        "visit_id",
        "checkin_id",
        "image_path",
        "image_mime",
        "image_bytes",
        "sha256",
        "device_time",
        "created_by_user_id",
        "created_by_staff_id",
        "client_id",
        "image_deleted_at",
        "created_at",
        "updated_at",
    }
    # 既存の行と、もう一方の CHECK (kind) は残る。
    with engine.begin() as conn:
        conn.execute(sa.text(insert.format(id="c2", src="signature")))
        assert conn.execute(sa.text("SELECT count(*) FROM visit_checkins")).scalar() == 2
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(insert.format(id="c3", src="other")))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO visit_checkins VALUES ('c4', 'v1', 'bogus', 'qr')"))
    sig = (
        "INSERT INTO visit_signatures (id, visit_id, checkin_id, image_mime, sha256, client_id) "
        "VALUES ('{id}', 'v1', '{checkin}', 'image/png', 'h', {client})"
    )
    with engine.begin() as conn:
        conn.execute(sa.text(sig.format(id="s1", checkin="c2", client="'k1'")))
        conn.execute(sa.text(sig.format(id="s2", checkin="c0", client="NULL")))
    # 打刻 1 行に画像 1 枚・client_id は重ならない。
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(sig.format(id="s3", checkin="c2", client="NULL")))

    run(migration.downgrade)
    assert "visit_signatures" not in inspect(engine).get_table_names()
    with engine.begin() as conn:
        # サインの退出は「QRなし」に戻る。
        assert (
            conn.execute(
                sa.text("SELECT checkin_source FROM visit_checkins WHERE id = 'c2'")
            ).scalar()
            == "manual"
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(insert.format(id="c5", src="signature")))
    run(migration.upgrade)
    assert "visit_signatures" in inspect(engine).get_table_names()
    engine.dispose()


# ---------------------------------------------------------------------------
# POST /visits/{id}/checkout-signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_signature_records_departure_and_image(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-1@example.com")
    visit = await _visit(db, staff, "SIG-1")
    # 到着は「到着を記録」= QR なし (manual)。
    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text

    device_time = datetime.now(UTC).replace(microsecond=0).isoformat()
    res = await _sign(client, user, visit, at=device_time)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["latest_checkin"]["kind"] == "departure"
    assert body["latest_checkin"]["checkin_source"] == "signature"
    assert body["latest_checkin"]["match_status"] == "match"
    assert body["actual_arrival_source"] == "manual"
    assert body["actual_departure_source"] == "signature"
    assert body["departure_signature_id"] is not None
    # 時刻を合わせる枠は、サインの退出でも使える (到着の打刻がある)。
    assert body["actual_adjust_allowed"] is True

    row = await db.scalar(select(VisitSignature).where(VisitSignature.visit_id == visit.id))
    assert str(row.id) == body["departure_signature_id"]
    assert row.image_mime == "image/png"
    assert row.image_bytes == len(PNG)
    assert len(row.sha256) == 64
    assert row.created_by_staff_id == staff.id
    path = Path(row.image_path)
    assert path.read_bytes() == PNG
    assert path.parent.parent.parent == sig_dir
    assert not list(sig_dir.rglob("*.part"))
    checkin = await db.get(VisitCheckin, row.checkin_id)
    assert (checkin.kind, checkin.checkin_source) == ("departure", "signature")
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_accepts_jpeg(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-jpg@example.com")
    visit = await _visit(db, staff, "SIG-JPG")
    res = await _sign(client, user, visit, image=JPEG)
    assert res.status_code == 200, res.text
    row = await db.scalar(select(VisitSignature).where(VisitSignature.visit_id == visit.id))
    assert row.image_mime == "image/jpeg"
    assert row.image_path.endswith(".jpg")
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_is_idempotent_by_client_id(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-idem@example.com")
    visit = await _visit(db, staff, "SIG-IDEM")
    client_id = str(uuid4())
    first = await _sign(client, user, visit, client_id=client_id)
    second = await _sign(client, user, visit, client_id=client_id)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["departure_signature_id"] == second.json()["departure_signature_id"]
    assert (
        await db.scalar(
            select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit.id)
        )
        == 1
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(VisitSignature)
            .where(VisitSignature.visit_id == visit.id)
        )
        == 1
    )
    assert len(list(sig_dir.rglob("*.png"))) == 1

    # 同じ client_id を別の訪問で使うと 409 (別の記録を返さない)。
    other = await _visit(db, staff, "SIG-IDEM2")
    res = await _sign(client, user, other, client_id=client_id)
    assert res.status_code == 409, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_same_client_id_other_staff_is_409(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-cid-a@example.com", "担当 A")
    visit = await _visit(db, staff, "SIG-CID")
    other, other_user = await _staff_user(db, "sig-cid-b@example.com", "担当 B")
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=other.id))
    await db.commit()
    client_id = str(uuid4())
    assert (await _sign(client, user, visit, client_id=client_id)).status_code == 200
    # 同じ訪問でも、別の人が同じ client_id を名乗ったら 409 (他人の記録を返さない)。
    res = await _sign(client, other_user, visit, client_id=client_id)
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "この記録は別の訪問で使われています"
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_concurrent_resend_is_deduped_on_unique(
    client, db, sig_dir, monkeypatch
) -> None:
    """事前の client_id の確認をすり抜けた並走の再送: UNIQUE で弾き、打刻ごと巻き戻して
    先の記録を返す (打刻 1 件・画像 1 枚のまま)。"""
    from app.api.v1 import visits as visits_api

    staff, user = await _staff_user(db, "sig-race@example.com")
    visit = await _visit(db, staff, "SIG-RACE")
    client_id = str(uuid4())
    first = await _sign(client, user, visit, client_id=client_id)
    assert first.status_code == 200, first.text

    real_lookup = visits_api._signature_by_client_id
    calls = {"n": 0}

    async def _lookup(session, cid):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 並走した 2 本目には、まだ 1 本目が見えていなかった
        return await real_lookup(session, cid)

    monkeypatch.setattr(visits_api, "_signature_by_client_id", _lookup)
    second = await _sign(client, user, visit, client_id=client_id)
    assert second.status_code == 200, second.text
    assert calls["n"] == 2
    assert second.json()["departure_signature_id"] == first.json()["departure_signature_id"]
    checkins = await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit.id)
    )
    signatures = await db.scalar(
        select(func.count()).select_from(VisitSignature).where(VisitSignature.visit_id == visit.id)
    )
    assert (checkins, signatures) == (1, 1)
    # 2 本目が書いた画像は消してある。
    assert len(list(sig_dir.rglob("*.png"))) == 1
    assert not list(sig_dir.rglob("*.part"))
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_oversized_request_is_413_by_content_length(
    client, db, sig_dir, monkeypatch
) -> None:
    monkeypatch.setenv("VISIT_SIGNATURE_MAX_BYTES", "1024")
    get_settings.cache_clear()
    staff, user = await _staff_user(db, "sig-cl@example.com")
    visit = await _visit(db, staff, "SIG-CL")
    # 画像 1 KiB の上限 + 64 KiB の余白を超えるリクエスト。
    res = await _sign(client, user, visit, image=PNG + bytes(70 * 1024))
    assert res.status_code == 413, res.text
    assert res.json()["detail"] == "サインの画像が大きすぎます（1 KB まで）"
    count = await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit.id)
    )
    assert count == 0
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_rejects_non_image_and_records_nothing(
    client, db, sig_dir
) -> None:
    staff, user = await _staff_user(db, "sig-bad@example.com")
    visit = await _visit(db, staff, "SIG-BAD")
    res = await _sign(client, user, visit, image=b"<svg>not a png</svg>")
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "サインの画像は PNG か JPEG で送ってください"
    res = await _sign(client, user, visit, image=b"")
    assert res.status_code == 422, res.text
    res = await _sign(client, user, visit, lat="999")
    assert res.status_code == 422, res.text
    res = await _sign(client, user, visit, client_id="not-a-uuid")
    assert res.status_code == 422, res.text
    count = await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit.id)
    )
    assert count == 0
    assert not sig_dir.exists() or not list(sig_dir.rglob("*.*"))
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_too_large_is_413(client, db, sig_dir, monkeypatch) -> None:
    monkeypatch.setenv("VISIT_SIGNATURE_MAX_BYTES", "32")
    get_settings.cache_clear()
    staff, user = await _staff_user(db, "sig-big@example.com")
    visit = await _visit(db, staff, "SIG-BIG")
    res = await _sign(client, user, visit)
    assert res.status_code == 413, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_far_away_is_recorded_as_mismatch(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-far@example.com")
    visit = await _visit(db, staff, "SIG-FAR")
    res = await _sign(
        client, user, visit, lat="35.05", lng="139.0", is_override="true", reason="玄関先で"
    )
    assert res.status_code == 200, res.text
    latest = res.json()["latest_checkin"]
    assert latest["match_status"] == "mismatch"
    assert latest["is_override"] is True
    assert latest["reason"] == "玄関先で"
    assert latest["checkin_source"] == "signature"
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_not_assigned_is_404(client, db, sig_dir) -> None:
    owner, _owner_user = await _staff_user(db, "sig-owner@example.com", "担当")
    _other, other_user = await _staff_user(db, "sig-other@example.com", "担当外")
    visit = await _visit(db, owner, "SIG-404")
    res = await _sign(client, other_user, visit)
    assert res.status_code == 404, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_signature_other_day_is_409(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-day@example.com")
    visit = await _visit(db, staff, "SIG-DAY", visit_date=_today() + timedelta(days=1))
    res = await _sign(client, user, visit)
    assert res.status_code == 409, res.text
    assert not sig_dir.exists() or not list(sig_dir.rglob("*.*"))
    await db.rollback()


# ---------------------------------------------------------------------------
# GET /visit-signatures/{id}/image
# ---------------------------------------------------------------------------


async def _seed_signature(
    db,
    sig_dir: Path,
    code: str,
    *,
    created_at: datetime | None = None,
    visit_date: date | None = None,
) -> str:
    """サインの退出と画像を DB に直接置く (API を通さない = テストの DB 操作と API の
    書き込みを交互にしない)。すべて commit してから ID を返す。"""
    owner, _owner_user = await _staff_user(db, f"{code.lower()}-owner@example.com")
    visit = await _visit(db, owner, code, visit_date=visit_date)
    when = created_at or datetime.now(UTC)
    checkin = VisitCheckin(
        visit_id=visit.id,
        patient_id=visit.patient_id,
        staff_id=owner.id,
        kind="departure",
        scanned_at=when,
        match_status="match",
        threshold_snapshot={"v": 1},
        checkin_source="signature",
    )
    db.add(checkin)
    await db.flush()
    path = sig_dir / f"{when:%Y}" / f"{when:%m}" / f"{code}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    signature = VisitSignature(
        visit_id=visit.id,
        checkin_id=checkin.id,
        image_path=str(path),
        image_mime="image/png",
        image_bytes=len(PNG),
        sha256="0" * 64,
        created_at=when,
    )
    db.add(signature)
    await db.commit()
    return str(signature.id)


async def _fresh_signature(db, sig_id: str) -> VisitSignature:
    """API が書いた後の行を、セッションの記憶ではなく DB から読み直す。"""
    return (
        await db.scalars(
            select(VisitSignature)
            .where(VisitSignature.id == UUID(sig_id))
            .execution_options(populate_existing=True)
        )
    ).one()


@pytest.mark.asyncio
async def test_any_staff_can_view_past_signature_and_each_read_is_audited(
    client, db, sig_dir
) -> None:
    # 過去の日 (400 日前) の訪問のサイン (期間の制限なし)。
    sig_id = await _seed_signature(
        db, sig_dir, "SIG-VIEW", visit_date=_today() - timedelta(days=400)
    )
    _other, other_user = await _staff_user(db, "sig-viewer@example.com", "担当外")
    admin = await _admin(db)
    other_id, admin_id = other_user.id, admin.id
    await db.commit()

    for viewer in (other_user, admin):
        res = await client.get(f"/api/v1/visit-signatures/{sig_id}/image", headers=_bearer(viewer))
        assert res.status_code == 200, res.text
        assert res.content == PNG
        assert res.headers["content-type"] == "image/png"
        assert res.headers["cache-control"] == "no-store"
    reads = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "signature_read", AuditLog.target_id == sig_id
            )
        )
    ).all()
    assert {r.actor_user_id for r in reads} == {other_id, admin_id}
    assert all(r.target_table == "visit_signatures" for r in reads)

    res = await client.get(f"/api/v1/visit-signatures/{uuid4()}/image", headers=_bearer(admin))
    assert res.status_code == 404
    res = await client.get(f"/api/v1/visit-signatures/{sig_id}/image")
    assert res.status_code == 401
    await db.rollback()


@pytest.mark.asyncio
async def test_view_is_refused_when_the_read_cannot_be_audited(
    client, db, sig_dir, monkeypatch
) -> None:
    """見た記録を残せなければ画像を渡さない (fail closed)。"""
    from app.api.v1 import visit_signatures as api

    sig_id = await _seed_signature(db, sig_dir, "SIG-NOAUDIT")
    admin = await _admin(db, "sig-noaudit@example.com")
    await db.commit()

    async def _fail(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(api, "_audit_signature_read", _fail)
    res = await client.get(f"/api/v1/visit-signatures/{sig_id}/image", headers=_bearer(admin))
    assert res.status_code == 503
    assert res.json()["detail"] == api.DETAIL_AUDIT_FAILED
    await db.rollback()


# ---------------------------------------------------------------------------
# POST /admin/visit-signatures/purge-images
# ---------------------------------------------------------------------------

PURGE_URL = "/api/v1/admin/visit-signatures/purge-images"


@pytest.mark.asyncio
async def test_purge_removes_only_images_older_than_retention(client, db, sig_dir) -> None:
    old_id = await _seed_signature(
        db, sig_dir, "SIG-OLD", created_at=datetime.now(UTC) - timedelta(days=1826)
    )
    new_id = await _seed_signature(db, sig_dir, "SIG-NEW")
    old_path = Path((await _fresh_signature(db, old_id)).image_path)
    new_path = Path((await _fresh_signature(db, new_id)).image_path)
    admin = await _admin(db, "sig-purge-1@example.com")
    await db.commit()

    res = await client.post(PURGE_URL, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json() == {"locked": False, "purged": 1}
    old = await _fresh_signature(db, old_id)
    assert old.image_path is None
    assert old.image_deleted_at is not None
    assert old.sha256  # 記録は残る
    assert not old_path.exists()
    new = await _fresh_signature(db, new_id)
    assert new.image_deleted_at is None
    assert new_path.exists()
    # 打刻 (退出) は残る。
    remaining = await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.id == old.checkin_id)
    )
    assert remaining == 1
    await db.commit()

    res = await client.get(f"/api/v1/visit-signatures/{old_id}/image", headers=_bearer(admin))
    assert res.status_code == 410
    # 冪等。
    res = await client.post(PURGE_URL, headers=_bearer(admin))
    assert res.json() == {"locked": False, "purged": 0}
    await db.rollback()


@pytest.mark.asyncio
async def test_purge_leaves_row_for_retry_when_file_cannot_be_deleted(
    client, db, sig_dir, monkeypatch
) -> None:
    old_id = await _seed_signature(
        db, sig_dir, "SIG-STUCK", created_at=datetime.now(UTC) - timedelta(days=1900)
    )
    admin = await _admin(db, "sig-purge-2@example.com")
    await db.commit()
    real_unlink = Path.unlink

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "SIG-STUCK.png":
            raise PermissionError("busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _unlink)
    res = await client.post(PURGE_URL, headers=_bearer(admin))
    assert res.json() == {"locked": False, "purged": 0}
    row = await _fresh_signature(db, old_id)
    assert row.image_deleted_at is None
    assert row.image_path is not None
    await db.commit()

    # 次の実行で消せれば「消した」になる。
    monkeypatch.setattr(Path, "unlink", real_unlink)
    res = await client.post(PURGE_URL, headers=_bearer(admin))
    assert res.json() == {"locked": False, "purged": 1}
    assert (await _fresh_signature(db, old_id)).image_deleted_at is not None
    await db.rollback()


@pytest.mark.asyncio
async def test_purge_rejects_short_retention_and_non_admin(
    client, db, sig_dir, monkeypatch
) -> None:
    _staff, staff_user = await _staff_user(db, "sig-purge-staff@example.com")
    admin = await _admin(db, "sig-purge-admin@example.com")
    await db.commit()
    res = await client.post(PURGE_URL, headers=_bearer(staff_user))
    assert res.status_code == 403
    monkeypatch.setenv("VISIT_SIGNATURE_RETENTION_DAYS", "30")
    get_settings.cache_clear()
    res = await client.post(PURGE_URL, headers=_bearer(admin))
    assert res.status_code == 422
    await db.rollback()


# ---------------------------------------------------------------------------
# 打刻履歴・Excel・A4・訪問モニター
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_remarks_xlsx_report_and_monitor_show_signature(client, db, sig_dir) -> None:
    staff, user = await _staff_user(db, "sig-hist@example.com", "サイン 花子")
    signed = await _visit(db, staff, "SIG-H1")
    plain = await _visit(db, staff, "SIG-H2")
    for v in (signed, plain):
        res = await client.post(
            f"/api/v1/visits/{v.id}/checkin",
            headers=_bearer(user),
            json={"lat": 35.0, "lng": 139.0},
        )
        assert res.status_code == 200, res.text
    res = await _sign(client, user, signed)
    assert res.status_code == 200, res.text
    sig_id = res.json()["departure_signature_id"]
    res = await client.post(
        f"/api/v1/visits/{plain.id}/checkout", headers=_bearer(user), json={"lat": 35.0}
    )
    assert res.status_code == 200, res.text
    admin = await _admin(db, "sig-hist-admin@example.com")
    params = {"from": _today().isoformat(), "to": _today().isoformat()}

    res = await client.get("/api/v1/visit-history", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    rows = {r["visit_id"]: r for r in res.json()["items"]}
    signed_row = rows[str(signed.id)]
    assert "QRなし（サイン）" in signed_row["remarks"]
    assert "サイン" in signed_row["remarks"]
    assert "QRなし" not in signed_row["remarks"]
    assert signed_row["departure_source"] == "signature"
    assert signed_row["signature_id"] == sig_id
    plain_row = rows[str(plain.id)]
    assert "QRなし" in plain_row["remarks"]
    assert "サイン" not in plain_row["remarks"]
    assert plain_row["signature_id"] is None

    res = await client.get("/api/v1/visit-history/export", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    wb = load_workbook(BytesIO(res.content))
    remarks = [row[-1] for row in wb["QR読み取りあり"].iter_rows(min_row=2, values_only=True)]
    assert any(r and "サイン" in r for r in remarks)
    notes = "\n".join(str(c) for row in wb["読み方"].iter_rows(values_only=True) for c in row)
    assert "「サイン」は" in notes

    res = await client.get(
        "/api/v1/visit-history/report",
        headers=_bearer(admin),
        params={**params, "format": "html"},
    )
    assert res.status_code == 200, res.text
    visible = res.text.split("<script>")[0]
    footer = visible.split("<footer>")[1].split("</footer>")[0]
    assert "到着・退出は QR・サインの時刻です" in footer
    assert "QRなし（サイン）、サイン" in visible
    # 画像は載せない。
    assert "visit-signatures" not in visible

    res = await client.get(
        "/api/v1/monitor", headers=_bearer(admin), params={"date": _today().isoformat()}
    )
    assert res.status_code == 200, res.text
    visits = {v["visit_id"]: v for row in res.json()["staff"] for v in row["visits"]}
    assert visits[str(signed.id)]["departure"]["checkin_source"] == "signature"
    assert visits[str(signed.id)]["departure"]["signature_id"] == sig_id
    assert visits[str(signed.id)]["arrival"]["checkin_source"] == "manual"
    assert visits[str(plain.id)]["departure"]["signature_id"] is None
    await db.rollback()
