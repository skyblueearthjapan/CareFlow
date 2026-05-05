"""W2-BE5: PendingRequestApplier の業務反映ハンドラ試験.

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §9.2 に対応する受入テスト。

検証観点:
  1. 9 種類の request_type 全てで applier が業務テーブルを正しく更新する
  2. 冪等性 (受入基準 4): 同一申請の二重 apply で 1 回しか反映されない
  3. 失敗時の rollback (受入基準 5): 必要パラメータ欠落で例外 + 副作用なし
  4. patient_reschedule の scope=permanent で weekly_pattern が更新される
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import select

from app.models import Patient, Staff, User
from app.models.pending_request import PendingRequest
from app.models.staff import StaffEvent, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit
from app.services.pending_request_applier import (
    PendingRequestApplier,
    PendingRequestApplyError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str = "applier-admin@example.com") -> User:
    user = User(email=email, password_hash="x", role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, name: str = "鈴木") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_patient(db, *, code: str = "P-APL-001") -> Patient:
    p = Patient(code=code, name="患者", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_pending(
    db,
    *,
    requester: User,
    request_type: str,
    payload: dict,
    target_staff_id=None,
    target_patient_id=None,
    target_date: date | None = None,
    scope: str | None = None,
) -> PendingRequest:
    row = PendingRequest(
        requester_user_id=requester.id,
        request_type=request_type,
        payload=payload,
        target_staff_id=target_staff_id,
        target_patient_id=target_patient_id,
        target_date=target_date,
        scope=scope,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 1) staff_off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_off_creates_override(db) -> None:
    user = await _make_user(db, "apl-staffoff@example.com")
    staff = await _make_staff(db)
    target = date(2026, 5, 11)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_off",
        payload={
            "staff_id": str(staff.id),
            "date": target.isoformat(),
            "override_type": "off",
            "reason": "私用",
        },
        target_staff_id=staff.id,
        target_date=target,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(
        select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
    )
    assert row is not None
    iso = target.isocalendar()
    assert row.iso_year == iso.year
    assert row.iso_week == iso.week
    assert row.weekday == target.weekday()
    assert row.override_type == "off"


# ---------------------------------------------------------------------------
# 2) staff_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_event_creates_event(db) -> None:
    user = await _make_user(db, "apl-event@example.com")
    staff = await _make_staff(db)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_event",
        payload={
            "staff_id": str(staff.id),
            "date": "2026-05-11",
            "start_time": "13:00",
            "end_time": "14:00",
            "event_type": "meeting",
            "title": "管理者会議",
        },
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    ev = await db.scalar(select(StaffEvent).where(StaffEvent.staff_id == staff.id))
    assert ev is not None
    assert ev.title == "管理者会議"
    assert ev.starts_at.hour == 13


# ---------------------------------------------------------------------------
# 3) staff_mentor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_mentor_updates_mentor_id(db) -> None:
    user = await _make_user(db, "apl-mentor@example.com")
    mentee = await _make_staff(db, name="新人")
    mentor = await _make_staff(db, name="先輩")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={"staff_id": str(mentee.id), "mentor_id": str(mentor.id)},
        target_staff_id=mentee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(mentee)
    assert mentee.mentor_id == mentor.id


# ---------------------------------------------------------------------------
# 4) staff_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_create_inserts_staff(db) -> None:
    user = await _make_user(db, "apl-staffcreate@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_create",
        payload={
            "name": "新人 一郎",
            "kana": "シンジン イチロウ",
            "role": "staff",
            "status": "active",
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(select(Staff).where(Staff.name == "新人 一郎"))
    assert row is not None
    assert row.kana == "シンジン イチロウ"


# ---------------------------------------------------------------------------
# 5) patient_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_create_inserts_patient(db) -> None:
    user = await _make_user(db, "apl-pcreate@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-NEW-001",
            "name": "田中 太郎",
            "kana": "タナカ タロウ",
            "status": "active",
            "weekly_pattern": {
                "frequency_per_week": 2,
                "preferred_weekdays": ["Mon", "Thu"],
            },
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(select(Patient).where(Patient.code == "P-NEW-001"))
    assert row is not None
    assert row.name == "田中 太郎"


# ---------------------------------------------------------------------------
# 6) patient_cancel
# ---------------------------------------------------------------------------


async def _make_visit(
    db, *, patient: Patient, visit_date: date, hh_start: int = 10, hh_end: int = 11
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=time(hh_start, 0),
        end_time=time(hh_end, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@pytest.mark.asyncio
async def test_apply_patient_cancel_marks_visit_cancelled(db) -> None:
    user = await _make_user(db, "apl-cancel@example.com")
    patient = await _make_patient(db)
    target = date(2026, 5, 11)
    visit = await _make_visit(db, patient=patient, visit_date=target)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_cancel",
        payload={"visit_id": str(visit.id)},
        target_patient_id=patient.id,
        target_date=target,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(visit)
    assert visit.status == VISIT_STATUS_CANCELLED


@pytest.mark.asyncio
async def test_apply_patient_cancel_by_patient_and_date(db) -> None:
    """visit_id を渡さず patient_id + date で特定するパス."""
    user = await _make_user(db, "apl-cancel2@example.com")
    patient = await _make_patient(db, code="P-CXL-2")
    target = date(2026, 5, 12)
    visit = await _make_visit(db, patient=patient, visit_date=target)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_cancel",
        payload={"patient_id": str(patient.id), "date": target.isoformat()},
        target_patient_id=patient.id,
        target_date=target,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(visit)
    assert visit.status == VISIT_STATUS_CANCELLED


# ---------------------------------------------------------------------------
# 7) patient_reschedule (scope=one_time)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_reschedule_one_time_updates_visit_only(db) -> None:
    user = await _make_user(db, "apl-resched1@example.com")
    patient = await _make_patient(db, code="P-RES-1")
    patient.weekly_pattern = {"frequency_per_week": 1}
    await db.commit()

    target = date(2026, 5, 11)
    visit = await _make_visit(db, patient=patient, visit_date=target)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_reschedule",
        payload={
            "visit_id": str(visit.id),
            "new_start_time": "14:00",
            "new_end_time": "15:00",
        },
        target_patient_id=patient.id,
        target_date=target,
        scope="one_time",
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(visit)
    await db.refresh(patient)
    assert visit.start_time == time(14, 0)
    assert visit.end_time == time(15, 0)
    # one_time なので weekly_pattern は触らない
    assert patient.weekly_pattern == {"frequency_per_week": 1}


# ---------------------------------------------------------------------------
# 8) patient_reschedule (scope=permanent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_reschedule_permanent_updates_weekly_pattern(db) -> None:
    user = await _make_user(db, "apl-resched2@example.com")
    patient = await _make_patient(db, code="P-RES-2")
    patient.weekly_pattern = {"frequency_per_week": 1}
    await db.commit()

    target = date(2026, 5, 11)
    visit = await _make_visit(db, patient=patient, visit_date=target)

    new_pattern = {"frequency_per_week": 2, "preferred_weekdays": ["Mon", "Fri"]}
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_reschedule",
        payload={
            "visit_id": str(visit.id),
            "new_start_time": "10:00",
            "new_end_time": "11:00",
            "new_weekly_pattern": new_pattern,
        },
        target_patient_id=patient.id,
        target_date=target,
        scope="permanent",
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(patient)
    assert patient.weekly_pattern == new_pattern


@pytest.mark.asyncio
async def test_apply_patient_reschedule_missing_scope_raises(db) -> None:
    user = await _make_user(db, "apl-resched3@example.com")
    patient = await _make_patient(db, code="P-RES-3")
    target = date(2026, 5, 11)
    visit = await _make_visit(db, patient=patient, visit_date=target)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_reschedule",
        payload={
            "visit_id": str(visit.id),
            "new_start_time": "10:00",
            "new_end_time": "11:00",
        },
        target_patient_id=patient.id,
        target_date=target,
        scope=None,  # 意図的に欠落
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError):
        await applier.apply(db, pr)


# ---------------------------------------------------------------------------
# 9) patient_special_week_on / off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_special_week_on_appends(db) -> None:
    user = await _make_user(db, "apl-spwon@example.com")
    patient = await _make_patient(db, code="P-SP-ON")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_special_week_on",
        payload={
            "patient_id": str(patient.id),
            "iso_year": 2026,
            "iso_week": 20,
            "special_weekly_pattern": {"frequency_per_week": 3},
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(patient)

    assert patient.special_weekly_pattern == {"frequency_per_week": 3}
    weeks = patient.special_week_active or []
    assert any(w["iso_year"] == 2026 and w["iso_week"] == 20 for w in weeks)


@pytest.mark.asyncio
async def test_apply_special_week_off_removes(db) -> None:
    user = await _make_user(db, "apl-spwoff@example.com")
    patient = await _make_patient(db, code="P-SP-OFF")
    patient.special_week_active = [{"iso_year": 2026, "iso_week": 20}]
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_special_week_off",
        payload={
            "patient_id": str(patient.id),
            "iso_year": 2026,
            "iso_week": 20,
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(patient)
    assert all(
        not (w["iso_year"] == 2026 and w["iso_week"] == 20)
        for w in (patient.special_week_active or [])
    )


# ---------------------------------------------------------------------------
# 10) 冪等性: 二重 apply で 1 回しか反映されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_double_apply(db) -> None:
    user = await _make_user(db, "apl-idem@example.com")
    staff = await _make_staff(db)
    target = date(2026, 5, 11)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_off",
        payload={"staff_id": str(staff.id), "date": target.isoformat()},
        target_staff_id=staff.id,
        target_date=target,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    # 1 回目の apply の後に呼び出し側が status / approved_at をセットする想定。
    # Applier は status="approved" + approved_at IS NOT NULL のとき no-op になる。
    pr.status = "approved"
    pr.approved_at = datetime.utcnow()
    pr.approved_by = user.id
    await db.commit()

    # 2 回目: 副作用が増えないこと
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 11) 失敗時の rollback (必須パラメータ欠落)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_required_params_raise(db) -> None:
    user = await _make_user(db, "apl-fail@example.com")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_off",
        payload={"override_type": "off"},  # staff_id / date 欠落
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_unknown_request_type_raises(db) -> None:
    user = await _make_user(db, "apl-unknown@example.com")
    pr = PendingRequest(
        requester_user_id=user.id,
        request_type="unknown_type",
        payload={},
        status="pending",
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError):
        await applier.apply(db, pr)


# ---------------------------------------------------------------------------
# 12) edited_payload があるとそちらを優先する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edited_payload_overrides_payload(db) -> None:
    user = await _make_user(db, "apl-edit@example.com")
    staff = await _make_staff(db)
    target = date(2026, 5, 11)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_off",
        payload={"staff_id": str(staff.id), "date": target.isoformat(), "reason": "原"},
        target_staff_id=staff.id,
        target_date=target,
    )
    pr.edited_payload = {
        "staff_id": str(staff.id),
        "date": target.isoformat(),
        "reason": "編集後",
    }
    await db.commit()

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(
        select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
    )
    assert row is not None
    assert row.reason == "編集後"


# ---------------------------------------------------------------------------
# 13) E2E 風: applier 呼び出し成功 + 失敗時の rollback (HTTP 経由)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_approve_failure_rollback(client, db) -> None:
    """HTTP layer が applier の例外で rollback することの確認.

    payload 必須キー欠落で applier が PendingRequestApplyError を投げ、
    ステータスは pending のまま、業務テーブルにも追加が無いこと。
    """
    from app.core.security import create_access_token, hash_password

    admin = User(
        email="apl-rb-admin@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token = create_access_token(subject=admin.id, role="admin", staff_id=None)
    headers = {"Authorization": f"Bearer {token}"}

    # staff_id 抜きの staff_off 申請を作成 (POST 自体は通る)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=headers,
        json={
            "request_type": "staff_off",
            "payload": {"date": "2026-05-11"},
        },
    )
    assert res.status_code == 201, res.text
    pr_id = res.json()["id"]

    # approve は applier 内で 422 になる
    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=headers,
        json={},
    )
    assert res.status_code == 422, res.text

    # ステータスは pending のまま
    res = await client.get(f"/api/v1/pending-requests/{pr_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "pending"

    # 副作用なし
    rows = (await db.scalars(select(StaffWeeklyOverride))).all()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# 14) handler 9 種網羅サニティ (登録漏れ防止)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_nine_request_types_have_handlers() -> None:
    from app.services.pending_request_applier import _HANDLERS

    expected = {
        "staff_off",
        "staff_event",
        "staff_mentor",
        "staff_create",
        "patient_create",
        "patient_cancel",
        "patient_reschedule",
        "patient_special_week_on",
        "patient_special_week_off",
    }
    assert set(_HANDLERS.keys()) == expected
