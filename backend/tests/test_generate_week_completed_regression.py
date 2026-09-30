"""実施済み訪問のある週で「週を生成」すると 500 になる既知の不具合の回帰テスト.

(``CourseDayTablePanel.tsx`` のコメント「実施済み訪問がある週では 500 になる既知バグ」
/ ``docs/plans/copy-week-design-2026-09-30.md`` §5)

原因: 週生成は ``source='auto'`` かつ ``status='planned'`` の行だけを作り直す
(``_delete_existing_auto_visits``)。打刻で ``status`` が ``in_progress`` /
``completed`` になった ``source='auto'`` の行は消されずに残るが、INSERT 前の
衝突判定 (``_fetch_manual_conflict_keys``) は「auto 以外」または「cancelled」しか
見ていなかった。そのため同じ (患者・日・開始時刻) の auto 行をもう一度 INSERT し、
本番の部分 UNIQUE ``uq_visits_pds_group_active`` に当たって IntegrityError → 500。
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select, text

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit

ISO_YEAR = 2026
ISO_WEEK = 23
MONDAY = date(2026, 6, 1)


async def _admin(db) -> User:
    u = User(email="gw500@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=u.id, role=u.role)}"}


async def _create_partial_unique_index(db) -> None:
    """本番と同じ部分 UNIQUE (migration 0027) をテスト DB にも張る."""
    await db.execute(
        text(
            "CREATE UNIQUE INDEX uq_visits_pds_group_active "
            "ON visits (patient_id, visit_date, start_time, "
            "COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000')) "
            "WHERE deleted_at IS NULL"
        )
    )
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("done_status", ["completed", "in_progress"])
async def test_generate_week_again_with_checked_in_visit_is_not_500(client, db, done_status):
    admin = await _admin(db)
    await _create_partial_unique_index(db)
    office = Office(name="回帰拠点", lat=35.6, lng=140.1)
    db.add(office)
    await db.flush()
    patient = Patient(code="GW500", name="回帰 患者", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id, mode="normal", weekday=0, start_time=time(9, 0), duration_min=60
        )
    )
    await db.commit()

    url = "/api/v1/schedule/generate-week-only"
    body = {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK}
    res = await client.post(url, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text

    visit = await db.scalar(select(Visit).where(Visit.patient_id == patient.id))
    assert visit is not None and visit.source == "auto"
    visit.status = done_status  # QR 打刻で実施済み (or 訪問中) になった状態
    await db.commit()

    res2 = await client.post(url, headers=_bearer(admin), json=body)
    assert res2.status_code == 200, res2.text
    assert res2.json()["visits_created"] == 0

    live = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    ).all()
    assert len(live) == 1
    assert live[0].status == done_status
    assert live[0].visit_date == MONDAY
