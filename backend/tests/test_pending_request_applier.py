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
from app.models.staff_companion_assignment import StaffCompanionAssignment
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
async def test_apply_staff_mentor_updates_is_trainee_only(db) -> None:
    """W10-BE1 互換: mode A のみ (is_trainee=true) → フラグ更新のみ。回帰テスト。"""
    user = await _make_user(db, "apl-mentor@example.com")
    mentee = await _make_staff(db, name="新人")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={"staff_id": str(mentee.id), "is_trainee": True},
        target_staff_id=mentee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(mentee)
    assert mentee.is_trainee is True

    # companion_assignments は一切触っていない
    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == mentee.id
            )
        )
    ).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_apply_staff_mentor_clears_is_trainee_when_false(db) -> None:
    """W11-BE: mode A で is_trainee=false → 新人フラグを解除できる (Reviewer M-1 補完)."""
    user = await _make_user(db, "apl-mentor-off@example.com")
    # 既に新人フラグ ON のスタッフ
    mentee = Staff(name="卒業予定", is_trainee=True)
    db.add(mentee)
    await db.commit()
    await db.refresh(mentee)
    assert mentee.is_trainee is True

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={"staff_id": str(mentee.id), "is_trainee": False},
        target_staff_id=mentee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(mentee)
    assert mentee.is_trainee is False


# ---------------------------------------------------------------------------
# W11-BE: staff_mentor mode B (assignments[]) テスト
# ---------------------------------------------------------------------------


async def _make_active_staff(
    db,
    name: str,
    *,
    role: str = "staff",
    status: str = "active",
    is_trainee: bool = False,
) -> Staff:
    s = Staff(name=name, role=role, status=status, is_trainee=is_trainee)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_single(db) -> None:
    """mode B: assignments=[1件] → 1行INSERT + is_trainee 自動 true 化。"""
    user = await _make_user(db, "apl-asgn1@example.com")
    trainee = await _make_active_staff(db, "新人A")
    companion = await _make_active_staff(db, "先輩A")
    assert trainee.is_trainee is False

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [{"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)}],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(trainee)

    # is_trainee 強制 true 化
    assert trainee.is_trainee is True

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == trainee.id
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].weekday == 0
    assert rows[0].part == "am"
    assert rows[0].companion_staff_id == companion.id


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_7_full(db) -> None:
    """mode B: assignments=[7件 full] → 7行INSERT (全曜日終日)。"""
    user = await _make_user(db, "apl-asgn7@example.com")
    trainee = await _make_active_staff(db, "新人B")
    companion = await _make_active_staff(db, "先輩B")

    assignments = [
        {"weekday": wd, "part": "full", "companion_staff_id": str(companion.id)} for wd in range(7)
    ]

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={"staff_id": str(trainee.id), "assignments": assignments},
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == trainee.id
            )
        )
    ).all()
    assert len(rows) == 7
    parts = {r.part for r in rows}
    assert parts == {"full"}


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_14_am_pm(db) -> None:
    """mode B: assignments=[14件 am+pm 各曜日 別の人] → 14行INSERT。"""
    user = await _make_user(db, "apl-asgn14@example.com")
    trainee = await _make_active_staff(db, "新人C")
    companion_am = await _make_active_staff(db, "先輩C_am")
    companion_pm = await _make_active_staff(db, "先輩C_pm")

    assignments = []
    for wd in range(7):
        assignments.append(
            {"weekday": wd, "part": "am", "companion_staff_id": str(companion_am.id)}
        )
        assignments.append(
            {"weekday": wd, "part": "pm", "companion_staff_id": str(companion_pm.id)}
        )

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={"staff_id": str(trainee.id), "assignments": assignments},
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == trainee.id
            )
        )
    ).all()
    assert len(rows) == 14


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_duplicate_weekday_part_raises(db) -> None:
    """重複 (weekday, part) → 422。"""
    user = await _make_user(db, "apl-dup@example.com")
    trainee = await _make_active_staff(db, "新人D")
    companion = await _make_active_staff(db, "先輩D")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)},
                {"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError, match="duplicate"):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_full_am_conflict_raises(db) -> None:
    """同曜日 full と am 併存 → 422。"""
    user = await _make_user(db, "apl-conflict@example.com")
    trainee = await _make_active_staff(db, "新人E")
    companion = await _make_active_staff(db, "先輩E")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 1, "part": "am", "companion_staff_id": str(companion.id)},
                {"weekday": 1, "part": "full", "companion_staff_id": str(companion.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError, match="full conflicts"):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_self_companion_raises(db) -> None:
    """companion = 自身 → 422。"""
    user = await _make_user(db, "apl-self@example.com")
    trainee = await _make_active_staff(db, "新人F")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(trainee.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError, match="self-companion"):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_admin_companion_raises(db) -> None:
    """companion が admin → 422 (role must be manager/staff)。"""
    user = await _make_user(db, "apl-admincomp@example.com")
    trainee = await _make_active_staff(db, "新人G")
    admin_staff = await _make_active_staff(db, "管理者G", role="admin")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(admin_staff.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError, match="role must be manager/staff"):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_retired_companion_raises(db) -> None:
    """companion が退職者 (status=retired) → 422。"""
    user = await _make_user(db, "apl-retired@example.com")
    trainee = await _make_active_staff(db, "新人H")
    retired = await _make_active_staff(db, "退職者H", status="retired")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(retired.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError, match="companion not active"):
        await applier.apply(db, pr)


@pytest.mark.asyncio
async def test_apply_staff_mentor_assignments_idempotent_put(db) -> None:
    """既存 assignments を含む staff に対し新 assignments PUT → 既存全削除 → INSERT (冪等)。"""
    user = await _make_user(db, "apl-idem-asgn@example.com")
    trainee = await _make_active_staff(db, "新人I", is_trainee=True)
    companion1 = await _make_active_staff(db, "先輩I-1")
    companion2 = await _make_active_staff(db, "先輩I-2")

    # 事前に 2 行セット
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id, weekday=0, part="am", companion_staff_id=companion1.id
        )
    )
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id, weekday=1, part="am", companion_staff_id=companion1.id
        )
    )
    await db.commit()

    # 新しい assignments で全置換
    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "assignments": [
                {"weekday": 2, "part": "full", "companion_staff_id": str(companion2.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == trainee.id
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].weekday == 2
    assert rows[0].part == "full"
    assert rows[0].companion_staff_id == companion2.id


@pytest.mark.asyncio
async def test_apply_staff_mentor_mode_a_and_b_combined(db) -> None:
    """mode A + mode B 両方同時指定 → 両方反映される。"""
    user = await _make_user(db, "apl-combo@example.com")
    trainee = await _make_active_staff(db, "新人J")
    companion = await _make_active_staff(db, "先輩J")
    assert trainee.is_trainee is False

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_mentor",
        payload={
            "staff_id": str(trainee.id),
            "is_trainee": True,
            "assignments": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)},
            ],
        },
        target_staff_id=trainee.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(trainee)

    assert trainee.is_trainee is True
    rows = (
        await db.scalars(
            select(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == trainee.id
            )
        )
    ).all()
    assert len(rows) == 1


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
# 5a) patient_create: requires_multiple_staff / 住所ジオコード補完 (code-reviewer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_create_saves_requires_multiple_staff(db) -> None:
    """payload の requires_multiple_staff=true が患者に保存される。"""
    user = await _make_user(db, "apl-pcreate-rms@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-RMS-001",
            "name": "複数 太郎",
            "status": "active",
            "requires_multiple_staff": True,
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(select(Patient).where(Patient.code == "P-RMS-001"))
    assert row is not None
    assert row.requires_multiple_staff is True


@pytest.mark.asyncio
async def test_apply_patient_create_geocodes_when_lat_lng_missing(db, monkeypatch) -> None:
    """payload に lat/lng 無し + address 有 → geocode が呼ばれ座標が設定される。"""
    from app.services.geocoding.client import GeocodeResult

    calls: list[str] = []

    async def _fake_geocode(address: str, **kwargs) -> GeocodeResult:
        calls.append(address)
        return GeocodeResult(
            lat=35.6,
            lng=139.7,
            formatted_address="東京都…",
            place_id="fake",
            status="OK",
        )

    # applier モジュールが import している参照を差し替える。
    monkeypatch.setattr("app.services.pending_request_applier.geocode_address", _fake_geocode)

    user = await _make_user(db, "apl-pcreate-geo@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-GEO-001",
            "name": "ジオ 太郎",
            "status": "active",
            "address": "東京都千代田区1-1",
            # lat / lng は意図的に未指定 (モバイル経路)
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    assert calls == ["東京都千代田区1-1"]
    row = await db.scalar(select(Patient).where(Patient.code == "P-GEO-001"))
    assert row is not None
    assert float(row.lat) == 35.6
    assert float(row.lng) == 139.7


@pytest.mark.asyncio
async def test_apply_patient_create_geocode_failure_still_creates_patient(db, monkeypatch) -> None:
    """geocode が例外でも患者作成は成功 (best-effort; 座標は None)。"""

    async def _failing_geocode(address: str, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("app.services.pending_request_applier.geocode_address", _failing_geocode)

    user = await _make_user(db, "apl-pcreate-geofail@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-GEOFAIL-001",
            "name": "失敗 太郎",
            "status": "active",
            "address": "東京都千代田区1-1",
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    row = await db.scalar(select(Patient).where(Patient.code == "P-GEOFAIL-001"))
    assert row is not None
    assert row.lat is None
    assert row.lng is None


@pytest.mark.asyncio
async def test_apply_patient_create_does_not_regeocode_when_lat_lng_present(
    db, monkeypatch
) -> None:
    """payload に lat/lng 明示時は再 geocode しない (与えられた座標を採用)。"""
    calls: list[str] = []

    async def _fake_geocode(address: str, **kwargs):
        calls.append(address)
        raise AssertionError("geocode_address must not be called when lat/lng present")

    monkeypatch.setattr("app.services.pending_request_applier.geocode_address", _fake_geocode)

    user = await _make_user(db, "apl-pcreate-nogeo@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-NOGEO-001",
            "name": "座標 太郎",
            "status": "active",
            "address": "東京都千代田区1-1",
            "lat": 34.0,
            "lng": 135.0,
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    assert calls == []
    row = await db.scalar(select(Patient).where(Patient.code == "P-NOGEO-001"))
    assert row is not None
    assert float(row.lat) == 34.0
    assert float(row.lng) == 135.0


# ---------------------------------------------------------------------------
# 5b) patient_create + proposed_visits (StageB-backend: 1 承認で作成 + PFV 確定)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_create_with_proposed_visits_sets_normal_pfv(db) -> None:
    """proposed_visits 付き承認 → 患者作成 + normal PFV (slot0) が設定される。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-pcreate-pv@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-PV-001",
            "name": "確定 太郎",
            "status": "active",
            "proposed_visits": [
                {"weekday": 0, "start_time": "10:00", "duration_min": 60},
                {"weekday": 3, "start_time": "14:30", "duration_min": 45},
            ],
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    patient = await db.scalar(select(Patient).where(Patient.code == "P-PV-001"))
    assert patient is not None

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient.id)
            .order_by(PatientFixedVisit.weekday)
        )
    ).all()
    assert len(rows) == 2
    assert all(r.mode == "normal" for r in rows)
    assert all(r.slot_index == 0 for r in rows)
    assert rows[0].weekday == 0
    assert rows[0].start_time == time(10, 0)
    assert rows[0].duration_min == 60
    assert rows[1].weekday == 3
    assert rows[1].start_time == time(14, 30)
    assert rows[1].duration_min == 45


@pytest.mark.asyncio
async def test_apply_patient_create_without_proposed_visits_creates_patient_only(db) -> None:
    """proposed_visits 無し → 患者作成のみ (現行不変; PFV 0 件)。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-pcreate-nopv@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-NOPV-001",
            "name": "通常 花子",
            "status": "active",
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    patient = await db.scalar(select(Patient).where(Patient.code == "P-NOPV-001"))
    assert patient is not None

    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_apply_patient_create_with_invalid_proposed_visits_rolls_back(db) -> None:
    """proposed_visits の検証失敗 → 例外 + 患者作成もロールバック (同一 TX)。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-pcreate-bad@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-BADPV-001",
            "name": "失敗 一郎",
            "status": "active",
            # weekday=9 は範囲外 → ProposedVisitsError
            "proposed_visits": [
                {"weekday": 9, "start_time": "10:00", "duration_min": 60},
            ],
        },
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422

    # HTTP 層相当の rollback を模倣
    await db.rollback()

    # 患者は作成されていない (同一 TX rollback)
    patient = await db.scalar(select(Patient).where(Patient.code == "P-BADPV-001"))
    assert patient is None
    # PFV も残っていない
    rows = (await db.scalars(select(PatientFixedVisit))).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_apply_patient_create_proposed_visits_does_not_touch_other_patients(db) -> None:
    """proposed_visits 設定が special / 他患者の PFV を壊さない。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-pcreate-iso@example.com")

    # 既存の別患者 + その normal/special PFV (壊れてはいけない)
    other = await _make_patient(db, code="P-OTHER-PV")
    db.add(
        PatientFixedVisit(
            patient_id=other.id,
            mode="normal",
            weekday=1,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=other.id,
            mode="special",
            weekday=2,
            start_time=time(11, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-NEWISO-001",
            "name": "隔離 太郎",
            "status": "active",
            "proposed_visits": [
                {"weekday": 0, "start_time": "10:00", "duration_min": 60},
            ],
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    # 他患者の PFV は normal/special ともに維持
    other_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == other.id))
    ).all()
    assert len(other_rows) == 2
    modes = {r.mode for r in other_rows}
    assert modes == {"normal", "special"}

    # 新患者の normal PFV は 1 件
    new_patient = await db.scalar(select(Patient).where(Patient.code == "P-NEWISO-001"))
    assert new_patient is not None
    new_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == new_patient.id)
        )
    ).all()
    assert len(new_rows) == 1
    assert new_rows[0].mode == "normal"


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
# 14) handler 11 種網羅サニティ (登録漏れ防止)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_request_types_have_handlers() -> None:
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
        "staff_status_update",
        "patient_status_update",
        # Phase G-84 A-4
        "patient_visit_add",
    }
    assert set(_HANDLERS.keys()) == expected


# ---------------------------------------------------------------------------
# W14-BE: staff_status_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_status_update_active(db) -> None:
    """staff_status_update: on_leave → active に戻す。"""
    user = await _make_user(db, "apl-sstatus-active@example.com")
    staff = Staff(name="休職中スタッフ", status="on_leave")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"staff_id": str(staff.id), "status": "active"},
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(staff)
    assert staff.status == "active"


@pytest.mark.asyncio
async def test_apply_staff_status_update_on_leave(db) -> None:
    """staff_status_update: active → on_leave。"""
    user = await _make_user(db, "apl-sstatus-leave@example.com")
    staff = Staff(name="在籍スタッフ", status="active")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"staff_id": str(staff.id), "status": "on_leave"},
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(staff)
    assert staff.status == "on_leave"


@pytest.mark.asyncio
async def test_apply_staff_status_update_retired(db) -> None:
    """staff_status_update: active → retired。"""
    user = await _make_user(db, "apl-sstatus-retired@example.com")
    staff = Staff(name="退職予定スタッフ", status="active")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"staff_id": str(staff.id), "status": "retired"},
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(staff)
    assert staff.status == "retired"


@pytest.mark.asyncio
async def test_apply_staff_status_update_invalid_status_raises(db) -> None:
    """staff_status_update: 不正値 → 422。"""
    user = await _make_user(db, "apl-sstatus-bad@example.com")
    staff = Staff(name="スタッフ不正値", status="active")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"staff_id": str(staff.id), "status": "inactive"},
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_staff_status_update_missing_staff_id_raises(db) -> None:
    """staff_status_update: staff_id 未指定 → 422。"""
    user = await _make_user(db, "apl-sstatus-noid@example.com")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"status": "active"},
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_staff_status_update_deleted_staff_raises(db) -> None:
    """staff_status_update: 削除済みスタッフ (deleted_at IS NOT NULL) → 404。"""
    from datetime import datetime

    user = await _make_user(db, "apl-sstatus-del@example.com")
    staff = Staff(name="削除済みスタッフ", status="active", deleted_at=datetime.utcnow())
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="staff_status_update",
        payload={"staff_id": str(staff.id), "status": "retired"},
        target_staff_id=staff.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 404


# ---------------------------------------------------------------------------
# W14-BE: patient_status_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_status_update_admitted(db) -> None:
    """patient_status_update: active → admitted。"""
    user = await _make_user(db, "apl-pstatus-admit@example.com")
    patient = await _make_patient(db, code="P-PST-ADM")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_status_update",
        payload={"patient_id": str(patient.id), "status": "admitted"},
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(patient)
    assert patient.status == "admitted"


@pytest.mark.asyncio
async def test_apply_patient_status_update_suspended(db) -> None:
    """patient_status_update: active → suspended。"""
    user = await _make_user(db, "apl-pstatus-sus@example.com")
    patient = await _make_patient(db, code="P-PST-SUS")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_status_update",
        payload={"patient_id": str(patient.id), "status": "suspended"},
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()
    await db.refresh(patient)
    assert patient.status == "suspended"


@pytest.mark.asyncio
async def test_apply_patient_status_update_invalid_status_raises(db) -> None:
    """patient_status_update: 不正値 → 422。"""
    user = await _make_user(db, "apl-pstatus-bad@example.com")
    patient = await _make_patient(db, code="P-PST-BAD")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_status_update",
        payload={"patient_id": str(patient.id), "status": "unknown_status"},
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_patient_status_update_missing_patient_id_raises(db) -> None:
    """patient_status_update: patient_id 未指定 → 422。"""
    user = await _make_user(db, "apl-pstatus-noid@example.com")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_status_update",
        payload={"status": "admitted"},
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_patient_status_update_deleted_patient_raises(db) -> None:
    """patient_status_update: 削除済み患者 (deleted_at IS NOT NULL) → 404。"""
    from datetime import datetime

    user = await _make_user(db, "apl-pstatus-del@example.com")
    patient = Patient(
        code="P-PST-DEL", name="削除済み患者", status="active", deleted_at=datetime.utcnow()
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_status_update",
        payload={"patient_id": str(patient.id), "status": "cancelled"},
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 404


# ---------------------------------------------------------------------------
# Phase G-84: patient_visit_add (既存患者の空き枠直接配置)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_visit_add_preserves_existing_and_adds_one(db) -> None:
    """patient_visit_add: 既存 normal PFV を維持しつつ別曜日に 1 枠追加する。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-keep@example.com")
    patient = await _make_patient(db, code="P-VA-KEEP")
    # 既存 normal PFV (Mon 09:00-30分).
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "patient_name": "患者",
            "proposed_visits": [
                {"weekday": 3, "start_time": "14:00", "duration_min": 45},
            ],
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient.id)
            .order_by(PatientFixedVisit.weekday)
        )
    ).all()
    assert len(rows) == 2
    assert rows[0].weekday == 0
    assert rows[0].start_time == time(9, 0)
    assert rows[0].duration_min == 30
    assert rows[1].weekday == 3
    assert rows[1].start_time == time(14, 0)
    assert rows[1].duration_min == 45


@pytest.mark.asyncio
async def test_apply_patient_visit_add_same_weekday_replaces_with_reason(db) -> None:
    """patient_visit_add: 同一 weekday は置換 (赤=time_overlap, override_reason あれば許可)。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-replace@example.com")
    patient = await _make_patient(db, code="P-VA-REPLACE")
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "proposed_visits": [
                {"weekday": 0, "start_time": "11:00", "duration_min": 60},
            ],
            "warnings": [
                {"level": "red", "code": "time_overlap", "message": "時刻重複"},
            ],
            "override_reason": "現場判断で強行",
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    # 同一 weekday=0 は置換されるので 1 件のまま (新しい時刻).
    assert len(rows) == 1
    assert rows[0].weekday == 0
    assert rows[0].start_time == time(11, 0)
    assert rows[0].duration_min == 60


@pytest.mark.asyncio
async def test_apply_patient_visit_add_same_weekday_without_reason_rejected(db) -> None:
    """patient_visit_add: 同一 weekday (=サーバ再判定の赤) で override_reason 空 → 422。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-noreason@example.com")
    patient = await _make_patient(db, code="P-VA-NOREASON")
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            # 同一 weekday=0 → サーバが time_overlap を再判定 → reason 必須.
            "proposed_visits": [
                {"weekday": 0, "start_time": "11:00", "duration_min": 60},
            ],
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    # 同一 weekday → サーバが time_overlap を再判定 → override_reason 必須 → 422.
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_patient_visit_add_patient_not_found(db) -> None:
    """patient_visit_add: 患者不在 → 404。"""
    import uuid as _uuid

    user = await _make_user(db, "apl-visitadd-404@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(_uuid.uuid4()),
            "proposed_visits": [
                {"weekday": 1, "start_time": "10:00", "duration_min": 30},
            ],
        },
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 404


@pytest.mark.asyncio
async def test_apply_patient_visit_add_preserves_slot1_multistaff(db) -> None:
    """patient_visit_add (CRITICAL 是正): multi-staff 患者の slot_index=1 を保全する。

    同曜日に slot_index=0 / 1 の 2 本 normal PFV を持つ multi-staff 患者に、
    **別曜日** へ 1 枠 visit_add する。修正前は全 normal PFV を削除し slot_index=0
    のみ再 INSERT していたため slot_index=1 が恒久消失していた (= このテストは
    修正前なら失敗する)。修正後は slot_index=1 が保全され、新 weekday の
    slot_index=0 が追加されることを検証する。
    """
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-slot1@example.com")
    patient = await _make_patient(db, code="P-VA-SLOT1")
    patient.requires_multiple_staff = True
    # 同曜日 (Mon) に 2 本: slot_index=0 と slot_index=1.
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=1,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "proposed_visits": [
                {"weekday": 3, "start_time": "14:00", "duration_min": 45},
            ],
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient.id)
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    # Mon slot0 + Mon slot1 (保全) + 新 Thu slot0 = 3 行.
    assert len(rows) == 3
    keyed = {(r.weekday, r.slot_index): r for r in rows}
    assert (0, 0) in keyed
    assert (0, 1) in keyed  # ★ slot_index=1 が保全されている (修正前は消失)
    assert (3, 0) in keyed
    assert keyed[(3, 0)].start_time == time(14, 0)
    assert keyed[(3, 0)].duration_min == 45
    # 元の Mon 2 本は時刻不変.
    assert keyed[(0, 0)].start_time == time(9, 0)
    assert keyed[(0, 1)].start_time == time(9, 0)


@pytest.mark.asyncio
async def test_apply_patient_visit_add_same_weekday_keeps_slot1(db) -> None:
    """patient_visit_add: 同一 weekday の slot0 を置換しても slot1 は残る。

    同曜日に slot_index=0 / 1 を持つ患者に、同じ weekday へ visit_add (置換)。
    赤=time_overlap になるので override_reason 必須。slot_index=0 は新時刻に
    置換され、slot_index=1 は触れられずそのまま残ることを検証する。
    """
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-replace-slot1@example.com")
    patient = await _make_patient(db, code="P-VA-REPL-SLOT1")
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=1,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "proposed_visits": [
                {"weekday": 0, "start_time": "11:00", "duration_min": 60},
            ],
            "override_reason": "現場判断で強行",
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient.id)
            .order_by(PatientFixedVisit.slot_index)
        )
    ).all()
    # slot0 は置換 (新時刻), slot1 は保全 → 2 行のまま.
    assert len(rows) == 2
    keyed = {r.slot_index: r for r in rows}
    assert keyed[0].weekday == 0
    assert keyed[0].start_time == time(11, 0)  # 置換された
    assert keyed[0].duration_min == 60
    assert keyed[1].weekday == 0
    assert keyed[1].start_time == time(9, 0)  # slot1 は不変


@pytest.mark.asyncio
async def test_apply_patient_visit_add_blank_override_reason_rejected(db) -> None:
    """patient_visit_add: override_reason が空白のみ ('  ') → 422 (trim 判定)。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-visitadd-blankreason@example.com")
    patient = await _make_patient(db, code="P-VA-BLANK")
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            # 同一 weekday=0 → サーバ time_overlap 再判定 → reason 必須.
            "proposed_visits": [
                {"weekday": 0, "start_time": "11:00", "duration_min": 60},
            ],
            "override_reason": "   ",  # 空白のみ → trim で空扱い → 422.
        },
        target_patient_id=patient.id,
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422

    # 副作用なし: 既存 PFV は変化していない (slot0 のまま 09:00).
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].start_time == time(9, 0)


@pytest.mark.asyncio
async def test_apply_patient_visit_add_empty_proposed_visits_rejected(db) -> None:
    """patient_visit_add: proposed_visits が空 → 422。"""
    user = await _make_user(db, "apl-visitadd-empty@example.com")
    patient = await _make_patient(db, code="P-VA-EMPTY")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={"patient_id": str(patient.id), "proposed_visits": []},
        target_patient_id=patient.id,
    )
    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_patient_visit_add_two_proposed_visits_rejected(db) -> None:
    """patient_visit_add: proposed_visits が 2 件 → 422。"""
    user = await _make_user(db, "apl-visitadd-two@example.com")
    patient = await _make_patient(db, code="P-VA-TWO")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "proposed_visits": [
                {"weekday": 0, "start_time": "09:00", "duration_min": 30},
                {"weekday": 1, "start_time": "10:00", "duration_min": 30},
            ],
        },
        target_patient_id=patient.id,
    )
    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_apply_patient_visit_add_weekday_out_of_range_rejected(db) -> None:
    """patient_visit_add: weekday 範囲外 (7) → 422。"""
    user = await _make_user(db, "apl-visitadd-wd@example.com")
    patient = await _make_patient(db, code="P-VA-WD")

    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "proposed_visits": [
                {"weekday": 7, "start_time": "09:00", "duration_min": 30},
            ],
        },
        target_patient_id=patient.id,
    )
    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422


# ---------------------------------------------------------------------------
# Phase G-84: patient_create + placement/warnings (業務反映に影響しないこと)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_patient_create_placement_meta_does_not_affect_business(db) -> None:
    """placement / yellow warnings は監査メタ扱いで業務反映に影響しない。"""
    from app.models.patient_fixed_visit import PatientFixedVisit

    user = await _make_user(db, "apl-pcreate-placement@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-PLACE-001",
            "name": "配置 太郎",
            "status": "active",
            "proposed_visits": [
                {"weekday": 0, "start_time": "10:00", "duration_min": 60},
            ],
            "placement": {
                "office_id": "00000000-0000-0000-0000-000000000001",
                "office_name": "稲毛",
                "course_code": "稲A",
                "course_label": "稲A",
                "weekday": 0,
                "start_time": "10:00",
                "duration_min": 60,
            },
            # 黄警告のみ → override_reason 不要.
            "warnings": [
                {"level": "yellow", "code": "travel_shortage", "message": "移動不足"},
            ],
        },
    )

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    patient = await db.scalar(select(Patient).where(Patient.code == "P-PLACE-001"))
    assert patient is not None
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    # placement は格納されず、proposed_visits の 1 枠のみが PFV になる.
    assert len(rows) == 1
    assert rows[0].weekday == 0
    assert rows[0].start_time == time(10, 0)


@pytest.mark.asyncio
async def test_apply_patient_create_red_warning_without_reason_rejected(db) -> None:
    """patient_create: 赤警告ありで override_reason 空 → 422。"""
    user = await _make_user(db, "apl-pcreate-redno@example.com")
    pr = await _make_pending(
        db,
        requester=user,
        request_type="patient_create",
        payload={
            "code": "P-REDNO-001",
            "name": "赤 太郎",
            "status": "active",
            "warnings": [
                {"level": "red", "code": "capacity_full", "message": "満員"},
            ],
        },
    )

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422
    await db.rollback()

    patient = await db.scalar(select(Patient).where(Patient.code == "P-REDNO-001"))
    assert patient is None
