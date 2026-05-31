"""Tests for ``GET /api/v1/schedule/v2/weekday-staff-capacity``.

週ビューのコース「休」/定員表示をスタッフ数連動にするための read-only
エンドポイント. (office_id, weekday) ごとの稼働 staff 数を返す.

A-E コース開講判定 (= frontend 側ロジック) の検証:
    courseCodeIndex(A=0..E=4) < min(staff_count, course_codes_max) なら開講.
    → 4 名出勤: D (index=3) 開講, 3 名: D 休.
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride

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


async def _make_office(db, *, name: str, operating_weekdays: list[int] | None = None) -> Office:
    o = Office(name=name, operating_weekdays=operating_weekdays or [0, 1, 2, 3, 4, 5])
    db.add(o)
    await db.flush()
    return o


async def _make_staff(
    db,
    *,
    name: str,
    office: Office,
    work_days: list[int],
    role: str = "staff",
    is_trainee: bool = False,
) -> Staff:
    s = Staff(
        name=name,
        role=role,
        is_trainee=is_trainee,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    for wd in work_days:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return s


def _count_for(body: dict, office_id, weekday: int) -> int:
    """body.items から (office_id, weekday) の staff_count を引く (未存在は 0)."""
    for it in body["items"]:
        if it["office_id"] == str(office_id) and it["weekday"] == weekday:
            return it["staff_count"]
    return 0


def _manager_count_for(body: dict, office_id, weekday: int) -> int:
    """body.items から (office_id, weekday) の manager_count を引く (未存在は 0)."""
    for it in body["items"]:
        if it["office_id"] == str(office_id) and it["weekday"] == weekday:
            return it["manager_count"]
    return 0


def _opens_course(staff_count: int, course_index: int, course_codes_max: int) -> bool:
    """frontend と同一の A-E 開講判定."""
    return course_index < min(staff_count, course_codes_max)


# ---------------------------------------------------------------------------
# 正常系: 4 名 → D 開講, 3 名 → D 休 (回帰テスト)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_four_staff_opens_d_course_three_staff_rests(client, db) -> None:
    """火曜 4 名出勤 → D (index=3) 開講, 水曜 3 名 → D 休."""
    admin = await _make_user(db, email="wsc-admin1@example.com", role="admin")
    office = await _make_office(db, name="wsc-office-1")
    # 火 (wd=1): 4 名, 水 (wd=2): 3 名
    for i in range(4):
        await _make_staff(db, name=f"wsc1-s{i}", office=office, work_days=[1])
    for i in range(3):
        await _make_staff(db, name=f"wsc1-w{i}", office=office, work_days=[2])
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["course_codes_max"] == 5
    cmax = body["course_codes_max"]

    tue = _count_for(body, office.id, 1)
    wed = _count_for(body, office.id, 2)
    assert tue == 4
    assert wed == 3

    # D = course_index 3.
    assert _opens_course(tue, 3, cmax) is True  # 4 名 → D 開講
    assert _opens_course(wed, 3, cmax) is False  # 3 名 → D 休
    # E = index 4: 4 名でも休 (min(4,5)=4, 4<4 = False).
    assert _opens_course(tue, 4, cmax) is False
    # A-C (index 0-2) はどちらも開講.
    for idx in range(3):
        assert _opens_course(tue, idx, cmax) is True
        assert _opens_course(wed, idx, cmax) is True


@pytest.mark.asyncio
async def test_five_or_more_staff_caps_at_course_codes_max(client, db) -> None:
    """6 名出勤でも A-E (5 コース) まで. E (index=4) は開講, それ以上は無い."""
    admin = await _make_user(db, email="wsc-admin2@example.com", role="admin")
    office = await _make_office(db, name="wsc-office-2")
    for i in range(6):
        await _make_staff(db, name=f"wsc2-s{i}", office=office, work_days=[0])
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    cmax = body["course_codes_max"]
    mon = _count_for(body, office.id, 0)
    assert mon == 6
    # E (index 4) 開講 (min(6,5)=5, 4<5).
    assert _opens_course(mon, 4, cmax) is True


@pytest.mark.asyncio
async def test_trainee_and_override_excluded(client, db) -> None:
    """trainee は除外, off override も除外される."""
    admin = await _make_user(db, email="wsc-admin3@example.com", role="admin")
    office = await _make_office(db, name="wsc-office-3")
    # 月: 2 名 staff + 1 名 trainee → trainee 除外で 2.
    await _make_staff(db, name="wsc3-s0", office=office, work_days=[0])
    s1 = await _make_staff(db, name="wsc3-s1", office=office, work_days=[0])
    await _make_staff(db, name="wsc3-t0", office=office, work_days=[0], is_trainee=True)
    # s1 に月の off override → 1 名に減る.
    db.add(
        StaffWeeklyOverride(
            staff_id=s1.id,
            iso_year=2026,
            iso_week=20,
            weekday=0,
            override_type="off",
        )
    )
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, office.id, 0) == 1


@pytest.mark.asyncio
async def test_all_offices_when_office_id_omitted(client, db) -> None:
    """office_id 未指定なら全拠点を対象に返す."""
    admin = await _make_user(db, email="wsc-admin4@example.com", role="admin")
    office_a = await _make_office(db, name="wsc-office-4a")
    office_b = await _make_office(db, name="wsc-office-4b")
    await _make_staff(db, name="wsc4-a0", office=office_a, work_days=[0])
    await _make_staff(db, name="wsc4-b0", office=office_b, work_days=[1])
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, office_a.id, 0) == 1
    assert _count_for(body, office_b.id, 1) == 1


# ---------------------------------------------------------------------------
# Phase G-53: manager_count (週ビューヘッダーの拠点別 S/M 表示用)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_count_returned_per_weekday(client, db) -> None:
    """月: staff 4 名 + manager 1 名 → staff_count=4, manager_count=1."""
    admin = await _make_user(db, email="wsc-mgr1@example.com", role="admin")
    office = await _make_office(db, name="wsc-office-mgr1")
    for i in range(4):
        await _make_staff(db, name=f"wsc-mgr1-s{i}", office=office, work_days=[0])
    await _make_staff(db, name="wsc-mgr1-m0", office=office, work_days=[0], role="manager")
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, office.id, 0) == 4
    assert _manager_count_for(body, office.id, 0) == 1


@pytest.mark.asyncio
async def test_manager_only_weekday_still_returned(client, db) -> None:
    """staff 0 名でも manager>0 の (office, weekday) は item として返る."""
    admin = await _make_user(db, email="wsc-mgr2@example.com", role="admin")
    office = await _make_office(db, name="wsc-office-mgr2")
    # 火 (wd=1): manager のみ 1 名, staff 0 名.
    await _make_staff(db, name="wsc-mgr2-m0", office=office, work_days=[1], role="manager")
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 2026, "iso_week": 20, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _count_for(body, office.id, 1) == 0
    assert _manager_count_for(body, office.id, 1) == 1


# ---------------------------------------------------------------------------
# RBAC / バリデーション
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="wsc-staff@example.com", role="staff")
    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(staff_user),
        params={"iso_year": 2026, "iso_week": 20},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_rejects_no_auth(client, db) -> None:
    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        params={"iso_year": 2026, "iso_week": 20},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_rejects_bad_iso_year(client, db) -> None:
    admin = await _make_user(db, email="wsc-bad@example.com", role="admin")
    res = await client.get(
        "/api/v1/schedule/v2/weekday-staff-capacity",
        headers=_bearer(admin),
        params={"iso_year": 1990, "iso_week": 20},
    )
    assert res.status_code == 422
