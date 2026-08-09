"""案Z「マスタを賢くする」BE (PO 決定 2026-08-09).

1. POST /patients/{id}/fixed-visits/validate — dry-run 検証 (書き込まない・
   患者間衝突 V3 が返る)
2. GET  /patients/{id}/fixed-visits/course-load — (曜日×コース) の他患者負荷
   (対象患者自身は含めない・定数つき)
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import CourseTemplate, Office, Patient, User
from app.models.patient_fixed_visit import PatientFixedVisit


async def _make_admin(db) -> User:
    user = User(
        email="smart-master-admin@example.com",
        password_hash=hash_password("does-not-matter-here"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed(db) -> dict:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    tpl = CourseTemplate(label="A", office_id=office.id)
    db.add(tpl)
    await db.flush()
    target = Patient(
        code="PT-SM-1",
        name="山田 太郎",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
        lat=35.65,
        lng=140.10,
    )
    other = Patient(
        code="PT-SM-2",
        name="佐藤 花子",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
        # 意図的に離す (>100m) — 同住所ペア許容に吸われないように。
        lat=35.70,
        lng=140.20,
    )
    db.add_all([target, other])
    await db.flush()
    # 他患者の型: 月曜 10:00-10:35 コースA
    db.add(
        PatientFixedVisit(
            patient_id=other.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
            course_template_id=tpl.id,
            is_pinned=False,
        )
    )
    await db.commit()
    for obj in (office, tpl, target, other):
        await db.refresh(obj)
    return {"office": office, "tpl": tpl, "target": target, "other": other}


@pytest.mark.asyncio
async def test_validate_dry_run_returns_conflict_and_writes_nothing(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)

    res = await client.post(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/validate",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [
                {
                    "weekday": 0,
                    "start_time": "10:00",
                    "duration_min": 35,
                    "course_template_id": str(seeded["tpl"].id),
                    "slot_index": 0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    warnings = res.json()["warnings"]
    codes = {w["code"] for w in warnings}
    assert "patient_time_conflict" in codes
    conflict = next(w for w in warnings if w["code"] == "patient_time_conflict")
    assert "佐藤 花子" in conflict["message"]

    # dry-run: 対象患者の PFV は 1 行も作られていない
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == seeded["target"].id)
        )
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_validate_clean_slot_returns_no_conflict(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)
    # 火曜 (他患者の型なし) → 衝突警告は出ない
    res = await client.post(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/validate",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [
                {
                    "weekday": 1,
                    "start_time": "10:00",
                    "duration_min": 35,
                    "course_template_id": str(seeded["tpl"].id),
                    "slot_index": 0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    codes = {w["code"] for w in res.json()["warnings"]}
    assert "patient_time_conflict" not in codes


@pytest.mark.asyncio
async def test_course_load_aggregates_other_patients_only(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)
    # 対象患者自身にも型を作る — 集計に含まれないことの検証用
    db.add(
        PatientFixedVisit(
            patient_id=seeded["target"].id,
            mode="normal",
            weekday=0,
            start_time=time(14, 0),
            duration_min=60,
            slot_index=0,
            course_template_id=seeded["tpl"].id,
            is_pinned=False,
        )
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/course-load?mode=normal",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["course_max_minutes"] == 480
    assert body["max_patients_per_course"] >= 1
    cells = body["cells"]
    assert len(cells) == 1  # 他患者 (佐藤) の月曜 A のみ。自身の 14:00 は含めない
    cell = cells[0]
    assert cell["weekday"] == 0
    assert cell["course_template_id"] == str(seeded["tpl"].id)
    assert cell["used_minutes"] == 35
    assert cell["patient_count"] == 1


@pytest.mark.asyncio
async def test_course_load_unknown_patient_404(client, db) -> None:
    admin = await _make_admin(db)
    res = await client.get(
        f"/api/v1/patients/{uuid.uuid4()}/fixed-visits/course-load",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text
