"""W2-BE4: visit v2 拡張カラム + 2 名体制 + visit_staff_assignments tests.

設計仕様書 v0.9 §3.3 / §4.5 / API 契約 §5 / 実装手順書 v0.2 §3 W2-BE4 に対応。

検証観点:
  1. course_id / required_staff_count / visit_group_id の round-trip
  2. required_staff_count 1/2 のみ受理 (CHECK 制約)
  3. 2 名体制: 同じ visit_group_id で 2 visits + visit_staff_assignments 2 行
  4. visit_staff_assignments への複数 staff 割当 (POST /visits/{id}/staff)
  5. 多対多 PK 重複は 409
  6. visit_staff_assignments の DELETE
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, User, Visit, VisitStaffAssignment

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


async def _make_patient(db, code: str = "P-V") -> Patient:
    p = Patient(code=code, name=f"患者{code}")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_course(db, **overrides) -> Course:
    """W15-BE-FIXPATTERN: courses.office_id NOT NULL のため
    Course 作成前に Office を自動作成して FK を埋める。
    呼び出し側が明示的に office_id を渡せば、その値を尊重する。
    """
    base = {"iso_year": 2026, "iso_week": 22, "weekday": 0, "code": "A"}
    base.update(overrides)
    if "office_id" not in base:
        office = Office(name=f"事業所-{base['code']}-{base['weekday']}")
        db.add(office)
        await db.flush()
        base["office_id"] = office.id
    c = Course(**base)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


def _visit_payload(patient_id, **overrides) -> dict:
    base = {
        "patient_id": str(patient_id),
        "visit_date": "2026-05-25",
        "start_time": "09:00:00",
        "end_time": "10:00:00",
        "type": "regular",
        "status": "planned",
        "source": "manual",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. round-trip: course_id / required_staff_count / visit_group_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_v2_round_trip_new_columns(client, db) -> None:
    admin = await _make_user(db, "v2-rt-admin@example.com", "admin")
    p = await _make_patient(db, code="P-RT-1")
    c = await _make_course(db)
    grp = uuid4()

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json=_visit_payload(
            p.id,
            course_id=str(c.id),
            required_staff_count=2,
            visit_group_id=str(grp),
        ),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["course_id"] == str(c.id)
    assert body["required_staff_count"] == 2
    assert body["visit_group_id"] == str(grp)

    # GET でも反映
    vid = body["id"]
    follow = await client.get(f"/api/v1/visits/{vid}", headers=_bearer(admin))
    assert follow.status_code == 200, follow.text
    g = follow.json()
    assert g["course_id"] == str(c.id)
    assert g["required_staff_count"] == 2
    assert g["visit_group_id"] == str(grp)


@pytest.mark.asyncio
async def test_visit_v2_default_required_staff_count_is_1(client, db) -> None:
    """required_staff_count を指定しなければデフォルト 1."""
    admin = await _make_user(db, "v2-d-admin@example.com", "admin")
    p = await _make_patient(db, code="P-RT-D")

    res = await client.post("/api/v1/visits", headers=_bearer(admin), json=_visit_payload(p.id))
    assert res.status_code == 201, res.text
    assert res.json()["required_staff_count"] == 1


# ---------------------------------------------------------------------------
# 2. required_staff_count CHECK 制約
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_v2_required_staff_count_3_returns_422(client, db) -> None:
    """3 以上は Pydantic で 422."""
    admin = await _make_user(db, "v2-bad-admin@example.com", "admin")
    p = await _make_patient(db, code="P-RT-B")

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json=_visit_payload(p.id, required_staff_count=3),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_visit_v2_required_staff_count_zero_returns_422(client, db) -> None:
    admin = await _make_user(db, "v2-bad2-admin@example.com", "admin")
    p = await _make_patient(db, code="P-RT-B0")

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json=_visit_payload(p.id, required_staff_count=0),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 3. 2 名体制: 同じ visit_group_id で 2 visits + assignments 2 行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_v2_two_staff_pattern(client, db) -> None:
    admin = await _make_user(db, "v2-2s-admin@example.com", "admin")
    p = await _make_patient(db, code="P-2S")
    s1 = await _make_staff(db, name="スタッフ1")
    s2 = await _make_staff(db, name="スタッフ2")
    grp = uuid4()

    # 同じ visit_group_id で 2 visits を作る
    base_payload = _visit_payload(
        p.id,
        required_staff_count=2,
        visit_group_id=str(grp),
    )
    v1 = await client.post("/api/v1/visits", headers=_bearer(admin), json=base_payload)
    assert v1.status_code == 201, v1.text
    v2 = await client.post("/api/v1/visits", headers=_bearer(admin), json=base_payload)
    assert v2.status_code == 201, v2.text

    v1_id = v1.json()["id"]
    v2_id = v2.json()["id"]
    assert v1_id != v2_id
    assert v1.json()["visit_group_id"] == str(grp)
    assert v2.json()["visit_group_id"] == str(grp)

    # 各 visit に 1 スタッフを割当
    add1 = await client.post(
        f"/api/v1/visits/{v1_id}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s1.id)},
    )
    assert add1.status_code == 201, add1.text
    add2 = await client.post(
        f"/api/v1/visits/{v2_id}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s2.id)},
    )
    assert add2.status_code == 201, add2.text

    # 取得して assignments を確認
    g1 = await client.get(f"/api/v1/visits/{v1_id}", headers=_bearer(admin))
    assert g1.status_code == 200
    asn1 = g1.json()["staff_assignments"]
    assert len(asn1) == 1
    assert asn1[0]["staff_id"] == str(s1.id)
    assert asn1[0]["visit_id"] == v1_id

    g2 = await client.get(f"/api/v1/visits/{v2_id}", headers=_bearer(admin))
    assert g2.status_code == 200
    asn2 = g2.json()["staff_assignments"]
    assert len(asn2) == 1
    assert asn2[0]["staff_id"] == str(s2.id)

    # DB レベルでも 2 行確認
    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(
                VisitStaffAssignment.visit_id.in_([v1.json()["id"], v2.json()["id"]])
            )
        )
    ).all()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 4. 多対多: 1 visit に複数 staff (現実的には max 2 だが API 上は許可)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_staff_assignments_multiple(client, db) -> None:
    """1 visit に 2 staff の assignments を作れる (2 名体制を 1 visit で表現する別パターン)."""
    admin = await _make_user(db, "v2-m-admin@example.com", "admin")
    p = await _make_patient(db, code="P-M")
    s1 = await _make_staff(db, name="マルチ1")
    s2 = await _make_staff(db, name="マルチ2")

    create = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json=_visit_payload(p.id, required_staff_count=2),
    )
    assert create.status_code == 201, create.text
    vid = create.json()["id"]

    a1 = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s1.id)},
    )
    assert a1.status_code == 201, a1.text
    a2 = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s2.id)},
    )
    assert a2.status_code == 201, a2.text

    follow = await client.get(f"/api/v1/visits/{vid}", headers=_bearer(admin))
    assert follow.status_code == 200
    asn = follow.json()["staff_assignments"]
    assert len(asn) == 2
    assert {a["staff_id"] for a in asn} == {str(s1.id), str(s2.id)}


@pytest.mark.asyncio
async def test_visit_staff_assignments_duplicate_returns_409(client, db) -> None:
    """同じ (visit, staff) ペアの 2 度目の登録は 409 (PK 衝突)."""
    admin = await _make_user(db, "v2-dup-admin@example.com", "admin")
    p = await _make_patient(db, code="P-DUP")
    s = await _make_staff(db, name="重複")

    create = await client.post("/api/v1/visits", headers=_bearer(admin), json=_visit_payload(p.id))
    assert create.status_code == 201, create.text
    vid = create.json()["id"]

    a1 = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s.id)},
    )
    assert a1.status_code == 201, a1.text

    a2 = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s.id)},
    )
    assert a2.status_code == 409, a2.text


@pytest.mark.asyncio
async def test_visit_staff_assignments_delete_returns_204(client, db) -> None:
    admin = await _make_user(db, "v2-del-admin@example.com", "admin")
    p = await _make_patient(db, code="P-DEL")
    s = await _make_staff(db, name="削除対象")

    create = await client.post("/api/v1/visits", headers=_bearer(admin), json=_visit_payload(p.id))
    assert create.status_code == 201, create.text
    vid = create.json()["id"]
    a1 = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(admin),
        json={"staff_id": str(s.id)},
    )
    assert a1.status_code == 201, a1.text

    res = await client.delete(f"/api/v1/visits/{vid}/staff/{s.id}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    follow = await client.get(f"/api/v1/visits/{vid}", headers=_bearer(admin))
    assert follow.status_code == 200
    assert follow.json()["staff_assignments"] == []


@pytest.mark.asyncio
async def test_visit_staff_assignments_delete_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "v2-del2-admin@example.com", "admin")
    p = await _make_patient(db, code="P-DEL2")

    create = await client.post("/api/v1/visits", headers=_bearer(admin), json=_visit_payload(p.id))
    assert create.status_code == 201, create.text
    vid = create.json()["id"]

    res = await client.delete(f"/api/v1/visits/{vid}/staff/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 5. RBAC: staff には POST /staff 不可 (admin/manager のみ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_staff_assignments_add_staff_role_returns_403(client, db) -> None:
    admin = await _make_user(db, "v2-rb-admin@example.com", "admin")
    staff_user = await _make_user(db, "v2-rb-staff@example.com", "staff")
    p = await _make_patient(db, code="P-RB")
    s = await _make_staff(db, name="RBAC")

    create = await client.post("/api/v1/visits", headers=_bearer(admin), json=_visit_payload(p.id))
    assert create.status_code == 201, create.text
    vid = create.json()["id"]

    res = await client.post(
        f"/api/v1/visits/{vid}/staff",
        headers=_bearer(staff_user),
        json={"staff_id": str(s.id)},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 6. ORM round-trip for VisitStaffAssignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_staff_assignment_orm_round_trip(db) -> None:
    p = Patient(code="P-ORM-VSA", name="ORM患者")
    db.add(p)
    s = Staff(name="ORMスタッフ")
    db.add(s)
    await db.commit()
    await db.refresh(p)
    await db.refresh(s)

    v = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 30),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        required_staff_count=1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)

    asn = VisitStaffAssignment(visit_id=v.id, staff_id=s.id)
    db.add(asn)
    await db.commit()

    rows = (
        await db.scalars(select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == v.id))
    ).all()
    assert len(rows) == 1
    assert rows[0].staff_id == s.id
    assert rows[0].created_at is not None
