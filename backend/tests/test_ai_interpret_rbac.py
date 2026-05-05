"""W7-BE2 — AI context RBAC tests (Codex Must-fix #2).

`_build_default_context` の `staff_list` / `patient_list` がロール別に
適切なスコープに絞られることを検証する。

設計仕様書 v0.9 §3.5.3 (ロール別の操作スコープ) の「Staff: 自分軸のみ」
を満たすことを確認するため、以下 3 ケースをカバー:

1. admin: 全件 staff_list + 全件 patient_list が返る
2. manager: admin と同等
3. staff (User.staff_id 紐付き): staff_list は自分のみ、
   patient_list は visit_staff_assignments / primary_staff_id /
   secondary_staff_id で紐付く担当患者のみ
4. staff (User.staff_id 未紐付き): staff_list / patient_list とも空
5. caller-supplied 値が user に関わらず尊重される (RBAC バイパスの
   抜け穴にならない用、admin で確認)
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from app.api.v1.ai import _build_default_context
from app.core.security import hash_password
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

# ---------------------------------------------------------------------------
# Fixture helpers


async def _make_user(db, *, email: str, role: str, staff_id: uuid.UUID | None = None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, *, code: str, name: str) -> Staff:
    s = Staff(code=code, name=name, status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_patient(db, *, code: str, name: str) -> Patient:
    p = Patient(code=code, name=name, status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    primary_staff: Staff | None = None,
    secondary_staff: Staff | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=primary_staff.id if primary_staff else None,
        secondary_staff_id=secondary_staff.id if secondary_staff else None,
        visit_date=date(2026, 5, 5),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
        status="planned",
        source="manual",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _attach_assignment(db, *, visit: Visit, staff: Staff) -> None:
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=staff.id))
    await db.commit()


# ---------------------------------------------------------------------------
# 1) admin / manager: 全件 staff_list + 全件 patient_list


@pytest.mark.asyncio
async def test_build_default_context_admin_returns_all_staff_and_patients(db) -> None:
    s1 = await _make_staff(db, code="S001", name="一郎")
    s2 = await _make_staff(db, code="S002", name="二郎")
    p1 = await _make_patient(db, code="P0001", name="患者A")
    p2 = await _make_patient(db, code="P0002", name="患者B")
    admin = await _make_user(db, email="admin-rbac@example.com", role="admin")

    ctx = await _build_default_context(db, {}, admin)

    assert any(f"({s1.code})" in s for s in ctx["staff_list"])
    assert any(f"({s2.code})" in s for s in ctx["staff_list"])
    assert any(f"({p1.code})" in s for s in ctx["patient_list"])
    assert any(f"({p2.code})" in s for s in ctx["patient_list"])
    # 必須メタも入る
    assert "today" in ctx and "iso_week" in ctx and "weekday" in ctx


@pytest.mark.asyncio
async def test_build_default_context_manager_returns_all_staff_and_patients(db) -> None:
    s1 = await _make_staff(db, code="S101", name="幸子")
    s2 = await _make_staff(db, code="S102", name="和子")
    p1 = await _make_patient(db, code="P1001", name="患者C")
    p2 = await _make_patient(db, code="P1002", name="患者D")
    manager = await _make_user(db, email="mgr-rbac@example.com", role="manager")

    ctx = await _build_default_context(db, {}, manager)

    assert any(f"({s1.code})" in s for s in ctx["staff_list"])
    assert any(f"({s2.code})" in s for s in ctx["staff_list"])
    assert any(f"({p1.code})" in s for s in ctx["patient_list"])
    assert any(f"({p2.code})" in s for s in ctx["patient_list"])


# ---------------------------------------------------------------------------
# 2) staff with assignment: 自分軸のみ


@pytest.mark.asyncio
async def test_build_default_context_staff_with_assignment_scopes_to_self(db) -> None:
    """Staff は staff_list に自分のみ、patient_list に担当患者のみ。"""
    me = await _make_staff(db, code="ME001", name="自分")
    other = await _make_staff(db, code="OT002", name="他人")
    my_patient = await _make_patient(db, code="MP001", name="自分の患者")
    other_patient = await _make_patient(db, code="OP002", name="他人の患者")
    unrelated_patient = await _make_patient(db, code="UP003", name="未割当患者")

    # me が担当する visit (visit_staff_assignments 経由)
    v1 = await _make_visit(db, patient=my_patient)
    await _attach_assignment(db, visit=v1, staff=me)

    # other が担当する visit (me から見えてはならない)
    v2 = await _make_visit(db, patient=other_patient, primary_staff=other)

    # 誰も担当していない visit はそもそも patient_list に入らない (unrelated)
    _ = unrelated_patient
    _ = v2  # silence unused

    user = await _make_user(db, email="me-staff@example.com", role="staff", staff_id=me.id)

    ctx = await _build_default_context(db, {}, user)

    # staff_list は自分 1 件のみ
    assert len(ctx["staff_list"]) == 1
    assert me.code in ctx["staff_list"][0]
    assert all(other.code not in s for s in ctx["staff_list"])

    # patient_list は自分の担当患者のみ
    codes_in_ctx = [s.split("(")[-1].rstrip(")") for s in ctx["patient_list"]]
    assert my_patient.code in codes_in_ctx
    assert other_patient.code not in codes_in_ctx
    assert unrelated_patient.code not in codes_in_ctx


@pytest.mark.asyncio
async def test_build_default_context_staff_legacy_primary_staff_id_counts_as_assignment(
    db,
) -> None:
    """v1 互換: visits.primary_staff_id 経由でも担当患者として認識される。"""
    me = await _make_staff(db, code="LE001", name="legacy")
    p = await _make_patient(db, code="LP001", name="legacy患者")
    await _make_visit(db, patient=p, primary_staff=me)

    user = await _make_user(db, email="legacy-staff@example.com", role="staff", staff_id=me.id)

    ctx = await _build_default_context(db, {}, user)

    codes_in_ctx = [s.split("(")[-1].rstrip(")") for s in ctx["patient_list"]]
    assert p.code in codes_in_ctx


@pytest.mark.asyncio
async def test_build_default_context_staff_legacy_secondary_staff_id_counts_as_assignment(
    db,
) -> None:
    """v1 互換: visits.secondary_staff_id 経由でも担当患者として認識される。"""
    me = await _make_staff(db, code="SE001", name="secondary")
    primary = await _make_staff(db, code="PR001", name="primary")
    p = await _make_patient(db, code="SP001", name="secondary患者")
    await _make_visit(db, patient=p, primary_staff=primary, secondary_staff=me)

    user = await _make_user(db, email="sec-staff@example.com", role="staff", staff_id=me.id)

    ctx = await _build_default_context(db, {}, user)

    codes_in_ctx = [s.split("(")[-1].rstrip(")") for s in ctx["patient_list"]]
    assert p.code in codes_in_ctx


# ---------------------------------------------------------------------------
# 3) staff without staff_id: 完全に空


@pytest.mark.asyncio
async def test_build_default_context_staff_without_staff_id_returns_empty_lists(
    db,
) -> None:
    """User.staff_id が紐付いていない staff は staff_list / patient_list とも空。"""
    # 周囲に staff / patient が居ても、自分軸が定義できないため漏らさない。
    await _make_staff(db, code="X001", name="他人A")
    await _make_patient(db, code="XP001", name="他人A担当")

    user = await _make_user(db, email="orphan-staff@example.com", role="staff", staff_id=None)

    ctx = await _build_default_context(db, {}, user)

    assert ctx["staff_list"] == []
    assert ctx["patient_list"] == []


# ---------------------------------------------------------------------------
# 4) caller-supplied は admin / staff いずれでも尊重される


@pytest.mark.asyncio
async def test_build_default_context_supplied_overrides_take_precedence(db) -> None:
    """caller-supplied staff_list / patient_list は role に関わらず上書きされない。"""
    await _make_staff(db, code="ADM-S", name="admin観点")
    await _make_patient(db, code="ADM-P", name="admin観点患者")
    admin = await _make_user(db, email="adm-supplied@example.com", role="admin")

    supplied = {
        "staff_list": ["pre-pinned-staff"],
        "patient_list": ["pre-pinned-patient"],
    }
    ctx = await _build_default_context(db, supplied, admin)

    assert ctx["staff_list"] == ["pre-pinned-staff"]
    assert ctx["patient_list"] == ["pre-pinned-patient"]


@pytest.mark.asyncio
async def test_build_default_context_staff_supplied_overrides_take_precedence(
    db,
) -> None:
    """staff でも caller-supplied 値は尊重される (RBAC は default 動作のみに作用)。"""
    me = await _make_staff(db, code="SUP-S", name="self")
    user = await _make_user(db, email="sup-staff@example.com", role="staff", staff_id=me.id)

    supplied = {
        "staff_list": ["explicit-pin"],
        "patient_list": ["explicit-pin-pt"],
    }
    ctx = await _build_default_context(db, supplied, user)

    assert ctx["staff_list"] == ["explicit-pin"]
    assert ctx["patient_list"] == ["explicit-pin-pt"]
