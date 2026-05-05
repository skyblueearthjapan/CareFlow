"""W7-BE1: Patients API の Staff RBAC 強化テスト (Codex Must-fix #1).

検証観点:
  1. admin は全件参照可 (list / detail)
  2. manager は全件参照可 (list / detail)
  3. staff (assignment 有り) は担当患者のみ list で見える
  4. staff (assignment 無し) は list 0 件 / 担当外 detail で 404
  5. staff: 担当外 detail は 404 (情報漏洩防止: 403 ではない)
  6. staff: POST / PATCH / DELETE は 403

担当判定ロジック (`_staff_patient_ids_subquery`):
  - visits.primary_staff_id / secondary_staff_id / mentor_staff_id == staff (v1 互換)
  - visit_staff_assignments に staff の行がある visit (v2 正規; §4.5)
  - visits.deleted_at IS NULL に限定
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit, VisitStaffAssignment

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_patient(db, code: str, name: str = "患者") -> Patient:
    # ``special_week_active`` は PatientV2Read 側で ``list[dict] = []`` を要求するが、
    # ORM 直挿入では NULL のままで残るため、明示的に空リストを与える
    # (W7-BE1 RBAC テストの読込で ResponseValidationError を回避).
    p = Patient(code=code, name=name, special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    primary_staff_id=None,
    secondary_staff_id=None,
    mentor_staff_id=None,
    visit_date: date = date(2026, 5, 25),
    deleted: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=primary_staff_id,
        secondary_staff_id=secondary_staff_id,
        mentor_staff_id=mentor_staff_id,
        visit_date=visit_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="manual",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    if deleted:
        from sqlalchemy import func as sa_func

        v.deleted_at = sa_func.now()
        await db.commit()
        await db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# 1. admin: list で全件取得可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_admin_sees_all(client, db) -> None:
    admin = await _make_user(db, "rbac-list-admin@example.com", "admin")
    await _make_patient(db, "P-RBAC-A1")
    await _make_patient(db, "P-RBAC-A2")
    await _make_patient(db, "P-RBAC-A3")

    res = await client.get("/api/v1/patients", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    codes = {item["code"] for item in res.json()}
    assert {"P-RBAC-A1", "P-RBAC-A2", "P-RBAC-A3"} <= codes


# ---------------------------------------------------------------------------
# 2. manager: list で全件取得可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_manager_sees_all(client, db) -> None:
    mgr = await _make_user(db, "rbac-list-mgr@example.com", "manager")
    await _make_patient(db, "P-RBAC-M1")
    await _make_patient(db, "P-RBAC-M2")

    res = await client.get("/api/v1/patients", headers=_bearer(mgr))
    assert res.status_code == 200, res.text
    codes = {item["code"] for item in res.json()}
    assert {"P-RBAC-M1", "P-RBAC-M2"} <= codes


# ---------------------------------------------------------------------------
# 3. staff with assignment: 担当患者のみ取得 (primary_staff_id 経由)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_via_primary_staff(client, db) -> None:
    s = await _make_staff(db, name="主担当")
    staff_user = await _make_user(db, "rbac-l-s1@example.com", "staff", staff_id=s.id)

    mine = await _make_patient(db, "P-RBAC-MINE")
    other = await _make_patient(db, "P-RBAC-OTHER")  # noqa: F841 — needed for setup

    # staff が primary の visit を作る
    await _make_visit(db, patient=mine, primary_staff_id=s.id)

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    codes = {item["code"] for item in res.json()}
    assert codes == {"P-RBAC-MINE"}, f"staff should only see assigned patient, got {codes}"


# ---------------------------------------------------------------------------
# 4. staff with assignment via visit_staff_assignments (v2 正規)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_via_assignments_table(client, db) -> None:
    s = await _make_staff(db, name="多対多")
    staff_user = await _make_user(db, "rbac-l-s2@example.com", "staff", staff_id=s.id)

    mine = await _make_patient(db, "P-RBAC-VSA-MINE")
    other = await _make_patient(db, "P-RBAC-VSA-OTHER")  # noqa: F841

    # primary_staff_id は別人 (None) だが visit_staff_assignments で紐付ける
    v = await _make_visit(db, patient=mine, primary_staff_id=None)
    db.add(VisitStaffAssignment(visit_id=v.id, staff_id=s.id))
    await db.commit()

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    codes = {item["code"] for item in res.json()}
    assert codes == {"P-RBAC-VSA-MINE"}


# ---------------------------------------------------------------------------
# 5. staff: secondary / mentor staff 経由でも担当扱い
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_via_secondary_or_mentor(client, db) -> None:
    s = await _make_staff(db, name="副/メンター")
    staff_user = await _make_user(db, "rbac-l-s3@example.com", "staff", staff_id=s.id)

    p_secondary = await _make_patient(db, "P-RBAC-SEC")
    p_mentor = await _make_patient(db, "P-RBAC-MEN")

    await _make_visit(db, patient=p_secondary, secondary_staff_id=s.id)
    await _make_visit(db, patient=p_mentor, mentor_staff_id=s.id)

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    codes = {item["code"] for item in res.json()}
    assert codes == {"P-RBAC-SEC", "P-RBAC-MEN"}


# ---------------------------------------------------------------------------
# 6. staff without staff_id: 0 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_without_staff_id_returns_empty(client, db) -> None:
    staff_user = await _make_user(db, "rbac-l-nostaff@example.com", "staff", staff_id=None)
    await _make_patient(db, "P-RBAC-NOSTAFF")

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 7. staff with staff_id but 0 assignments: 0 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_no_assignment_returns_empty(client, db) -> None:
    s = await _make_staff(db, name="未担当")
    staff_user = await _make_user(db, "rbac-l-noasn@example.com", "staff", staff_id=s.id)

    # 患者は居るが、当該 staff の visit/assignment は無い
    await _make_patient(db, "P-RBAC-LONELY-1")
    await _make_patient(db, "P-RBAC-LONELY-2")

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 8. staff: deleted な visit 経由では「担当」扱いされない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patients_staff_excludes_deleted_visits(client, db) -> None:
    s = await _make_staff(db, name="ソフト削除")
    staff_user = await _make_user(db, "rbac-l-deleted@example.com", "staff", staff_id=s.id)

    p = await _make_patient(db, "P-RBAC-DEL-VISIT")
    # ソフト削除済み visit は担当判定に使わない
    await _make_visit(db, patient=p, primary_staff_id=s.id, deleted=True)

    res = await client.get("/api/v1/patients", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 9. detail: admin は任意患者を取得可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_patient_admin_any(client, db) -> None:
    admin = await _make_user(db, "rbac-g-admin@example.com", "admin")
    p = await _make_patient(db, "P-RBAC-G-ADM")
    res = await client.get(f"/api/v1/patients/{p.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["code"] == "P-RBAC-G-ADM"


@pytest.mark.asyncio
async def test_get_patient_manager_any(client, db) -> None:
    mgr = await _make_user(db, "rbac-g-mgr@example.com", "manager")
    p = await _make_patient(db, "P-RBAC-G-MGR")
    res = await client.get(f"/api/v1/patients/{p.id}", headers=_bearer(mgr))
    assert res.status_code == 200, res.text
    assert res.json()["code"] == "P-RBAC-G-MGR"


# ---------------------------------------------------------------------------
# 10. detail: staff は担当患者を取得可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_patient_staff_assigned_returns_200(client, db) -> None:
    s = await _make_staff(db, name="詳細担当")
    staff_user = await _make_user(db, "rbac-g-s1@example.com", "staff", staff_id=s.id)
    mine = await _make_patient(db, "P-RBAC-G-MINE")
    await _make_visit(db, patient=mine, primary_staff_id=s.id)

    res = await client.get(f"/api/v1/patients/{mine.id}", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    assert res.json()["code"] == "P-RBAC-G-MINE"


# ---------------------------------------------------------------------------
# 11. detail: staff の担当外は 404 (not 403)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_patient_staff_out_of_scope_returns_404(client, db) -> None:
    s = await _make_staff(db, name="権限外")
    staff_user = await _make_user(db, "rbac-g-s2@example.com", "staff", staff_id=s.id)
    other = await _make_patient(db, "P-RBAC-G-OTHER")
    # other に対する visit / assignment は存在しない

    res = await client.get(f"/api/v1/patients/{other.id}", headers=_bearer(staff_user))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_get_patient_staff_no_staff_id_returns_404(client, db) -> None:
    staff_user = await _make_user(db, "rbac-g-nost@example.com", "staff", staff_id=None)
    p = await _make_patient(db, "P-RBAC-G-NOST")

    res = await client.get(f"/api/v1/patients/{p.id}", headers=_bearer(staff_user))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_get_patient_staff_unknown_id_returns_404(client, db) -> None:
    s = await _make_staff(db, name="未知")
    staff_user = await _make_user(db, "rbac-g-unk@example.com", "staff", staff_id=s.id)
    res = await client.get(f"/api/v1/patients/{uuid4()}", headers=_bearer(staff_user))
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 12. mutations: staff は POST/PATCH/DELETE で 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_patient_staff_returns_403(client, db) -> None:
    s = await _make_staff(db, name="作成権限なし")
    staff_user = await _make_user(db, "rbac-c-staff@example.com", "staff", staff_id=s.id)
    payload = {
        "code": "P-RBAC-C-FORBID",
        "name": "禁止",
    }
    res = await client.post("/api/v1/patients", headers=_bearer(staff_user), json=payload)
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_patch_patient_staff_returns_403(client, db) -> None:
    s = await _make_staff(db, name="更新権限なし")
    staff_user = await _make_user(db, "rbac-p-staff@example.com", "staff", staff_id=s.id)
    # 担当患者にしてもなお 403 (権限ベースのため visibility と独立)
    p = await _make_patient(db, "P-RBAC-PATCH")
    await _make_visit(db, patient=p, primary_staff_id=s.id)

    res = await client.patch(
        f"/api/v1/patients/{p.id}",
        headers=_bearer(staff_user),
        json={"name": "改竄?"},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_delete_patient_staff_returns_403(client, db) -> None:
    s = await _make_staff(db, name="削除権限なし")
    staff_user = await _make_user(db, "rbac-d-staff@example.com", "staff", staff_id=s.id)
    p = await _make_patient(db, "P-RBAC-DEL")
    await _make_visit(db, patient=p, primary_staff_id=s.id)

    res = await client.delete(f"/api/v1/patients/{p.id}", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text
