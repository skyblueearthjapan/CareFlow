"""Tests for /api/v1/admin/geocoding/audit (Task #144 / Phase G-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.services.geocoding.client import GeocodeResult

AUDIT_PATH = "app.services.geocoding.audit.geocode_address"
ENDPOINT = "/api/v1/admin/geocoding/audit"


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, code: str, address: str, lat: float, lng: float) -> Patient:
    p = Patient(
        code=code,
        name=f"テスト{code}",
        sex="female",
        status="active",
        address=address,
        lat=lat,
        lng=lng,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@pytest.mark.asyncio
async def test_audit_corrects_drifted_lat_lng(client, db) -> None:
    """5km ずれた lat/lng が geocoding 結果で補正される + audit_log に記録."""
    admin = await _make_user(db, "audit-admin@example.com", "admin")
    # 千葉県内住所 + lat/lng は東京付近 (= 約 30-40km ずれ)
    p = await _make_patient(db, "P-AUD-1", "千葉県千葉市稲毛区園生町519-11", 35.6895, 139.6917)
    # Geocoding は正しい千葉座標を返す
    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=35.6471649,
            lng=140.1131102,
            formatted_address="日本、〒263-0051 千葉県千葉市稲毛区園生町519-11",
            place_id="place-1",
            status="OK",
        )
    )
    with patch(AUDIT_PATH, fake):
        res = await client.post(ENDPOINT, headers=_bearer(admin), json={"dry_run": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["locked"] is False
    assert body["checked"] == 1
    assert body["corrected"] == 1
    assert body["errors"] == 0
    assert len(body["details"]) == 1
    detail = body["details"][0]
    assert detail["code"] == "P-AUD-1"
    assert detail["drift_km"] > 30  # 東京 → 千葉 で 30km 以上
    assert detail["before"]["lat"] == pytest.approx(35.6895)
    assert detail["after"]["lat"] == pytest.approx(35.6471649)

    # DB 更新を確認
    await db.refresh(p)
    assert float(p.lat) == pytest.approx(35.6471649, abs=1e-5)
    assert float(p.lng) == pytest.approx(140.1131102, abs=1e-5)

    # audit_log 記録を確認
    logs = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "auto_geocode_correction",
                AuditLog.target_id == "P-AUD-1",
            )
        )
    ).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_user_id == admin.id
    assert log.before["lat"] == pytest.approx(35.6895)
    assert log.after["lat"] == pytest.approx(35.6471649)


@pytest.mark.asyncio
async def test_audit_skips_when_within_threshold(client, db) -> None:
    """100m 未満ズレ (= MIN_DRIFT_KM 以下) は skip し DB 更新も log も無し."""
    admin = await _make_user(db, "audit-admin2@example.com", "admin")
    p = await _make_patient(
        db, "P-AUD-2", "千葉県千葉市稲毛区園生町519-11", 35.6471649, 140.1131102
    )
    # Geocoding はほぼ同じ座標を返す (50m 程度のズレ)
    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=35.6472100,  # 約 5m 差
            lng=140.1131500,
            formatted_address="千葉県千葉市稲毛区園生町519-11",
            place_id="place-2",
            status="OK",
        )
    )
    with patch(AUDIT_PATH, fake):
        res = await client.post(ENDPOINT, headers=_bearer(admin), json={"dry_run": False})
    assert res.status_code == 200
    body = res.json()
    assert body["corrected"] == 0
    assert body["skipped_unchanged"] == 1

    # DB は変わらず
    await db.refresh(p)
    assert float(p.lat) == pytest.approx(35.6471649, abs=1e-5)

    # audit_log も無し
    logs = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "auto_geocode_correction",
                AuditLog.target_id == "P-AUD-2",
            )
        )
    ).all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_audit_dry_run_does_not_update(client, db) -> None:
    """dry_run=True の時は detail を返すが DB は変更しない."""
    admin = await _make_user(db, "audit-admin3@example.com", "admin")
    p = await _make_patient(db, "P-AUD-3", "千葉県千葉市", 35.6895, 139.6917)
    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=35.6471649,
            lng=140.1131102,
            formatted_address="千葉",
            place_id="place-3",
            status="OK",
        )
    )
    with patch(AUDIT_PATH, fake):
        res = await client.post(ENDPOINT, headers=_bearer(admin), json={"dry_run": True})
    assert res.status_code == 200
    body = res.json()
    assert body["corrected"] == 1
    assert len(body["details"]) == 1

    # DB は元のまま
    await db.refresh(p)
    assert float(p.lat) == pytest.approx(35.6895)

    # audit_log も書かれない
    logs = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "auto_geocode_correction",
                AuditLog.target_id == "P-AUD-3",
            )
        )
    ).all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_audit_requires_admin_role(client, db) -> None:
    """staff role では 403."""
    staff = await _make_user(db, "audit-staff@example.com", "staff")
    res = await client.post(ENDPOINT, headers=_bearer(staff), json={"dry_run": True})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_audit_no_auth_returns_401(client) -> None:
    res = await client.post(ENDPOINT, json={"dry_run": True})
    assert res.status_code == 401
