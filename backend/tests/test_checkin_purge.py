"""QR 訪問チェックイン Phase 5-6 のテスト — GPS 座標の保持パージ (2 年).

- 2 年超の checkin の lat/lng/accuracy_m を NULL 化し、distance_m / match_status は
  残す。2 年内の checkin は不変。
- 冪等 (再実行で 0 件)。
- 管理 API のロール (admin のみ)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit, VisitCheckin
from app.services.checkin.purge import run_gps_purge

JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_user(db, email: str, role: str, *, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_visit_with_patient_staff(db, code: str) -> tuple[Visit, Patient, Staff]:
    staff = Staff(name="担当")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    p = Patient(code=code, name="利用者", lat=35.0, lng=139.0)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    visit = Visit(
        patient_id=p.id,
        primary_staff_id=staff.id,
        visit_date=_today_jst(),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit, p, staff


async def _make_checkin(db, visit, patient, staff, *, scanned_at) -> VisitCheckin:
    c = VisitCheckin(
        visit_id=visit.id,
        patient_id=patient.id,
        staff_id=staff.id,
        kind="arrival",
        scanned_at=scanned_at,
        lat=Decimal("35.0000000"),
        lng=Decimal("139.0000000"),
        accuracy_m=Decimal("12.5"),
        distance_m=Decimal("123.4"),
        match_status="review",
        threshold_snapshot={"v": 1},
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@pytest.mark.asyncio
async def test_purge_nulls_old_gps_keeps_derived(db) -> None:
    visit, p, staff = await _make_visit_with_patient_staff(db, "PG-1")
    now = datetime.now(UTC)
    # 3 年前 (2 年超) → パージ対象。
    old = await _make_checkin(db, visit, p, staff, scanned_at=now - timedelta(days=365 * 3))
    # 1 年前 (2 年内) → 不変。
    recent = await _make_checkin(db, visit, p, staff, scanned_at=now - timedelta(days=365))

    result = await run_gps_purge(db, now=now)
    assert result["locked"] is False
    assert result["purged"] == 1

    await db.refresh(old)
    await db.refresh(recent)

    # 旧行: lat/lng/accuracy は NULL 化、distance/status は保持。
    assert old.lat is None
    assert old.lng is None
    assert old.accuracy_m is None
    assert old.distance_m == Decimal("123.4")
    assert old.match_status == "review"

    # 新しい行: 不変。
    assert recent.lat is not None
    assert recent.lng is not None
    assert recent.accuracy_m is not None


@pytest.mark.asyncio
async def test_purge_is_idempotent(db) -> None:
    visit, p, staff = await _make_visit_with_patient_staff(db, "PG-2")
    now = datetime.now(UTC)
    await _make_checkin(db, visit, p, staff, scanned_at=now - timedelta(days=365 * 3))

    first = await run_gps_purge(db, now=now)
    assert first["purged"] == 1
    # 再実行: 既に NULL のため 0 件。
    second = await run_gps_purge(db, now=now)
    assert second["purged"] == 0


@pytest.mark.asyncio
async def test_purge_rejects_retention_below_floor(db) -> None:
    """保持下限 (365 日) 未満の retention_days は ValueError (security L-1)."""
    visit, p, staff = await _make_visit_with_patient_staff(db, "PG-3")
    now = datetime.now(UTC)
    old = await _make_checkin(db, visit, p, staff, scanned_at=now - timedelta(days=365 * 3))

    with pytest.raises(ValueError):
        await run_gps_purge(db, retention_days=364, now=now)

    # ガードで弾かれ、行は一切パージされていない。
    await db.refresh(old)
    assert old.lat is not None


@pytest.mark.asyncio
async def test_purge_gps_api_requires_admin(client, db) -> None:
    staff = Staff(name="s")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    staff_user = await _make_user(db, "pg-staff@example.com", "staff", staff_id=staff.id)
    admin = await _make_user(db, "pg-admin@example.com", "admin")

    res_staff = await client.post("/api/v1/admin/checkin/purge-gps", headers=_bearer(staff_user))
    assert res_staff.status_code == 403, res_staff.text

    res_admin = await client.post("/api/v1/admin/checkin/purge-gps", headers=_bearer(admin))
    assert res_admin.status_code == 200, res_admin.text
    body = res_admin.json()
    assert set(body.keys()) == {"locked", "purged"}

    await db.rollback()
