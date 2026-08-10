"""NG スタッフ (患者×スタッフ割当禁止) CRUD API tests.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §3〜§4-1.
手本 = ``test_g21_same_address_api.py`` (同住所リンク CRUD).

検証観点:
  N1  PUT で登録 → GET 一覧 (staff_name 解決) → PUT で note 更新 (冪等) → DELETE
  N2  staff ロールは GET 可 / PUT・DELETE は 403
  N3  削除済みスタッフの新規指定は 422 (既存行の更新は可 + staff_is_deleted=true)
  N4  患者 soft-delete で NG 行が物理削除される
  N5  スタッフ soft-delete で NG 行が物理削除される
  N6  存在しない patient_id / staff_id は 404 / 未登録行の DELETE は 404
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.security import create_access_token, hash_password
from app.models import Patient, User
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
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


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"P-{code}", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, *, code: str, name: str) -> Staff:
    s = Staff(code=code, name=name, status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


# ---------------------------------------------------------------------------
# N1: upsert → GET → note 更新 (冪等) → DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n1_upsert_list_update_delete(client, db) -> None:
    admin = await _make_user(db, email="ng-crud-admin@example.com", role="admin")
    patient = await _make_patient(db, code="NG-CRUD-P")
    s1 = await _make_staff(db, code="NG-S1", name="佐藤 花子")
    s2 = await _make_staff(db, code="NG-S2", name="鈴木 太郎")

    # --- 登録 (2 名) ---
    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{s1.id}",
        headers=_bearer(admin),
        json={"note": "相性不良"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["staff_id"] == str(s1.id)
    assert body["staff_name"] == "佐藤 花子"
    assert body["staff_is_deleted"] is False
    assert body["note"] == "相性不良"
    assert body["decided_by_user_id"] == str(admin.id)

    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{s2.id}",
        headers=_bearer(admin),
        json={"note": None},
    )
    assert res.status_code == 200, res.text

    # --- GET 一覧 (staff_name 解決) ---
    res = await client.get(
        f"/api/v1/patients/{patient.id}/ng-staff",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 2
    by_id = {row["staff_id"]: row for row in rows}
    assert by_id[str(s1.id)]["staff_name"] == "佐藤 花子"
    assert by_id[str(s1.id)]["note"] == "相性不良"
    assert by_id[str(s2.id)]["staff_name"] == "鈴木 太郎"
    assert by_id[str(s2.id)]["note"] is None

    # --- note 更新 (upsert 冪等: 行は増えない) ---
    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{s1.id}",
        headers=_bearer(admin),
        json={"note": "理由を更新"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["note"] == "理由を更新"

    db_rows = (
        await db.scalars(select(PatientNgStaff).where(PatientNgStaff.patient_id == patient.id))
    ).all()
    assert len(db_rows) == 2
    assert {r.staff_id for r in db_rows} == {s1.id, s2.id}

    # --- DELETE ---
    res = await client.delete(
        f"/api/v1/patients/{patient.id}/ng-staff/{s1.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    res = await client.get(
        f"/api/v1/patients/{patient.id}/ng-staff",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert [row["staff_id"] for row in res.json()] == [str(s2.id)]

    # 削除済み行の再 DELETE は 404 (= 冪等ではない. 設計書 §4-1「無ければ 404」)
    res = await client.delete(
        f"/api/v1/patients/{patient.id}/ng-staff/{s1.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# N2: RBAC — staff は GET 可 / PUT・DELETE は 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n2_staff_can_read_but_not_mutate(client, db) -> None:
    admin = await _make_user(db, email="ng-rbac-admin@example.com", role="admin")
    staff_user = await _make_user(db, email="ng-rbac-staff@example.com", role="staff")
    patient = await _make_patient(db, code="NG-RBAC-P")
    s = await _make_staff(db, code="NG-RBAC-S", name="権限 テスト")

    # staff は PUT 不可
    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{s.id}",
        headers=_bearer(staff_user),
        json={"note": "だめ"},
    )
    assert res.status_code == 403, res.text

    # admin で登録
    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{s.id}",
        headers=_bearer(admin),
        json={"note": "ok"},
    )
    assert res.status_code == 200, res.text

    # staff は GET 可 (閲覧は全ロール開放)
    res = await client.get(
        f"/api/v1/patients/{patient.id}/ng-staff",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 1

    # staff は DELETE 不可
    res = await client.delete(
        f"/api/v1/patients/{patient.id}/ng-staff/{s.id}",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# N3: 削除済みスタッフ — 新規指定は 422 / 既存行は残り更新可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n3_deleted_staff_new_assignment_rejected(client, db) -> None:
    admin = await _make_user(db, email="ng-del-staff-admin@example.com", role="admin")
    # commit / rollback 後は ORM オブジェクトが expire するため、ヘッダは先に組む。
    headers = _bearer(admin)
    patient = await _make_patient(db, code="NG-DELS-P")
    # 既に NG 登録済みのスタッフ (後で退職させる)
    existing_staff = await _make_staff(db, code="NG-DELS-1", name="既存 退職者")
    # 最初から退職済みのスタッフ
    retired_staff = await _make_staff(db, code="NG-DELS-2", name="新規 退職者")
    # commit / rollback は ORM オブジェクトを expire させ、以降の属性参照が
    # MissingGreenlet になるため ID は先にローカルへ退避する。
    patient_id = patient.id
    existing_staff_id = existing_staff.id
    retired_staff_id = retired_staff.id

    res = await client.put(
        f"/api/v1/patients/{patient_id}/ng-staff/{existing_staff_id}",
        headers=headers,
        json={"note": "退職前に登録"},
    )
    assert res.status_code == 200, res.text

    # 2 名とも soft delete (DB 直更新: staff 削除 API は NG 行を消してしまうため、
    # ここでは「退職済みスタッフの扱い」だけを検証する)。
    # expire 済み ORM オブジェクトへの属性代入は lazy load を誘発するため
    # UPDATE 文で直接書き換える。
    await db.rollback()
    await db.execute(
        update(Staff)
        .where(Staff.id.in_([existing_staff_id, retired_staff_id]))
        .values(deleted_at=datetime.now(UTC))
    )
    await db.commit()

    # 新規指定は 422
    res = await client.put(
        f"/api/v1/patients/{patient_id}/ng-staff/{retired_staff_id}",
        headers=headers,
        json={"note": "退職者を新規指定"},
    )
    assert res.status_code == 422, res.text

    # 既存行の note 更新は可 + staff_is_deleted=true
    res = await client.put(
        f"/api/v1/patients/{patient_id}/ng-staff/{existing_staff_id}",
        headers=headers,
        json={"note": "退職後に更新"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["staff_is_deleted"] is True
    assert res.json()["note"] == "退職後に更新"

    # GET でも退職済みの行は返る
    res = await client.get(
        f"/api/v1/patients/{patient_id}/ng-staff",
        headers=headers,
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["staff_id"] == str(existing_staff_id)
    assert rows[0]["staff_is_deleted"] is True


# ---------------------------------------------------------------------------
# N4: 患者 soft-delete で NG 行が物理削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n4_delete_patient_removes_ng_rows(client, db) -> None:
    admin = await _make_user(db, email="ng-cas-pat@example.com", role="admin")
    target = await _make_patient(db, code="NG-CASP-1")
    other = await _make_patient(db, code="NG-CASP-2")
    s = await _make_staff(db, code="NG-CASP-S", name="巻き添え確認")
    # rollback で ORM オブジェクトが expire するため ID は先に退避する。
    target_id, other_id, staff_id = target.id, other.id, s.id

    for patient_id in (target_id, other_id):
        res = await client.put(
            f"/api/v1/patients/{patient_id}/ng-staff/{staff_id}",
            headers=_bearer(admin),
            json={"note": "cascade"},
        )
        assert res.status_code == 200, res.text

    res = await client.delete(f"/api/v1/patients/{target_id}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    await db.rollback()
    rows = (await db.scalars(select(PatientNgStaff))).all()
    # 削除した患者の行だけ消え、他患者の行は残る
    assert [(r.patient_id, r.staff_id) for r in rows] == [(other_id, staff_id)]


# ---------------------------------------------------------------------------
# N5: スタッフ soft-delete で NG 行が物理削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n5_delete_staff_removes_ng_rows(client, db) -> None:
    admin = await _make_user(db, email="ng-cas-stf@example.com", role="admin")
    patient = await _make_patient(db, code="NG-CASS-P")
    target = await _make_staff(db, code="NG-CASS-1", name="退職する人")
    other = await _make_staff(db, code="NG-CASS-2", name="残る人")
    # rollback で ORM オブジェクトが expire するため ID は先に退避する。
    patient_id, target_id, other_id = patient.id, target.id, other.id

    for staff_id in (target_id, other_id):
        res = await client.put(
            f"/api/v1/patients/{patient_id}/ng-staff/{staff_id}",
            headers=_bearer(admin),
            json={"note": "cascade"},
        )
        assert res.status_code == 200, res.text

    res = await client.delete(f"/api/v1/staff/{target_id}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    await db.rollback()
    rows = (await db.scalars(select(PatientNgStaff))).all()
    assert [(r.patient_id, r.staff_id) for r in rows] == [(patient_id, other_id)]


# ---------------------------------------------------------------------------
# N6: 存在しない ID は 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n6_unknown_ids_return_404(client, db) -> None:
    admin = await _make_user(db, email="ng-404@example.com", role="admin")
    patient = await _make_patient(db, code="NG-404-P")
    s = await _make_staff(db, code="NG-404-S", name="存在する人")
    missing = uuid4()

    # 未知の患者
    res = await client.get(f"/api/v1/patients/{missing}/ng-staff", headers=_bearer(admin))
    assert res.status_code == 404, res.text

    res = await client.put(
        f"/api/v1/patients/{missing}/ng-staff/{s.id}",
        headers=_bearer(admin),
        json={"note": None},
    )
    assert res.status_code == 404, res.text

    # 未知のスタッフ
    res = await client.put(
        f"/api/v1/patients/{patient.id}/ng-staff/{missing}",
        headers=_bearer(admin),
        json={"note": None},
    )
    assert res.status_code == 404, res.text

    # 未登録行の DELETE
    res = await client.delete(
        f"/api/v1/patients/{patient.id}/ng-staff/{s.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text
