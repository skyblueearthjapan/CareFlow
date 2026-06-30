"""QR 訪問チェックイン Phase 4 — しきい値設定 API + monitor 連動のテスト.

- GET /checkin-settings: 行無しはコード既定 (6 値) + is_default 全 true (admin/manager)。
- PUT /checkin-settings: upsert / 部分更新 / null リセット / range 422 / review<match 422。
- RBAC: GET/PUT は staff 403、public は staff 200 (全ログイン可)、未認証 401。
- GET /checkin-settings/public: 距離系 (match/review/accuracy) のみ返す (時間系は出さない)。
- monitor: 退出忘れ判定が checkin_settings.max_inprogress_min を読む (ハードコード解消)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit, VisitCheckin
from app.models.checkin_settings import CheckinSettings
from app.services.checkin.judge import DEFAULT_THRESHOLDS
from app.services.checkin.monitor import ALERT_NONE, ALERT_REVIEW, PHASE_INPROGRESS, build_monitor

JST = ZoneInfo("Asia/Tokyo")
TARGET = date(2026, 6, 30)


def _utc(h: int, mi: int) -> datetime:
    return datetime(TARGET.year, TARGET.month, TARGET.day, h, mi, tzinfo=JST).astimezone(UTC)


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET — 既定値 (行無し)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_code_defaults_when_no_row(client, db) -> None:
    admin = await _make_user(db, "cs-admin@example.com", "admin")
    res = await client.get("/api/v1/checkin-settings", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    for field in (
        "match_m",
        "review_m",
        "accuracy_m",
        "no_show_grace_min",
        "late_min",
        "max_inprogress_min",
    ):
        assert body["values"][field] == DEFAULT_THRESHOLDS[field]
        assert body["is_default"][field] is True


# ---------------------------------------------------------------------------
# PUT — upsert / 部分更新 / null リセット
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_upsert_and_partial_and_reset(client, db) -> None:
    manager = await _make_user(db, "cs-mgr@example.com", "manager")
    hdr = _bearer(manager)

    # 初回 PUT (lazy insert)。max_inprogress_min を含む 6 値の一部を更新。
    res = await client.put(
        "/api/v1/checkin-settings",
        json={"match_m": 150, "review_m": 400, "max_inprogress_min": 120},
        headers=hdr,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["values"]["match_m"] == 150
    assert body["values"]["review_m"] == 400
    assert body["values"]["max_inprogress_min"] == 120
    assert body["is_default"]["match_m"] is False
    assert body["is_default"]["max_inprogress_min"] is False
    # 未指定の accuracy は既定のまま。
    assert body["values"]["accuracy_m"] == DEFAULT_THRESHOLDS["accuracy_m"]
    assert body["is_default"]["accuracy_m"] is True

    # 明示 null で既定に戻す。
    res = await client.put(
        "/api/v1/checkin-settings", json={"max_inprogress_min": None}, headers=hdr
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["values"]["max_inprogress_min"] == DEFAULT_THRESHOLDS["max_inprogress_min"]
    assert body["is_default"]["max_inprogress_min"] is True
    # 既存の match_m は維持 (部分更新)。
    assert body["values"]["match_m"] == 150


@pytest.mark.asyncio
async def test_put_range_validation_422(client, db) -> None:
    admin = await _make_user(db, "cs-range@example.com", "admin")
    hdr = _bearer(admin)
    # max_inprogress_min 範囲外 (1441 > 1440)。
    res = await client.put(
        "/api/v1/checkin-settings", json={"max_inprogress_min": 1441}, headers=hdr
    )
    assert res.status_code == 422, res.text
    # match_m 範囲外。
    res = await client.put("/api/v1/checkin-settings", json={"match_m": 5}, headers=hdr)
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_review_gte_match_422_in_payload(client, db) -> None:
    admin = await _make_user(db, "cs-rgm1@example.com", "admin")
    res = await client.put(
        "/api/v1/checkin-settings",
        json={"match_m": 400, "review_m": 300},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_review_gte_match_422_against_default(client, db) -> None:
    # match_m のみ大きく設定 → 既定 review_m (300) とマージした有効値で矛盾検出。
    admin = await _make_user(db, "cs-rgm2@example.com", "admin")
    res = await client.put(
        "/api/v1/checkin-settings", json={"match_m": 500}, headers=_bearer(admin)
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_get_and_put_staff_forbidden(client, db) -> None:
    staff = Staff(name="S-cs")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    staff_user = await _make_user(db, "cs-staff@example.com", "staff", staff_id=staff.id)
    hdr = _bearer(staff_user)

    assert (await client.get("/api/v1/checkin-settings", headers=hdr)).status_code == 403
    assert (
        await client.put("/api/v1/checkin-settings", json={"match_m": 120}, headers=hdr)
    ).status_code == 403


@pytest.mark.asyncio
async def test_public_allows_staff_and_returns_distance_only(client, db) -> None:
    staff = Staff(name="S-pub")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    staff_user = await _make_user(db, "cs-pubstaff@example.com", "staff", staff_id=staff.id)

    res = await client.get("/api/v1/checkin-settings/public", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"match_m", "review_m", "accuracy_m"}
    assert body["match_m"] == DEFAULT_THRESHOLDS["match_m"]
    # 時間系は露出しない。
    assert "no_show_grace_min" not in body
    assert "max_inprogress_min" not in body


@pytest.mark.asyncio
async def test_public_requires_auth(client) -> None:
    res = await client.get("/api/v1/checkin-settings/public")
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_public_reflects_saved_value(client, db) -> None:
    admin = await _make_user(db, "cs-pubsave@example.com", "admin")
    await client.put(
        "/api/v1/checkin-settings", json={"match_m": 120, "review_m": 350}, headers=_bearer(admin)
    )
    res = await client.get("/api/v1/checkin-settings/public", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["match_m"] == 120
    assert body["review_m"] == 350


# ---------------------------------------------------------------------------
# monitor 連動: 退出忘れ判定が max_inprogress_min 設定を読む
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_reads_max_inprogress_from_settings(db) -> None:
    """max_inprogress_min を 300 に上げると、滞在 270 分は review でなく none."""
    # 設定: 退出忘れしきい値を 300 分へ (既定 240 より緩い)。
    db.add(CheckinSettings(is_singleton=True, max_inprogress_min=300))
    staff = Staff(name="S-mon")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    patient = Patient(code="MIP-1", name="患者MIP", lat=35.0, lng=139.0)
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=TARGET,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    # 9:00 到着・退出なし・now 13:30 → 滞在 270 分。
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=patient.id,
            staff_id=staff.id,
            kind="arrival",
            scanned_at=_utc(9, 0),
            match_status="match",
            threshold_snapshot={"v": 1},
            is_override=False,
            checkin_source="qr",
        )
    )
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert resp.thresholds.max_inprogress_min == 300
    mv = None
    for row in resp.staff:
        for v in row.visits:
            if v.visit_id == visit.id:
                mv = v
    assert mv is not None
    assert mv.phase == PHASE_INPROGRESS
    assert mv.stay_minutes == 270
    # 270 < 300 → 退出忘れに該当せず none (既定 240 ならば review だった)。
    assert mv.alert_level == ALERT_NONE


@pytest.mark.asyncio
async def test_monitor_default_max_inprogress_still_review(db) -> None:
    """設定が無ければ既定 240 で滞在 270 分は review (回帰防止)."""
    staff = Staff(name="S-mon2")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    patient = Patient(code="MIP-2", name="患者MIP2", lat=35.0, lng=139.0)
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=TARGET,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=patient.id,
            staff_id=staff.id,
            kind="arrival",
            scanned_at=_utc(9, 0),
            match_status="match",
            threshold_snapshot={"v": 1},
            is_override=False,
            checkin_source="qr",
        )
    )
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert resp.thresholds.max_inprogress_min == DEFAULT_THRESHOLDS["max_inprogress_min"]
    mv = None
    for row in resp.staff:
        for v in row.visits:
            if v.visit_id == visit.id:
                mv = v
    assert mv is not None
    assert mv.alert_level == ALERT_REVIEW
