"""Tests for ``GET /api/v1/schedule/v2/pfv-course-presence``.

PO 決定 (2026-07-09): 固定訪問スケジュール (PFV) に含まれるコースを「正」とし、
スタッフ数連動の開講判定と和集合で週/日ビューの列を出す (スタッフ不足で列ごと消えて
既存訪問が管理画面から不可視になる事故を防ぐ) ための read-only 集計エンドポイント.

``patient_fixed_visits`` を (course_template_id, weekday) で GROUP BY し件数を返す.
    - course_template_id IS NULL は除外.
    - 削除済み患者 (patients.deleted_at IS NOT NULL) の PFV は除外.
    - mode ('normal'/'special') は絞らず全 mode を集計 (存在判定のみ).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.course_template import CourseTemplate
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name, operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add(o)
    await db.flush()
    return o


async def _make_template(db, *, office: Office, label: str) -> CourseTemplate:
    ct = CourseTemplate(office_id=office.id, label=label)
    db.add(ct)
    await db.flush()
    return ct


async def _make_patient(db, *, office: Office, code: str, deleted: bool = False) -> Patient:
    p = Patient(
        id=uuid.uuid4(),
        code=code,
        name=f"患者-{code}",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={},
    )
    if deleted:
        p.deleted_at = datetime.now(UTC)
    db.add(p)
    await db.flush()
    return p


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    template: CourseTemplate | None,
    mode: str = "normal",
    slot_index: int = 0,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode=mode,
        weekday=weekday,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=slot_index,
        course_template_id=template.id if template is not None else None,
    )
    db.add(pfv)
    await db.flush()
    return pfv


def _count_for(body: dict, template_id, weekday: int) -> int:
    """body.items から (course_template_id, weekday) の pfv_count を引く (未存在は 0)."""
    for it in body["items"]:
        if it["course_template_id"] == str(template_id) and it["weekday"] == weekday:
            return it["pfv_count"]
    return 0


# ---------------------------------------------------------------------------
# 正常系: PFV 2 件 (同 template×曜日) → presence 1 行 count=2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_pfvs_same_template_weekday_aggregated(client, db) -> None:
    """同 (course_template_id, weekday) の PFV 2 件 → 1 行 pfv_count=2."""
    admin = await _make_user(db, email="pcp-admin1@example.com", role="admin")
    office = await _make_office(db, name="pcp-office-1")
    ct = await _make_template(db, office=office, label="C")
    p1 = await _make_patient(db, office=office, code="PCP1A")
    p2 = await _make_patient(db, office=office, code="PCP1B")
    await _make_pfv(db, patient=p1, weekday=2, template=ct)
    await _make_pfv(db, patient=p2, weekday=2, template=ct)
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/pfv-course-presence",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, ct.id, 2) == 2
    # 別曜日は 0 (行なし).
    assert _count_for(body, ct.id, 3) == 0


@pytest.mark.asyncio
async def test_all_modes_aggregated(client, db) -> None:
    """normal / special 両方の固定枠が同一 (template, weekday) の件数に合算される."""
    admin = await _make_user(db, email="pcp-admin-mode@example.com", role="admin")
    office = await _make_office(db, name="pcp-office-mode")
    ct = await _make_template(db, office=office, label="D")
    p_normal = await _make_patient(db, office=office, code="PCPMN")
    p_special = await _make_patient(db, office=office, code="PCPMS")
    await _make_pfv(db, patient=p_normal, weekday=1, template=ct, mode="normal")
    await _make_pfv(db, patient=p_special, weekday=1, template=ct, mode="special")
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/pfv-course-presence",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, ct.id, 1) == 2


# ---------------------------------------------------------------------------
# 除外: 削除済み患者 / course_template_id IS NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_patient_pfv_excluded(client, db) -> None:
    """削除済み患者 (deleted_at) の PFV は集計から除外される."""
    admin = await _make_user(db, email="pcp-admin2@example.com", role="admin")
    office = await _make_office(db, name="pcp-office-2")
    ct = await _make_template(db, office=office, label="A")
    p_live = await _make_patient(db, office=office, code="PCP2LIVE")
    p_dead = await _make_patient(db, office=office, code="PCP2DEAD", deleted=True)
    await _make_pfv(db, patient=p_live, weekday=0, template=ct)
    await _make_pfv(db, patient=p_dead, weekday=0, template=ct)
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/pfv-course-presence",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 生存 1 件のみ (削除済みは除外).
    assert _count_for(body, ct.id, 0) == 1


@pytest.mark.asyncio
async def test_null_course_template_excluded(client, db) -> None:
    """course_template_id IS NULL の PFV は item に現れない."""
    admin = await _make_user(db, email="pcp-admin3@example.com", role="admin")
    office = await _make_office(db, name="pcp-office-3")
    p = await _make_patient(db, office=office, code="PCP3")
    await _make_pfv(db, patient=p, weekday=4, template=None)
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/pfv-course-presence",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # NULL テンプレの PFV は 1 件も含まれない.
    assert all(it["course_template_id"] is not None for it in body["items"])
    assert body["items"] == [] or all(it["weekday"] != 4 for it in body["items"])


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="pcp-staff@example.com", role="staff")
    res = await client.get(
        "/api/v1/schedule/v2/pfv-course-presence",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_rejects_no_auth(client, db) -> None:
    res = await client.get("/api/v1/schedule/v2/pfv-course-presence")
    assert res.status_code == 401
