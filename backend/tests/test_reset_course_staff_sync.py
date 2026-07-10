"""reset-to-fixed のコース担当同期 + assign-staff-only 二度押しガードの回帰テスト.

2026-07-11 根治 (handoff 2026-07-10 §4.7 「第4の事故経路」):
  reset_visits_to_fixed が訪問側 (visits.primary_staff_id / VSA) だけを
  PFV 由来のローテーションで再構築し、コース側 (courses.assigned_staff_id =
  表示の正典・原則⑥) を残置していたため、スケジュール表示とモバイル/モニターの
  担当が乖離した (2026-07-10 の熊澤さんモバイル0件事故)。

検証観点:
  1. コース担当が有効ならそのスタッフを visit に採用する (ローテーションより優先)
  2. コース担当が退職 (論理削除) 済みならローテーションで選び、コース側へ書き戻す
  3. dry_run ではコース側を書き換えない
  4. assign-staff-only は同一週の実行中リクエストを 409 で拒否する (二度押しガード)
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

import pytest
from sqlalchemy import select

from app.api.v1.schedule import _get_assign_staff_only_lock
from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 24


async def _seed_reset_fixture(db) -> dict[str, Any]:
    """1 拠点 + コース A (assigned_staff 付き) + PFV 1 件 + ローテ pool 1 名."""
    office = Office(name="reset-sync拠点", operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add(office)
    await db.flush()

    ct = CourseTemplate(office_id=office.id, label="A", capacity_mon=6)
    db.add(ct)
    await db.flush()

    # ローテーション pool に入るスタッフ (role='staff'・非 trainee・月曜出勤).
    pool_staff = Staff(
        code="RS-POOL",
        name="ローテ要員",
        role="staff",
        status="active",
        is_trainee=False,
        primary_office_id=office.id,
    )
    # コース担当にだけなっているマネージャー (pool には入らない).
    course_staff = Staff(
        code="RS-MGR",
        name="コース担当マネージャー",
        role="manager",
        status="active",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add_all([pool_staff, course_staff])
    await db.flush()
    db.add(StaffShift(staff_id=pool_staff.id, weekday=0, is_on=True))

    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        template_id=ct.id,
        office_id=office.id,
        assigned_staff_id=course_staff.id,
    )
    db.add(course)

    patient = Patient(
        code="RS-P1",
        name="reset同期 患者",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
            slot_index=0,
            is_pinned=True,
            course_template_id=ct.id,
        )
    )
    await db.commit()

    return {
        "office": office,
        "course": course,
        "pool_staff": pool_staff,
        "course_staff": course_staff,
        "patient": patient,
    }


async def _load_active_visits(db, patient_id) -> list[Visit]:
    rows = await db.scalars(
        select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
    )
    return list(rows.all())


# ---------------------------------------------------------------------------
# 1) コース担当 (有効) を visit へ採用 — ローテーションより優先
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_uses_course_assigned_staff_when_valid(db) -> None:
    """コース担当が有効なら visit.primary_staff_id にコース担当を採用する.

    ローテーション pool には別スタッフ (role='staff') がいるが、コース担当の
    マネージャーが優先される (= スケジュール表示・モニター・モバイルが一致)。
    コース側の assigned_staff_id は不変。
    """
    fx = await _seed_reset_fixture(db)

    result = await reset_visits_to_fixed(
        db,
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        office_ids=[fx["office"].id],
        mode="legacy",
    )
    await db.commit()

    assert result["visits_regenerated"] == 1
    visits = await _load_active_visits(db, fx["patient"].id)
    assert len(visits) == 1
    assert visits[0].primary_staff_id == fx["course_staff"].id, (
        "コース担当 (courses.assigned_staff_id) が visit に採用されていない"
    )
    await db.refresh(fx["course"])
    assert fx["course"].assigned_staff_id == fx["course_staff"].id, (
        "有効なコース担当が reset で書き換えられた"
    )


# ---------------------------------------------------------------------------
# 2) コース担当が退職済み → ローテーションで選び、コース側へ書き戻す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_heals_stale_course_staff_via_rotation_writeback(db) -> None:
    """退職 (論理削除) スタッフが残置されたコース担当は、ローテーション結果で修復される.

    visit とコースの両方が同じ有効スタッフになる (乖離ゼロ)。
    """
    fx = await _seed_reset_fixture(db)
    # コース担当を退職させる (スタッフ削除とアカウント無効化は別物 — handoff §4-4).
    fx["course_staff"].deleted_at = datetime.now(tz=UTC)
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        office_ids=[fx["office"].id],
        mode="legacy",
    )
    await db.commit()

    assert result["visits_regenerated"] == 1
    visits = await _load_active_visits(db, fx["patient"].id)
    assert len(visits) == 1
    assert visits[0].primary_staff_id == fx["pool_staff"].id, (
        "退職済みコース担当が visit に採用されている (ローテーションに落ちていない)"
    )
    await db.refresh(fx["course"])
    assert fx["course"].assigned_staff_id == fx["pool_staff"].id, (
        "ローテーション結果がコース側へ書き戻されていない (乖離が残る)"
    )


# ---------------------------------------------------------------------------
# 3) dry_run はコース側を書き換えない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_dry_run_does_not_touch_course_staff(db) -> None:
    """dry_run=True では visit も生成せず、コース担当も不変 (DB 不変契約)."""
    fx = await _seed_reset_fixture(db)
    fx["course_staff"].deleted_at = datetime.now(tz=UTC)
    # rollback で ORM インスタンスが expire するため、ID は先に素の値で捕捉する.
    stale_id = fx["course_staff"].id
    patient_id = fx["patient"].id
    course_id = fx["course"].id
    office_id = fx["office"].id
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        office_ids=[office_id],
        mode="legacy",
        dry_run=True,
    )
    await db.rollback()

    assert result["dry_run"] is True
    visits = await _load_active_visits(db, patient_id)
    assert visits == []
    course = await db.get(Course, course_id)
    assert course is not None
    assert course.assigned_staff_id == stale_id, "dry_run でコース担当が書き換えられた"


# ---------------------------------------------------------------------------
# 4) assign-staff-only 二度押しガード (実行中は 409)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_staff_only_returns_409_while_running(client, db) -> None:
    """同一週のロックが取られている間は 409、解放後は通常応答に戻る."""
    admin = User(
        email="rs-guard-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK}

    lock = _get_assign_staff_only_lock(TEST_ISO_YEAR, TEST_ISO_WEEK)
    await lock.acquire()
    try:
        res = await client.post("/api/v1/schedule/assign-staff-only", json=body, headers=headers)
        assert res.status_code == 409, res.text
        assert "実行中" in res.json()["detail"]
    finally:
        lock.release()

    # 解放後は通常の処理に到達する (空週なので 200 / 0 コース).
    res_after = await client.post("/api/v1/schedule/assign-staff-only", json=body, headers=headers)
    assert res_after.status_code == 200, res_after.text
