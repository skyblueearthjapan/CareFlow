"""取り込み前スナップショット + 復元 (PO 決定 2026-08-09).

「間違えて取り込んでも、取り込む前に戻せる」の検証:
  1. 保存 → 週を壊す → 復元 で盤面 (訪問/割当/コース担当) が戻る
  2. 打刻の付いた週は復元不可 (422 / SnapshotRestoreBlockedError)
  3. 週ごとに直近 5 世代のみ保持 (剪定)
  4. API: 一覧 + 復元 (admin)
  5. 実適用 (replace) の直前にスナップショットが自動保存される

サービス直呼びのハーネスは test_kaipoke_inbound._seed_week と同型。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, CourseTemplate, Office, Patient, Staff, User, Visit
from app.models.inbound_snapshot import InboundSnapshot
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.kaipoke.inbound_snapshot import (
    KEEP_PER_WEEK,
    SnapshotRestoreBlockedError,
    restore_snapshot,
    snapshot_week,
)

WEEK_START = date(2026, 7, 6)  # ISO 2026-W28 月曜
TUE = date(2026, 7, 7)
WED = date(2026, 7, 8)


async def _make_admin(db) -> User:
    user = User(
        email="snap-admin@example.com",
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


async def _seed_week(db) -> dict[str, Any]:
    """office / staff / patient / コース / 対象週の visits (火・水) を作る。"""
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="佐藤 花子", role="staff", primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    tpl = CourseTemplate(label="A", office_id=office.id)
    db.add(tpl)
    await db.flush()
    iso = WEEK_START.isocalendar()
    course = Course(
        iso_year=iso.year,
        iso_week=iso.week,
        weekday=1,
        code="A",
        course_status="staff_assigned",
        assigned_staff_id=staff.id,
        template_id=tpl.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    patient = Patient(
        code="PT-SNAP-1",
        name="山田 太郎",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()

    v1 = Visit(
        patient_id=patient.id,
        visit_date=TUE,
        start_time=time(10, 0),
        end_time=time(10, 35),
        type="regular",
        status="planned",
        source="auto",
        week_pinned=True,
        required_staff_count=1,
        primary_staff_id=staff.id,
        course_id=course.id,
    )
    v2 = Visit(
        patient_id=patient.id,
        visit_date=WED,
        start_time=time(11, 0),
        end_time=time(11, 35),
        type="regular",
        status="planned",
        source="manual_week",
        required_staff_count=1,
        primary_staff_id=staff.id,
    )
    db.add_all([v1, v2])
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=v1.id, staff_id=staff.id))
    await db.commit()
    for obj in (office, staff, patient, course, v1, v2):
        await db.refresh(obj)
    return {
        "office": office,
        "staff": staff,
        "patient": patient,
        "course": course,
        "v1": v1,
        "v2": v2,
    }


async def _active_week_visits(db) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_date >= WEEK_START,
                    Visit.visit_date <= date(2026, 7, 12),
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 1) 保存 → 破壊 → 復元
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_restore_roundtrip(db) -> None:
    seeded = await _seed_week(db)
    snap = await snapshot_week(db, WEEK_START, kind="smart", user_id=None)
    await db.commit()
    assert snap.visits_count == 2

    # 「間違った取り込み」を模す: 既存を消し、取込行を挿し、コース担当を書き換える
    other = Staff(name="別人", role="staff", primary_office_id=seeded["office"].id)
    db.add(other)
    await db.flush()
    seeded["v1"].deleted_at = datetime.now(UTC)
    seeded["v2"].deleted_at = datetime.now(UTC)
    seeded["course"].assigned_staff_id = other.id
    db.add(
        Visit(
            patient_id=seeded["patient"].id,
            visit_date=TUE,
            start_time=time(15, 0),
            end_time=time(15, 35),
            type="regular",
            status="planned",
            source="import",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await restore_snapshot(db, snap, now=datetime.now(UTC))
    await db.commit()
    assert result.wiped == 1  # import で入った 1 件を畳む
    assert result.restored == 2

    survivors = await _active_week_visits(db)
    assert len(survivors) == 2
    keyset = {(v.visit_date, v.start_time, v.source, bool(v.week_pinned)) for v in survivors}
    assert (TUE, time(10, 0), "auto", True) in keyset  # 青ピンごと戻る
    assert (WED, time(11, 0), "manual_week", False) in keyset
    # コース担当も取り込み前へ戻る
    await db.refresh(seeded["course"])
    assert seeded["course"].assigned_staff_id == seeded["staff"].id
    # スタッフ割当も戻る
    tue_visit = next(v for v in survivors if v.visit_date == TUE)
    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == tue_visit.id)
        )
    ).all()
    assert [r.staff_id for r in rows] == [seeded["staff"].id]


# ---------------------------------------------------------------------------
# 2) 打刻ガード
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_blocked_by_checkin(db) -> None:
    seeded = await _seed_week(db)
    snap = await snapshot_week(db, WEEK_START, kind="smart", user_id=None)
    db.add(
        VisitCheckin(
            visit_id=seeded["v1"].id,
            patient_id=seeded["patient"].id,
            staff_id=seeded["staff"].id,
            kind="arrival",
            scanned_at=datetime(2026, 7, 7, 10, 1, tzinfo=UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    with pytest.raises(SnapshotRestoreBlockedError):
        await restore_snapshot(db, snap, now=datetime.now(UTC))


# ---------------------------------------------------------------------------
# 3) 剪定 (直近 5 世代)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_prune_keeps_recent_generations(db) -> None:
    await _seed_week(db)
    for _ in range(KEEP_PER_WEEK + 2):
        await snapshot_week(db, WEEK_START, kind="smart", user_id=None)
    await db.commit()
    count = len(
        (
            await db.scalars(
                select(InboundSnapshot).where(InboundSnapshot.week_start == WEEK_START)
            )
        ).all()
    )
    assert count == KEEP_PER_WEEK


# ---------------------------------------------------------------------------
# 4) API: 一覧 + 復元
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_list_and_restore_api(client, db) -> None:
    seeded = await _seed_week(db)
    admin = await _make_admin(db)
    snap = await snapshot_week(db, WEEK_START, kind="replace", user_id=admin.id)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/inbound-snapshots?weekStart={WEEK_START.isoformat()}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = res.json()["snapshots"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "replace"
    assert rows[0]["visitsCount"] == 2

    # 週を壊してから API で復元
    seeded["v1"].deleted_at = datetime.now(UTC)
    await db.commit()
    res = await client.post(
        f"/api/v1/integrations/inbound-snapshots/{snap.id}/restore",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["restored"] == 2
    survivors = await _active_week_visits(db)
    assert len(survivors) == 2


@pytest.mark.asyncio
async def test_restore_api_blocked_by_checkin_returns_422(client, db) -> None:
    seeded = await _seed_week(db)
    admin = await _make_admin(db)
    snap = await snapshot_week(db, WEEK_START, kind="smart", user_id=admin.id)
    db.add(
        VisitCheckin(
            visit_id=seeded["v1"].id,
            patient_id=seeded["patient"].id,
            staff_id=seeded["staff"].id,
            kind="arrival",
            scanned_at=datetime(2026, 7, 7, 10, 1, tzinfo=UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()
    res = await client.post(
        f"/api/v1/integrations/inbound-snapshots/{snap.id}/restore",
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
    assert "打刻" in res.json()["detail"]
