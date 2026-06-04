"""Tests for POST /api/v1/schedule/v2/travel-estimate (Phase G-84 A-1).

移動時間推定 API: 現場ボードの空き枠直接配置 (案2) で「直前訪問から候補住所までの
移動時間」を既存ユーティリティ (proposal_solver の haversine / VISIT_BUFFER_MINUTES)
で推定して返す read-only endpoint.

検証内容:
    - from_patient_id → patients.lat/lng 解決 + to_lat/lng で移動時間算出.
    - lat/lng フォールバック (from_patient_id 無し).
    - to_address → geocode 経路 (geocode_address をモック).
    - geocode 失敗 → to 未確定 → minutes=null.
    - from / to どちらか未確定 → minutes=null.
    - 認証 (staff 403 / no-auth 401).

ローカル SQLite のみ (本番 DB 禁止).

座標メモ (test_propose_slots_api.py と整合):
    BASE   = (35.6000, 140.1000)
    NEAR   = (35.6010, 140.1010)  → BASE から ~0.14km (近接)
    FAR    = (35.6300, 140.1400)  → BASE から ~4.5km (遠い)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User
from app.services.scheduling.proposal_solver import VISIT_BUFFER_MINUTES

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str, lat: float | None, lng: float | None) -> Patient:
    p = Patient(code=code, name=f"P-{code}", status="active", lat=lat, lng=lng)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# from_patient_id 解決 + to_lat/lng
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travel_estimate_from_patient_id_resolves(client, db) -> None:
    admin = await _make_user(db, email="te-admin1@example.com", role="admin")
    prev = await _make_patient(db, code="TE-PREV", lat=BASE[0], lng=BASE[1])

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "from_patient_id": str(prev.id),
            "to_lat": FAR[0],
            "to_lng": FAR[1],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_resolved"] is True
    assert body["to_resolved"] is True
    assert body["buffer_minutes"] == VISIT_BUFFER_MINUTES
    assert isinstance(body["travel_minutes"], int)
    assert body["travel_minutes"] >= 1
    assert body["total_minutes"] == body["travel_minutes"] + VISIT_BUFFER_MINUTES


# ---------------------------------------------------------------------------
# lat/lng フォールバック (from_patient_id 無し)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travel_estimate_from_lat_lng_fallback(client, db) -> None:
    admin = await _make_user(db, email="te-admin2@example.com", role="admin")

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "from_lat": BASE[0],
            "from_lng": BASE[1],
            "to_lat": NEAR[0],
            "to_lng": NEAR[1],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_resolved"] is True
    assert body["to_resolved"] is True
    assert body["total_minutes"] is not None


@pytest.mark.asyncio
async def test_travel_estimate_patient_without_coords_falls_back_to_lat_lng(client, db) -> None:
    """from_patient_id が座標未登録 → from_lat/lng にフォールバックする。"""
    admin = await _make_user(db, email="te-admin3@example.com", role="admin")
    prev = await _make_patient(db, code="TE-NOCOORD", lat=None, lng=None)

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "from_patient_id": str(prev.id),
            "from_lat": BASE[0],
            "from_lng": BASE[1],
            "to_lat": FAR[0],
            "to_lng": FAR[1],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_resolved"] is True
    assert body["to_resolved"] is True
    assert body["total_minutes"] is not None


# ---------------------------------------------------------------------------
# to_address → geocode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travel_estimate_geocodes_to_address(client, db, monkeypatch) -> None:
    admin = await _make_user(db, email="te-admin4@example.com", role="admin")

    from app.services.geocoding.client import GeocodeResult

    async def _fake_geocode(address: str, **kwargs: Any) -> GeocodeResult:
        return GeocodeResult(
            lat=FAR[0],
            lng=FAR[1],
            formatted_address="千葉県千葉市…",
            place_id="fake",
            status="OK",
        )

    monkeypatch.setattr("app.api.v1.schedule_v2.geocode_address", _fake_geocode)

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "from_lat": BASE[0],
            "from_lng": BASE[1],
            "to_address": "千葉県千葉市中央区1-1",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["to_resolved"] is True
    assert body["total_minutes"] is not None


@pytest.mark.asyncio
async def test_travel_estimate_geocode_failure_returns_null(client, db, monkeypatch) -> None:
    """geocode 失敗 → to 未確定 → minutes=null。"""
    admin = await _make_user(db, email="te-admin5@example.com", role="admin")

    async def _fake_geocode_fail(address: str, **kwargs: Any):
        raise RuntimeError("geocode boom")

    monkeypatch.setattr("app.api.v1.schedule_v2.geocode_address", _fake_geocode_fail)

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "from_lat": BASE[0],
            "from_lng": BASE[1],
            "to_address": "ジオコード失敗住所",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_resolved"] is True
    assert body["to_resolved"] is False
    assert body["travel_minutes"] is None
    assert body["total_minutes"] is None
    assert body["buffer_minutes"] == VISIT_BUFFER_MINUTES


# ---------------------------------------------------------------------------
# どちらか未確定 → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travel_estimate_from_missing_returns_null(client, db) -> None:
    admin = await _make_user(db, email="te-admin6@example.com", role="admin")

    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(admin),
        json={
            "to_lat": FAR[0],
            "to_lng": FAR[1],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_resolved"] is False
    assert body["to_resolved"] is True
    assert body["travel_minutes"] is None
    assert body["total_minutes"] is None


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travel_estimate_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="te-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        headers=_bearer(staff_user),
        json={"from_lat": BASE[0], "from_lng": BASE[1], "to_lat": FAR[0], "to_lng": FAR[1]},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_travel_estimate_no_auth_unauthorized(client) -> None:
    res = await client.post(
        "/api/v1/schedule/v2/travel-estimate",
        json={"from_lat": BASE[0], "from_lng": BASE[1], "to_lat": FAR[0], "to_lng": FAR[1]},
    )
    assert res.status_code == 401, res.text
