"""患者ステータス連動 (docs/plans/patient-status-schedule-design-2026-09-09.md §7-6 BE 1-8).

非稼働化 = 取消 (status_cancel) / 復帰 = 型から再生成、の 2 方向と、
影響件数 (dry-run) が実行結果と一致することを検証する。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.notification import Notification
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.pending_request import PendingRequest
from app.models.schedule_op_log import ScheduleOpLog
from app.models.special_visit import (
    MARK_KIND_EXTRA,
    MARK_STATUS_CANCELLED,
    MARK_STATUS_PLACED,
    MARK_STATUS_POOL,
    PERIOD_STATUS_ACTIVE,
    PERIOD_STATUS_ENDED,
    SpecialVisitMark,
    SpecialVisitPeriod,
)
from app.models.staff import Staff, StaffShift
from app.models.visit import (
    VISIT_SOURCE_STATUS_CANCEL,
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.op_log_service import (
    OpLogConflictError,
    _execute_payload,
    execute_undo,
)
from app.services.patient_status_sync import (
    AUTO_REJECT_REASON,
    apply_status_change,
    compute_impact,
    direction_for,
    is_schedulable_status,
    today_jst,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _admin(db, email: str = "status-admin@example.com") -> User:
    user = User(email=email, password_hash=hash_password("x-does-not-matter"), role="admin")
    db.add(user)
    await db.flush()
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _office(db, name: str = "status-office") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.flush()
    return office


async def _patient(db, office: Office, *, code: str = "PS-001", status: str = "active") -> Patient:
    patient = Patient(
        code=code,
        name="小湊 花子",
        status=status,
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # NULL だと既存の PatientRead (list 必須) が 500 になる = 本レーン外の既知事象。
        special_week_active=[],
    )
    db.add(patient)
    await db.flush()
    return patient


def _visit(
    patient: Patient,
    d: date,
    *,
    start: time = time(10, 0),
    status: str = VISIT_STATUS_PLANNED,
    source: str = "auto",
    week_pinned: bool = False,
    group_id: UUID | None = None,
) -> Visit:
    return Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=start,
        end_time=time(start.hour, start.minute) if False else time(start.hour + 1, start.minute),
        type="regular",
        status=status,
        source=source,
        required_staff_count=1,
        week_pinned=week_pinned,
        visit_group_id=group_id,
    )


def _checkin(visit: Visit) -> VisitCheckin:
    return VisitCheckin(
        visit_id=visit.id,
        patient_id=visit.patient_id,
        kind="arrival",
        scanned_at=datetime.now(UTC),
        match_status="match",
        threshold_snapshot={},
    )


async def _staff_with_shifts(db, office: Office) -> Staff:
    staff = Staff(name="status-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    for wd in range(5):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    await db.flush()
    return staff


# ---------------------------------------------------------------------------
# 1. 非稼働化: 対象 / 除外 / 2 名体制
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_cancels_future_planned_only(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)

    past = _visit(patient, today - timedelta(days=3), start=time(9, 0))
    future_1 = _visit(patient, today, start=time(10, 0))  # 当日から (PO 決定 Q8)
    future_2 = _visit(patient, today + timedelta(days=8), start=time(11, 0))
    completed = _visit(patient, today + timedelta(days=2), start=time(12, 0), status="completed")
    in_prog = _visit(patient, today + timedelta(days=2), start=time(17, 0), status="in_progress")
    pinned = _visit(patient, today + timedelta(days=3), start=time(13, 0), week_pinned=True)
    checked = _visit(patient, today + timedelta(days=4), start=time(14, 0))
    group_id = uuid4()
    pair_a = _visit(patient, today + timedelta(days=5), start=time(15, 0), group_id=group_id)
    pair_b = _visit(patient, today + timedelta(days=5), start=time(16, 0), group_id=group_id)
    db.add_all([past, future_1, future_2, completed, in_prog, pinned, checked, pair_a, pair_b])
    await db.flush()
    db.add(_checkin(checked))
    await db.commit()

    impact = await compute_impact(db, patient, to_status="admitted")
    assert impact.visits.excluded == {"checked_in": 1, "in_progress": 0, "week_pinned": 1}, (
        "打刻済み / 青ピンは除外理由として数える (in_progress は planned でないので対象外)"
    )
    assert impact.visits.pair_groups == 1

    result = await apply_status_change(
        db, patient, to_status="admitted", actor_user_id=admin.id, note="急変のため入院"
    )
    await db.commit()

    assert result.direction == "deactivate"
    assert result.cancelled_count == 4  # future_1 / future_2 / pair_a / pair_b
    for v in (future_1, future_2, pair_a, pair_b):
        await db.refresh(v)
        assert v.status == VISIT_STATUS_CANCELLED
        assert v.source == VISIT_SOURCE_STATUS_CANCEL
    for v in (past, pinned, checked):
        await db.refresh(v)
        assert v.status == VISIT_STATUS_PLANNED, "過去日 / 青ピン / 打刻済みは不変"
        assert v.source == "auto"
    for v in (completed, in_prog):
        await db.refresh(v)
        assert v.source == "auto", "完了 / 訪問中の実績は触らない"
    assert completed.status == "completed"
    assert in_prog.status == "in_progress"

    await db.refresh(patient)
    assert patient.status == "admitted"
    assert patient.status_changed_at is not None
    assert patient.status_changed_by == admin.id


@pytest.mark.asyncio
async def test_deactivate_impact_matches_apply(db) -> None:
    """§7-6-6: 影響件数 (dry-run) == 実行結果 (同一 selector)."""
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    pinned = _visit(patient, today + timedelta(days=1), start=time(9, 0), week_pinned=True)
    db.add_all(
        [
            _visit(patient, today + timedelta(days=1), start=time(10, 0)),
            _visit(patient, today + timedelta(days=2), start=time(10, 0), source="manual_week"),
            _visit(patient, today + timedelta(days=9), start=time(10, 0)),
            pinned,
        ]
    )
    await db.commit()

    impact = await compute_impact(db, patient, to_status="admitted")
    assert impact.direction == "deactivate"
    assert impact.visits.excluded["week_pinned"] == 1
    assert impact.visits.by_source == {"auto": 2, "manual_week": 1}
    assert sum(w.count for w in impact.visits.by_week) == impact.visits.total
    assert impact.kaipoke_weeks == len(impact.visits.by_week)

    result = await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()
    assert result.cancelled_count == impact.visits.total == 3


@pytest.mark.asyncio
async def test_pair_is_excluded_together_when_one_is_checked_in(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    group_id = uuid4()
    a = _visit(patient, today + timedelta(days=2), start=time(9, 0), group_id=group_id)
    b = _visit(patient, today + timedelta(days=2), start=time(10, 0), group_id=group_id)
    db.add_all([a, b])
    await db.flush()
    db.add(_checkin(a))
    await db.commit()

    result = await apply_status_change(db, patient, to_status="suspended", actor_user_id=admin.id)
    await db.commit()

    assert result.cancelled_count == 0, "片肺の取消を作らない (グループごと除外)"
    for v in (a, b):
        await db.refresh(v)
        assert v.status == VISIT_STATUS_PLANNED


# ---------------------------------------------------------------------------
# 2. 特別訪問週間: keep / end
# ---------------------------------------------------------------------------


async def _special_period(db, patient: Patient, placed_visit: Visit) -> SpecialVisitPeriod:
    today = today_jst()
    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=today - timedelta(days=2),
        end_date=today + timedelta(days=20),
        weekly_target=5,
        status=PERIOD_STATUS_ACTIVE,
    )
    db.add(period)
    await db.flush()
    iso = placed_visit.visit_date.isocalendar()
    db.add_all(
        [
            SpecialVisitMark(
                period_id=period.id,
                patient_id=patient.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=placed_visit.visit_date.weekday(),
                kind=MARK_KIND_EXTRA,
                status=MARK_STATUS_PLACED,
                placed_visit_id=placed_visit.id,
            ),
            SpecialVisitMark(
                period_id=period.id,
                patient_id=patient.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=(placed_visit.visit_date.weekday() + 1) % 6,
                kind=MARK_KIND_EXTRA,
                status=MARK_STATUS_POOL,
            ),
        ]
    )
    await db.flush()
    return period


@pytest.mark.asyncio
async def test_special_period_keep_leaves_marks_and_placed_visit(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    normal = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    placed = _visit(patient, today + timedelta(days=2), start=time(15, 0), source="manual_week")
    db.add_all([normal, placed])
    await db.flush()
    period = await _special_period(db, patient, placed)
    await db.commit()

    impact = await compute_impact(db, patient, to_status="admitted")
    assert impact.special_period is not None
    assert impact.special_period.pool_marks == 1
    assert impact.special_period.placed_marks == 1
    assert impact.special_period.placed_future_visits == 1
    assert impact.visits.total == 1, "keep の件数には ● 由来を含めない"

    result = await apply_status_change(
        db, patient, to_status="admitted", special_period_action="keep", actor_user_id=admin.id
    )
    await db.commit()

    assert result.cancelled_count == 1
    assert result.special_period is not None and result.special_period.action == "keep"
    await db.refresh(placed)
    await db.refresh(period)
    assert placed.status == VISIT_STATUS_PLANNED, "⭐ 配置分は一切触らない"
    assert period.status == PERIOD_STATUS_ACTIVE
    marks = (
        await db.scalars(select(SpecialVisitMark).where(SpecialVisitMark.period_id == period.id))
    ).all()
    assert {m.status for m in marks} == {MARK_STATUS_PLACED, MARK_STATUS_POOL}


@pytest.mark.asyncio
async def test_special_period_end_cancels_marks_and_placed_visit(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    normal = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    placed = _visit(patient, today + timedelta(days=2), start=time(15, 0), source="manual_week")
    db.add_all([normal, placed])
    await db.flush()
    period = await _special_period(db, patient, placed)
    await db.commit()

    result = await apply_status_change(
        db, patient, to_status="admitted", special_period_action="end", actor_user_id=admin.id
    )
    await db.commit()

    assert result.cancelled_count == 2
    assert result.special_period is not None
    assert result.special_period.action == "end"
    assert result.special_period.cancelled_pool_marks == 1
    await db.refresh(period)
    assert period.status == PERIOD_STATUS_ENDED
    assert period.end_date == max(period.start_date, today - timedelta(days=1))
    await db.refresh(placed)
    assert placed.status == VISIT_STATUS_CANCELLED
    marks = (
        await db.scalars(select(SpecialVisitMark).where(SpecialVisitMark.period_id == period.id))
    ).all()
    assert {m.status for m in marks} == {MARK_STATUS_CANCELLED}


# ---------------------------------------------------------------------------
# 3. op-log (週ごとに 1 グループ) + undo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_op_log_one_group_per_week_and_undo_is_blocked(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    this_week = _visit(patient, today + timedelta(days=1), start=time(9, 0), source="reset_v2")
    next_week = _visit(patient, today + timedelta(days=8), start=time(9, 0), source="manual_week")
    db.add_all([this_week, next_week])
    await db.commit()

    result = await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()

    assert len(result.op_groups) == 2
    assert len({g.op_group_id for g in result.op_groups}) == 2
    rows = (await db.scalars(select(ScheduleOpLog))).all()
    assert len(rows) == 2
    assert {r.op_kind for r in rows} == {"cancel_visit"}
    assert all(r.forward_payload["cancel_source"] == VISIT_SOURCE_STATUS_CANCEL for r in rows)

    # ステータス連動の取消は「戻る」で戻せない (どちらの向きも 409)。
    ref = next(
        g for g in result.op_groups if g.iso_week == (today + timedelta(days=8)).isocalendar().week
    )
    with pytest.raises(OpLogConflictError) as exc:
        await execute_undo(db, user_id=admin.id, iso_year=ref.iso_year, iso_week=ref.iso_week)
    assert "稼働中に戻して" in str(exc.value.detail)
    await db.rollback()
    # 訪問は取消されたまま (中途半端に戻らない)。
    await db.refresh(next_week)
    assert next_week.status == VISIT_STATUS_CANCELLED
    assert next_week.source == VISIT_SOURCE_STATUS_CANCEL


@pytest.mark.asyncio
async def test_status_cancel_payloads_are_blocked_in_both_directions(db) -> None:
    """forward (やり直す) / inverse (戻る) のどちらの payload も 409 になる.

    undo が塞がっている以上 redo 枝は作れないので、``_execute_payload`` を
    両向きに直接叩いてガードを確かめる (executor 側の単一ソース)。
    記録行そのものは監査のため残す。
    """
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    db.add(_visit(patient, today + timedelta(days=8), start=time(9, 0)))
    await db.commit()

    await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()

    rows = list((await db.scalars(select(ScheduleOpLog))).all())
    assert len(rows) == 1, "記録は監査のために残る"
    row = rows[0]
    assert row.forward_payload["cancel_source"] == VISIT_SOURCE_STATUS_CANCEL
    assert row.inverse_payload["cancel_source"] == VISIT_SOURCE_STATUS_CANCEL

    for payload in (row.forward_payload, row.inverse_payload):
        with pytest.raises(OpLogConflictError) as exc:
            await _execute_payload(db, payload)
        assert "稼働中に戻して" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# 4. 申請の自動却下 + 通知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_requests_rejected_and_admins_notified(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    requester = await _admin(db, email="requester@example.com")
    patient = await _patient(db, office)
    db.add(_visit(patient, today + timedelta(days=1), start=time(9, 0)))
    req = PendingRequest(
        requester_user_id=requester.id,
        request_type="visit_reschedule",
        payload={},
        target_patient_id=patient.id,
        status="pending",
    )
    db.add(req)
    await db.commit()

    result = await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()

    assert result.rejected_requests == 1
    await db.refresh(req)
    assert req.status == "rejected"
    assert req.rejection_reason == AUTO_REJECT_REASON
    assert req.rejected_at is not None
    assert req.rejected_by == admin.id

    notes = (await db.scalars(select(Notification))).all()
    admin_notes = [n for n in notes if n.reference_type == "patient"]
    req_notes = [n for n in notes if n.reference_type == "pending_request"]
    assert len(admin_notes) == 2, "active admin 全員へ (操作者本人も含む)"
    assert result.notification_count == 2
    assert all(n.type == "patient_status_sync" for n in admin_notes)
    assert all(n.reference_id is None for n in notes), "部分 UNIQUE を避けて毎回 1 通残す"
    assert "入院中" in admin_notes[0].title
    body = admin_notes[0].body or ""
    assert "カイポケの週間パターンを停止してください" in body
    assert str(patient.id) in body
    # 却下通知は **変更後の** ステータスで書く (古い「稼働中」と書かない)。
    assert len(req_notes) == 1
    req_body = req_notes[0].body or ""
    assert "入院中" in req_body
    assert "稼働中" not in req_body
    assert AUTO_REJECT_REASON in req_body


@pytest.mark.asyncio
async def test_second_status_change_adds_a_second_notification(db) -> None:
    """reference_id=None で入れるので部分 UNIQUE に当たらない (変更のたびに 1 通)."""
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    await db.commit()

    await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()
    await apply_status_change(db, patient, to_status="active", actor_user_id=admin.id)
    await db.commit()

    notes = list((await db.scalars(select(Notification))).all())
    assert len(notes) == 2
    titles = sorted(n.title for n in notes)
    assert any("入院中" in t for t in titles)
    assert any("稼働中" in t for t in titles)


# ---------------------------------------------------------------------------
# 5. 復帰 (型から再生成)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reactivate_regenerates_from_fixed_visits(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    await _staff_with_shifts(db, office)
    patient = await _patient(db, office)
    for wd in range(5):
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=35,
                slot_index=0,
            )
        )
    # 生成済みの週 (来週) に予定がある = 週生成が回った週。
    next_week_monday = today + timedelta(days=(7 - today.weekday()))
    for wd in range(5):
        db.add(_visit(patient, next_week_monday + timedelta(days=wd), start=time(10, 0)))
    await db.commit()

    await apply_status_change(db, patient, to_status="admitted", actor_user_id=admin.id)
    await db.commit()
    cancelled = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.source == VISIT_SOURCE_STATUS_CANCEL,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(cancelled) == 5

    result = await apply_status_change(
        db, patient, to_status="active", regenerate=True, actor_user_id=admin.id
    )
    await db.commit()

    assert result.direction == "reactivate"
    assert result.regenerated is not None
    assert result.regenerated.created >= 1, "型から作り直す"
    # status_cancel は soft-delete されている。
    left = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.source == VISIT_SOURCE_STATUS_CANCEL,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert left == []
    # from_date (今日) より前の日は 1 件も作られていない。
    before = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
                Visit.visit_date < today,
            )
        )
    ).all()
    assert before == []
    await db.refresh(patient)
    assert patient.status == "active"


@pytest.mark.asyncio
async def test_reactivate_without_office_is_422(db) -> None:
    from fastapi import HTTPException

    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office, status="admitted")
    patient.primary_office_id = None
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await apply_status_change(
            db, patient, to_status="active", regenerate=True, actor_user_id=admin.id
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# 7. バリデーション / direction=none
# ---------------------------------------------------------------------------


def test_direction_and_schedulable_helpers() -> None:
    assert is_schedulable_status("active") is True
    for s in ("suspended", "admitted", "pending", "cancelled", None):
        assert is_schedulable_status(s) is False
    assert direction_for("active", "admitted") == "deactivate"
    assert direction_for("admitted", "active") == "reactivate"
    assert direction_for("admitted", "suspended") == "none"
    assert direction_for("active", "active") == "none"


@pytest.mark.asyncio
async def test_past_from_date_is_422(db) -> None:
    from fastapi import HTTPException

    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await apply_status_change(
            db,
            patient,
            to_status="admitted",
            from_date=today_jst() - timedelta(days=1),
            actor_user_id=admin.id,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_direction_none_is_noop(db) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office, status="admitted")
    v = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    db.add(v)
    await db.commit()

    result = await apply_status_change(db, patient, to_status="suspended", actor_user_id=admin.id)
    await db.commit()

    assert result.direction == "none"
    assert result.cancelled_count == 0
    assert result.notification_count == 0
    await db.refresh(v)
    assert v.status == VISIT_STATUS_PLANNED
    await db.refresh(patient)
    assert patient.status == "suspended"


# ---------------------------------------------------------------------------
# 8. 入口: API (PATCH / status-change / status-impact) と申請適用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_patient_status_triggers_sync(db, client) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    v = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    db.add(v)
    await db.commit()

    res = await client.patch(
        f"/api/v1/patients/{patient.id}",
        json={"status": "admitted"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "admitted"

    await db.refresh(v)
    assert v.status == VISIT_STATUS_CANCELLED
    assert v.source == VISIT_SOURCE_STATUS_CANCEL


@pytest.mark.asyncio
async def test_status_impact_and_status_change_endpoints(db, client) -> None:
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    v = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    db.add(v)
    await db.commit()

    res = await client.get(
        f"/api/v1/patients/{patient.id}/status-impact",
        params={"to": "admitted"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["direction"] == "deactivate"
    assert body["visits"]["total"] == 1
    assert body["from_date"] == today.isoformat()

    res = await client.post(
        f"/api/v1/patients/{patient.id}/status-change",
        json={"status": "admitted", "special_period_action": "keep"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["direction"] == "deactivate"
    assert body["cancelled_count"] == 1
    assert body["patient"]["status"] == "admitted"
    assert body["patient"]["status_changed_at"] is not None

    await db.refresh(v)
    assert v.status == VISIT_STATUS_CANCELLED


@pytest.mark.asyncio
async def test_status_change_past_from_date_returns_422(db, client) -> None:
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{patient.id}/status-change",
        json={
            "status": "admitted",
            "from_date": (today_jst() - timedelta(days=1)).isoformat(),
        },
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_pending_request_applier_triggers_sync(db) -> None:
    from app.services.pending_request_applier import PendingRequestApplier

    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office)
    v = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    db.add(v)
    req = PendingRequest(
        requester_user_id=admin.id,
        request_type="patient_status_update",
        payload={"patient_id": str(patient.id), "status": "admitted"},
        target_patient_id=patient.id,
        status="pending",
    )
    db.add(req)
    await db.commit()

    await PendingRequestApplier().apply(db, req)
    await db.commit()

    await db.refresh(v)
    assert v.status == VISIT_STATUS_CANCELLED
    assert v.source == VISIT_SOURCE_STATUS_CANCEL
    await db.refresh(req)
    assert req.status == "pending", "適用中のこの申請自身は自動却下しない"


@pytest.mark.asyncio
async def test_reactivate_impact_matches_apply(db) -> None:
    """B1: 復帰の見込み件数 (dry-run) == 実行の作成件数 (同じ再生成関数)."""
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    await _staff_with_shifts(db, office)
    # 開始前 (pending) = 非稼働。status_cancel は 1 件も無い状態から復帰する。
    patient = await _patient(db, office, code="PS-B1", status="pending")
    for wd in range(5):
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=35,
                slot_index=0,
            )
        )
    # 「生成済みの週」の証拠 = 同じ拠点の別患者の予定。
    other = await _patient(db, office, code="PS-B1-OTHER")
    next_monday = today + timedelta(days=(7 - today.weekday()))
    db.add(_visit(other, next_monday, start=time(9, 0)))
    await db.commit()

    # 試走 (rollback) で session の ORM は expire する → id は先に控える。
    admin_id = admin.id

    impact = await compute_impact(db, patient, to_status="active")
    assert impact.direction == "reactivate"
    assert impact.regenerate is not None
    assert impact.regenerate.total >= 1, "型があるので作られる見込みが立つ"

    result = await apply_status_change(
        db, patient, to_status="active", regenerate=True, actor_user_id=admin_id
    )
    await db.commit()
    assert result.regenerated is not None
    assert result.regenerated.created == impact.regenerate.total
    assert [(w.iso_year, w.iso_week, w.count) for w in result.regenerated.weeks] == [
        (w.iso_year, w.iso_week, w.count) for w in impact.regenerate.weeks
    ]


@pytest.mark.asyncio
async def test_reactivate_keeps_pre_from_date_visit_and_its_staff(db) -> None:
    """M3: from_date より前の日は訪問も担当行もそのまま残す (穴も担当落ちも作らない)."""
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    staff = await _staff_with_shifts(db, office)
    patient = await _patient(db, office, code="PS-M3")
    for wd in range(5):
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=35,
                slot_index=0,
            )
        )
    # 来週の月曜 (= from_date より前) と木曜 (= from_date 以降) に予定を置く。
    next_monday = today + timedelta(days=(7 - today.weekday()))
    from_date = next_monday + timedelta(days=2)  # 週の途中 (水曜)
    keeper = _visit(patient, next_monday, start=time(10, 0))
    target = _visit(patient, next_monday + timedelta(days=3), start=time(10, 0))
    db.add_all([keeper, target])
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=keeper.id, staff_id=staff.id))
    await db.commit()
    keeper_id = keeper.id

    await apply_status_change(
        db, patient, to_status="admitted", from_date=from_date, actor_user_id=admin.id
    )
    await db.commit()
    await db.refresh(keeper)
    await db.refresh(target)
    assert keeper.status == VISIT_STATUS_PLANNED, "from_date より前は取消さない"
    assert target.status == VISIT_STATUS_CANCELLED

    result = await apply_status_change(
        db,
        patient,
        to_status="active",
        from_date=from_date,
        regenerate=True,
        actor_user_id=admin.id,
    )
    await db.commit()
    assert result.regenerated is not None and result.regenerated.created >= 1

    # 元の月曜の訪問はそのまま生きていて、担当も付いたまま。
    await db.refresh(keeper)
    assert keeper.deleted_at is None
    assert keeper.status == VISIT_STATUS_PLANNED
    assigned = (
        await db.scalars(
            select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == keeper_id)
        )
    ).all()
    assert list(assigned) == [staff.id], "再生成で担当行が落ちない (貼り直す)"

    # from_date より前に新しい訪問は 1 件も作られていない。
    before = list(
        (
            await db.scalars(
                select(Visit.id).where(
                    Visit.patient_id == patient.id,
                    Visit.deleted_at.is_(None),
                    Visit.visit_date < from_date,
                )
            )
        ).all()
    )
    assert before == [keeper_id]


@pytest.mark.asyncio
async def test_status_impact_special_period_action_end(db, client) -> None:
    """m2: ?special_period_action=end なら ⭐ 配置分も visits.total に含む."""
    today = today_jst()
    office = await _office(db)
    admin = await _admin(db)
    patient = await _patient(db, office, code="PS-M2")
    normal = _visit(patient, today + timedelta(days=1), start=time(9, 0))
    placed = _visit(patient, today + timedelta(days=2), start=time(15, 0), source="manual_week")
    db.add_all([normal, placed])
    await db.flush()
    await _special_period(db, patient, placed)
    await db.commit()

    keep = await client.get(
        f"/api/v1/patients/{patient.id}/status-impact",
        params={"to": "admitted"},
        headers=_bearer(admin),
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["visits"]["total"] == 1
    assert keep.json()["special_period"]["placed_future_visits"] == 1

    end = await client.get(
        f"/api/v1/patients/{patient.id}/status-impact",
        params={"to": "admitted", "special_period_action": "end"},
        headers=_bearer(admin),
    )
    assert end.status_code == 200, end.text
    assert end.json()["visits"]["total"] == 2
    assert end.json()["special_period"]["placed_future_visits"] == 1
