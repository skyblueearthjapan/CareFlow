"""週空間 A2 — POST /api/v1/schedule/v2/visit-assign-staff-week

正典: docs/plans/weekly-space-design.md §4-2 (患者個別の貼り替え)。

検証観点:
  1. 割当: primary_staff_id + manual_staff_override=True + VSA 置換
  2. 解除 (staff_id=None): manual=False・VSA 空
  3. 同じ担当は no-op (changed=False)
  4. planned 以外 422 / 新人 422 / staff ロール 403
  5. NG スタッフ: 422 constraint_confirmation_required → acknowledge 再送で通る
  6. undo: op_log の set_visit_staff payload で元へ戻せる
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.op_log_service import _execute_payload

_URL = "/api/v1/schedule/v2/visit-assign-staff-week"


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, *, name: str, is_trainee: bool = False) -> Staff:
    s = Staff(name=name, is_trainee=is_trainee)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_visit(
    db, *, patient: Patient, staff: Staff | None = None, status_value="planned"
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id if staff else None,
        visit_date=date(2026, 9, 4),  # 金曜
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status=status_value,
        source="auto",
    )
    db.add(v)
    await db.flush()
    if staff is not None:
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=staff.id))
    await db.commit()
    await db.refresh(v)
    return v


async def _vsa_staff_ids(db, visit_id) -> set:
    rows = await db.scalars(
        select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == visit_id)
    )
    return set(rows.all())


@pytest.mark.asyncio
async def test_assign_sets_primary_manual_and_vsa(client, db) -> None:
    admin = await _make_user(db, email="vas-1@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-1")
    staff_a = await _make_staff(db, name="旧担当")
    staff_b = await _make_staff(db, name="新担当")
    visit = await _make_visit(db, patient=patient, staff=staff_a)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": str(staff_b.id)}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["changed"] is True
    assert body["staff_id"] == str(staff_b.id)

    await db.refresh(visit)
    assert visit.primary_staff_id == staff_b.id
    assert visit.manual_staff_override is True
    assert await _vsa_staff_ids(db, visit.id) == {staff_b.id}


@pytest.mark.asyncio
async def test_unassign_clears_manual_and_vsa(client, db) -> None:
    admin = await _make_user(db, email="vas-2@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-2")
    staff_a = await _make_staff(db, name="担当A2")
    visit = await _make_visit(db, patient=patient, staff=staff_a)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": None}
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.primary_staff_id is None
    assert visit.manual_staff_override is False
    assert await _vsa_staff_ids(db, visit.id) == set()


@pytest.mark.asyncio
async def test_same_staff_is_noop(client, db) -> None:
    admin = await _make_user(db, email="vas-3@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-3")
    staff_a = await _make_staff(db, name="担当A3")
    visit = await _make_visit(db, patient=patient, staff=staff_a)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": str(staff_a.id)}
    )
    assert res.status_code == 200, res.text
    assert res.json()["changed"] is False


@pytest.mark.asyncio
async def test_non_planned_returns_422(client, db) -> None:
    admin = await _make_user(db, email="vas-4@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-4")
    staff_b = await _make_staff(db, name="新担当4")
    visit = await _make_visit(db, patient=patient, status_value="completed")

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": str(staff_b.id)}
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_trainee_returns_422(client, db) -> None:
    admin = await _make_user(db, email="vas-5@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-5")
    trainee = await _make_staff(db, name="新人さん", is_trainee=True)
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": str(trainee.id)}
    )
    assert res.status_code == 422, res.text
    assert "新人" in res.text


@pytest.mark.asyncio
async def test_staff_role_returns_403(client, db) -> None:
    patient = await _make_patient(db, code="VAS-6")
    staff_a = await _make_staff(db, name="担当A6")
    sr = await _make_user(db, email="vas-6@example.com", role="staff", staff_id=staff_a.id)
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(sr), json={"visit_id": str(visit.id), "staff_id": str(staff_a.id)}
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_ng_staff_requires_acknowledge(client, db) -> None:
    """NG スタッフへの貼り替えは確認 422 → acknowledge 再送で通る (§7-2)."""
    admin = await _make_user(db, email="vas-7@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-7")
    ng_staff = await _make_staff(db, name="NG対象")
    db.add(PatientNgStaff(patient_id=patient.id, staff_id=ng_staff.id, note="相性"))
    await db.commit()
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "staff_id": str(ng_staff.id)}
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "constraint_confirmation_required"

    res2 = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "staff_id": str(ng_staff.id),
            "acknowledge_constraint_warnings": True,
        },
    )
    assert res2.status_code == 200, res2.text
    await db.refresh(visit)
    assert visit.primary_staff_id == ng_staff.id


@pytest.mark.asyncio
async def test_oplog_set_visit_staff_payload_restores(client, db) -> None:
    """undo 実体: set_visit_staff の inverse payload 実行で元の担当へ戻る."""
    admin = await _make_user(db, email="vas-8@example.com", role="admin")
    patient = await _make_patient(db, code="VAS-8")
    staff_a = await _make_staff(db, name="元担当8")
    staff_b = await _make_staff(db, name="新担当8")
    visit = await _make_visit(db, patient=patient, staff=staff_a)
    visit_id = visit.id
    staff_a_id = staff_a.id

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit_id), "staff_id": str(staff_b.id)}
    )
    assert res.status_code == 200, res.text
    # API は別セッションで書くため、テスト側 session のキャッシュを捨てて
    # 最新状態 (staff_b) を見せてから inverse を実行する。
    db.expire_all()

    # inverse payload (= 変更前の状態) を直接実行して戻ることを検証。
    await _execute_payload(
        db,
        {
            "op": "set_visit_staff",
            "visit_id": str(visit_id),
            "staff_id": str(staff_a_id),
            "manual": False,
        },
    )
    await db.commit()
    restored = await db.scalar(select(Visit).where(Visit.id == visit_id))
    assert restored is not None
    assert restored.primary_staff_id == staff_a_id
    assert restored.manual_staff_override is False
    assert await _vsa_staff_ids(db, visit_id) == {staff_a_id}
