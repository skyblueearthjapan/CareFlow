"""staff が読める訪問は来週の日曜まで (mobile-future-days・PO 2026-11-01).

スマホの「今日」「今週」で明日以降・来週の予定を先回りして見られるようにする。
ここで縛るのは 3 つ:
  1. 来週の日曜までの自分の訪問は一覧 / 詳細で読める
  2. それより先は一覧で空・詳細で 403 (見える訪問なので存在は隠さない)
  3. admin は制限しない (PC の盤面が先の週を組むため)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit

JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(UTC).astimezone(JST).date()


def _next_sunday() -> date:
    today = _today_jst()
    return today - timedelta(days=today.weekday()) + timedelta(days=13)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_user(db, email: str, role: str, staff: Staff | None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
        staff_id=staff.id if staff else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _setup(db, tag: str) -> tuple[Staff, User, Patient]:
    staff = Staff(name=f"看護{tag}")
    db.add(staff)
    await db.flush()
    user = await _make_user(db, f"horizon-{tag}@example.com", "staff", staff)
    patient = Patient(code=f"HZ-{tag}", name="利用者")
    db.add(patient)
    await db.commit()
    await db.refresh(staff)
    await db.refresh(patient)
    return staff, user, patient


async def _make_visit(db, *, patient: Patient, staff: Staff, visit_date: date) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=visit_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@pytest.mark.asyncio
async def test_staff_list_reads_until_next_sunday_only(client, db) -> None:
    staff, user, patient = await _setup(db, "list")
    sunday = _next_sunday()
    tomorrow = await _make_visit(
        db, patient=patient, staff=staff, visit_date=_today_jst() + timedelta(days=1)
    )
    last_day = await _make_visit(db, patient=patient, staff=staff, visit_date=sunday)
    beyond = await _make_visit(
        db, patient=patient, staff=staff, visit_date=sunday + timedelta(days=1)
    )

    # 期間を指定しなくても、来週の日曜より先は返さない。
    res = await client.get("/api/v1/visits?limit=500", headers=_bearer(user))
    assert res.status_code == 200, res.text
    ids = {r["id"] for r in res.json()}
    assert str(tomorrow.id) in ids
    assert str(last_day.id) in ids
    assert str(beyond.id) not in ids

    # 先の週を直接指定しても空。
    res = await client.get(
        f"/api/v1/visits?week_start={sunday + timedelta(days=1)}"
        f"&week_end={sunday + timedelta(days=7)}",
        headers=_bearer(user),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


@pytest.mark.asyncio
async def test_staff_detail_403_beyond_next_sunday(client, db) -> None:
    staff, user, patient = await _setup(db, "detail")
    sunday = _next_sunday()
    last_day = await _make_visit(db, patient=patient, staff=staff, visit_date=sunday)
    beyond = await _make_visit(
        db, patient=patient, staff=staff, visit_date=sunday + timedelta(days=1)
    )

    ok = await client.get(f"/api/v1/visits/{last_day.id}", headers=_bearer(user))
    assert ok.status_code == 200, ok.text

    ng = await client.get(f"/api/v1/visits/{beyond.id}", headers=_bearer(user))
    assert ng.status_code == 403, ng.text


@pytest.mark.asyncio
async def test_staff_still_cannot_read_others_future_visit(client, db) -> None:
    """権限は自分の訪問だけのまま (先の日を開けても他人の訪問は 404)."""
    other, _, patient = await _setup(db, "other")
    _, me, _ = await _setup(db, "me")
    visit = await _make_visit(
        db, patient=patient, staff=other, visit_date=_today_jst() + timedelta(days=1)
    )
    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(me))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_admin_is_not_limited(client, db) -> None:
    staff, _, patient = await _setup(db, "admin")
    admin = await _make_user(db, "horizon-admin-user@example.com", "admin", None)
    far = await _make_visit(
        db, patient=patient, staff=staff, visit_date=_next_sunday() + timedelta(days=30)
    )
    res = await client.get(f"/api/v1/visits/{far.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    res = await client.get(f"/api/v1/visits?staff_id={staff.id}&limit=500", headers=_bearer(admin))
    assert str(far.id) in {r["id"] for r in res.json()}
