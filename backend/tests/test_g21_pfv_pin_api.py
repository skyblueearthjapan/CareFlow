"""Phase G-21 T2: PFV is_pinned 切替 API tests.

検証観点:
  T2.1 PATCH /pin で is_pinned 切替
  T2.2 bulk /pin で複数切替
  T2.3 非 admin で 403
  T2.4 audit_log 記録確認 (action="pfv_pin_toggle")
  T2.5 bulk: 重複 pfv_id は 422 (H2)
  T2.6 bulk: 1 件 missing で 404 + 既存 PFV 値は不変 + audit 0 件 (H3 atomic)
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User
from app.models.audit_log import AuditLog
from app.models.patient_fixed_visit import PatientFixedVisit

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"P-{code}", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_pfv(
    db, *, patient: Patient, weekday: int, is_pinned: bool = False
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(9, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


# ---------------------------------------------------------------------------
# T2.1: PATCH /pin で is_pinned 切替
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_1_patch_pin_toggles_is_pinned(client, db) -> None:
    admin = await _make_user(db, email="pin-admin@example.com", role="admin")
    patient = await _make_patient(db, code="PIN-001")
    pfv = await _make_pfv(db, patient=patient, weekday=0, is_pinned=False)

    res = await client.patch(
        f"/api/v1/patients/fixed-visits/{pfv.id}/pin",
        headers=_bearer(admin),
        json={"is_pinned": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_pinned"] is True
    assert body["id"] == str(pfv.id)

    # DB 反映確認 — 別 session で fetch.
    pfv2 = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.id == pfv.id))
    await db.refresh(pfv2)
    assert pfv2.is_pinned is True

    # false に戻せる
    res = await client.patch(
        f"/api/v1/patients/fixed-visits/{pfv.id}/pin",
        headers=_bearer(admin),
        json={"is_pinned": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_pinned"] is False


# ---------------------------------------------------------------------------
# T2.2: bulk /pin で複数切替
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_2_bulk_pin_updates_multiple(client, db) -> None:
    admin = await _make_user(db, email="bulkpin-admin@example.com", role="admin")
    patient = await _make_patient(db, code="PIN-BULK")
    pfv_a = await _make_pfv(db, patient=patient, weekday=0, is_pinned=False)
    pfv_b = await _make_pfv(db, patient=patient, weekday=1, is_pinned=True)
    pfv_c = await _make_pfv(db, patient=patient, weekday=2, is_pinned=False)

    res = await client.post(
        "/api/v1/patients/fixed-visits/pin/bulk",
        headers=_bearer(admin),
        json=[
            {"pfv_id": str(pfv_a.id), "is_pinned": True},
            {"pfv_id": str(pfv_b.id), "is_pinned": False},
            {"pfv_id": str(pfv_c.id), "is_pinned": True},
        ],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 3
    # 順序保持 (= payload 順).
    assert [row["id"] for row in body] == [str(pfv_a.id), str(pfv_b.id), str(pfv_c.id)]
    assert [row["is_pinned"] for row in body] == [True, False, True]

    # DB 反映確認
    for pfv, expected in [(pfv_a, True), (pfv_b, False), (pfv_c, True)]:
        await db.refresh(pfv)
        assert pfv.is_pinned is expected


# ---------------------------------------------------------------------------
# T2.3: 非 admin で 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_3_staff_cannot_toggle_pin(client, db) -> None:
    staff_user = await _make_user(db, email="pin-staff@example.com", role="staff")
    patient = await _make_patient(db, code="PIN-RBAC")
    pfv = await _make_pfv(db, patient=patient, weekday=3, is_pinned=False)

    # PATCH 単体
    res = await client.patch(
        f"/api/v1/patients/fixed-visits/{pfv.id}/pin",
        headers=_bearer(staff_user),
        json={"is_pinned": True},
    )
    assert res.status_code == 403, res.text

    # bulk
    res = await client.post(
        "/api/v1/patients/fixed-visits/pin/bulk",
        headers=_bearer(staff_user),
        json=[{"pfv_id": str(pfv.id), "is_pinned": True}],
    )
    assert res.status_code == 403, res.text

    # 値は不変
    await db.refresh(pfv)
    assert pfv.is_pinned is False


# ---------------------------------------------------------------------------
# T2.4: audit_log 記録確認
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_4_pin_toggle_writes_audit_log(client, db) -> None:
    admin = await _make_user(db, email="pin-audit@example.com", role="admin")
    patient = await _make_patient(db, code="PIN-AUDIT")
    pfv = await _make_pfv(db, patient=patient, weekday=4, is_pinned=False)

    res = await client.patch(
        f"/api/v1/patients/fixed-visits/{pfv.id}/pin",
        headers=_bearer(admin),
        json={"is_pinned": True},
    )
    assert res.status_code == 200, res.text

    rows = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "pfv_pin_toggle",
                AuditLog.target_id == str(pfv.id),
            )
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == admin.id
    assert row.target_table == "patient_fixed_visits"
    # P4-C: audit に movability の before/after も含まれる形式に拡張された.
    # (ピンON なので movability は 'unknown' のまま = 解放は解除時のみ)
    assert row.before == {"is_pinned": False, "movability": "unknown"}
    assert row.after == {"is_pinned": True, "movability": "unknown"}


# ---------------------------------------------------------------------------
# T2.5 (H2): bulk で重複 pfv_id があれば 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_5_bulk_pin_duplicate_pfv_id_returns_422(client, db) -> None:
    """H2: payload に同じ pfv_id が 2 件あると 422 で弾く (= 順序依存の競合回避)."""
    admin = await _make_user(db, email="bulkpin-dup@example.com", role="admin")
    patient = await _make_patient(db, code="PIN-DUP")
    pfv = await _make_pfv(db, patient=patient, weekday=0, is_pinned=False)

    res = await client.post(
        "/api/v1/patients/fixed-visits/pin/bulk",
        headers=_bearer(admin),
        json=[
            {"pfv_id": str(pfv.id), "is_pinned": True},
            {"pfv_id": str(pfv.id), "is_pinned": False},
        ],
    )
    assert res.status_code == 422, res.text

    # PFV は不変 + audit_log 0 件
    await db.refresh(pfv)
    assert pfv.is_pinned is False
    audit_rows = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "pfv_pin_toggle",
                AuditLog.target_id == str(pfv.id),
            )
        )
    ).all()
    assert audit_rows == []


# ---------------------------------------------------------------------------
# T2.6 (H3): bulk: 1 件 missing pfv_id で 404 + 既存 PFV は不変 + audit 0 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_6_bulk_pin_atomic_rollback_on_missing(client, db) -> None:
    """H3 atomic: 一部 PFV が存在しない場合は 404 で全 rollback.

    * 既存 PFV の ``is_pinned`` は変化しない.
    * audit_logs.action="pfv_pin_toggle" は 1 件も書かれない.
    """
    admin = await _make_user(db, email="bulkpin-atomic@example.com", role="admin")
    patient = await _make_patient(db, code="PIN-ATOMIC")
    pfv_a = await _make_pfv(db, patient=patient, weekday=0, is_pinned=False)
    pfv_b = await _make_pfv(db, patient=patient, weekday=1, is_pinned=True)
    missing_id = uuid.uuid4()  # 存在しない pfv_id

    res = await client.post(
        "/api/v1/patients/fixed-visits/pin/bulk",
        headers=_bearer(admin),
        json=[
            {"pfv_id": str(pfv_a.id), "is_pinned": True},  # 通常なら true へ
            {"pfv_id": str(missing_id), "is_pinned": True},  # 404 トリガー
            {"pfv_id": str(pfv_b.id), "is_pinned": False},  # 通常なら false へ
        ],
    )
    assert res.status_code == 404, res.text
    assert str(missing_id) in res.text

    # rollback: 既存 PFV は一切変化していない
    await db.refresh(pfv_a)
    await db.refresh(pfv_b)
    assert pfv_a.is_pinned is False  # 元のまま
    assert pfv_b.is_pinned is True  # 元のまま

    # audit_log は 1 件も無い
    audit_rows = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "pfv_pin_toggle",
                AuditLog.target_id.in_([str(pfv_a.id), str(pfv_b.id), str(missing_id)]),
            )
        )
    ).all()
    assert audit_rows == []
