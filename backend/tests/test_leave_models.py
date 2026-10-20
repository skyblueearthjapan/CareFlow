"""休みの表 (mig 0099) のモデルとスキーマ: create_all の試験の DB で書いて読める."""

from __future__ import annotations

from datetime import date, time

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.models import (
    GoogleDriveConnection,
    KaipokeLeaveJudgement,
    KinmuSheet,
    LeaveCompareRun,
    LeaveMonth,
    OfficeClosedDay,
    PendingRequest,
    Staff,
    StaffLeaveKaipoke,
    StaffWeeklyOverride,
    User,
)
from app.schemas.leave import (
    GoogleDriveConnectionRead,
    KaipokeLeaveJudgementRead,
    KinmuSheetRead,
    LeaveCompareRunRead,
    LeaveMonthRead,
    OfficeClosedDayRead,
    StaffLeaveKaipokeRead,
)
from app.schemas.staff import StaffRead
from app.schemas.v2.enums import RequestType


@pytest.mark.asyncio
async def test_leave_tables_round_trip(db) -> None:
    user = User(email="leave-models@example.com", password_hash="x", role="admin")
    staff = Staff(name="休み花子", work_form="A", admin_hours_per_day=4.0, kinmu_title="管理者")
    db.add_all([user, staff])
    await db.flush()

    run = LeaveCompareRun(
        month=date(2026, 10, 1),
        fetched_at={"2026-09-28": "2026-10-20T10:00:00+09:00"},
        diffs=[{"kind": "kaipoke_only", "staff_id": str(staff.id)}],
        diff_count=1,
        created_by=user.id,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            OfficeClosedDay(date=date(2026, 12, 30), label="年末年始", created_by=user.id),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=date(2026, 10, 2), sent_type="off"),
            KaipokeLeaveJudgement(
                staff_id=staff.id,
                event_date=date(2026, 10, 5),
                content_hash="a" * 64,
                subject="休み",
                starts=time(10, 0),
                ends=time(11, 0),
                chosen_kind="not_leave",
                decided_by=user.id,
            ),
            LeaveMonth(month=date(2026, 10, 1), required_hours=160, last_compare_run_id=run.id),
            KinmuSheet(
                month=date(2026, 10, 1),
                kind="final",
                version=2,
                drive_file_id="f1",
                url="https://x",
                reason="実績の時間を合わせた",
            ),
            GoogleDriveConnection(folder_id="folder", connected_by=user.id, status="connected"),
        ]
    )
    await db.commit()

    assert StaffRead.model_validate(staff).employment_type == "full_time"
    assert StaffRead.model_validate(staff).admin_hours_per_day == 4.0
    OfficeClosedDayRead.model_validate(await db.get(OfficeClosedDay, date(2026, 12, 30)))
    leave = await db.scalar(select(StaffLeaveKaipoke))
    read = StaffLeaveKaipokeRead.model_validate(leave)
    assert read.sent_type == "off" and read.baseline_pending is False
    assert (
        KaipokeLeaveJudgementRead.model_validate(
            await db.scalar(select(KaipokeLeaveJudgement))
        ).chosen_kind
        == "not_leave"
    )
    assert LeaveCompareRunRead.model_validate(run).diff_count == 1
    month = LeaveMonthRead.model_validate(await db.get(LeaveMonth, date(2026, 10, 1)))
    assert month.required_hours == 160 and month.last_compare_run_id == run.id
    sheet = KinmuSheetRead.model_validate(await db.scalar(select(KinmuSheet)))
    assert (sheet.version, sheet.reason) == (2, "実績の時間を合わせた")
    conn = GoogleDriveConnectionRead.model_validate(await db.scalar(select(GoogleDriveConnection)))
    assert conn.status == "connected"
    assert "refresh_token_encrypted" not in conn.model_dump()


@pytest.mark.asyncio
async def test_override_source_and_request_link(db) -> None:
    user = User(email="leave-models2@example.com", password_hash="x", role="staff")
    staff = Staff(name="申請太郎")
    db.add_all([user, staff])
    await db.flush()
    req = PendingRequest(
        requester_user_id=user.id,
        request_type="staff_off",
        payload={"override_type": "paid_off"},
        target_staff_id=staff.id,
    )
    db.add(req)
    await db.flush()
    db.add(
        StaffWeeklyOverride(
            staff_id=staff.id,
            iso_year=2026,
            iso_week=45,
            weekday=2,
            override_type="paid_off",
            source="request",
            pending_request_id=req.id,
        )
    )
    await db.commit()
    row = await db.scalar(select(StaffWeeklyOverride))
    assert (row.source, row.pending_request_id) == ("request", req.id)

    with pytest.raises(sa.exc.IntegrityError):
        db.add(
            StaffWeeklyOverride(
                staff_id=staff.id,
                iso_year=2026,
                iso_week=45,
                weekday=3,
                override_type="off",
                source="excel",
            )
        )
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_pending_request_check_accepts_all_app_types(db) -> None:
    user = User(email="leave-models3@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    for t in RequestType:
        db.add(PendingRequest(requester_user_id=user.id, request_type=t.value, payload={}))
    await db.commit()

    with pytest.raises(sa.exc.IntegrityError):
        db.add(PendingRequest(requester_user_id=user.id, request_type="visit_reschedule"))
        await db.commit()
    await db.rollback()


def test_month_start_schema_aligns_to_first_day() -> None:
    from app.schemas.leave import month_start

    assert month_start(date(2026, 10, 15)) == date(2026, 10, 1)
    assert LeaveMonthRead.model_validate({"month": date(2026, 2, 28)}).month == date(2026, 2, 1)


def test_month_check_is_postgres_only() -> None:
    """月の 1 日だけの CHECK は PG の DDL にだけ出る (SQLite の試験の DB は作れる)."""
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    for model in (LeaveMonth, LeaveCompareRun, KinmuSheet):
        pg = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        lite = str(CreateTable(model.__table__).compile(dialect=sqlite.dialect()))
        assert "EXTRACT(DAY FROM month) = 1" in pg
        assert f"ck_{model.__tablename__}_month_first_day" in pg
        assert "EXTRACT" not in lite


@pytest.mark.asyncio
async def test_google_drive_connection_is_single_row(db) -> None:
    db.add(GoogleDriveConnection(status="connected"))
    await db.commit()
    with pytest.raises(sa.exc.IntegrityError):
        db.add(GoogleDriveConnection(status="connected"))
        await db.commit()
    await db.rollback()
    with pytest.raises(sa.exc.IntegrityError):
        db.add(GoogleDriveConnection(status="connected", is_singleton=False))
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_staff_patch_employment_type_null_is_422(client, db) -> None:
    from app.core.security import create_access_token, hash_password

    admin = User(email="leave-models4@example.com", password_hash=hash_password("x"), role="admin")
    staff = Staff(name="常勤一郎")
    db.add_all([admin, staff])
    await db.commit()
    token = create_access_token(subject=admin.id, role=admin.role, staff_id=None)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.patch(
        f"/api/v1/staff/{staff.id}", headers=headers, json={"employment_type": None}
    )
    assert res.status_code == 422, res.text
    res = await client.patch(
        f"/api/v1/staff/{staff.id}",
        headers=headers,
        json={"employment_type": "part_time", "work_form": "C", "admin_hours_per_day": 4},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["employment_type"], body["work_form"], body["admin_hours_per_day"]) == (
        "part_time",
        "C",
        4.0,
    )
