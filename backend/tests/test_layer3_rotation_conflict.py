"""Phase G-89: ローテ衝突 / 未割当コース検出テスト.

直前 commit 748bf00 (Layer3 患者中心ローテ刷新) の続き。 人手不足で
「同じ担当者を避ける」 を維持できないときの **検出** をテストする
(= 埋めて事後警告。 fill/null 挙動自体は変えない).

検証観点:
  solve() レベル (純粋関数):
    1. スタッフを絞って強制衝突 → rotation_conflicts に正しい
       recent_index (0=連続 / 1=2個前) が出る
    2. visits>0 のコースに対し候補ゼロ → solve() の assignments から欠落
       (unassigned 検出は assign() 経由でテスト; solve は assignments のみ)
    3. 衝突なし (人員十分) → rotation_conflicts 空

  endpoint レベル (assign-staff-only):
    4. 未割当発生時に unassigned_warnings が出る
    5. 衝突なしなら rotation_warnings / unassigned_warnings 両方空
"""

from __future__ import annotations

from datetime import date, time
from uuid import UUID, uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_PROPOSED, COURSE_STATUS_STAFF_ASSIGNED
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
)

G89_ISO_YEAR = 2026
G89_ISO_WEEK = 25
G89_WEEK_MONDAY = date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Helpers (solve() pure-function tests)
# ---------------------------------------------------------------------------


def _make_staff(
    name: str,
    role: str = "staff",
    work_days: frozenset[int] = frozenset(range(6)),
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=None,
        role=role,
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days,
    )


def _make_course(weekday: int, code: str, patient_id: UUID | None = None) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        patient_ids=[patient_id] if patient_id is not None else [],
    )


# ---------------------------------------------------------------------------
# solve() pure-function tests
# ---------------------------------------------------------------------------


def test_rotation_conflict_detected_when_only_staff_is_recent() -> None:
    """候補スタッフが直近担当者 1 人しか居ない → 衝突 (recent_index=0, 連続)."""
    pid = uuid4()
    s1 = _make_staff("S1")
    # 月曜の 1 コース (患者 pid). pid の直近担当者 = s1 のみ.
    c_mon = _make_course(weekday=0, code="A", patient_id=pid)

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_mon],
        [s1],
        patient_recent_staff={pid: [s1.staff_id]},
    )

    # s1 しか居ないので埋める (= 割当される)
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id
    # 衝突として記録される (recent_index=0 = 1 つ前と同じ = 連続)
    assert len(result.rotation_conflicts) == 1
    conflict = result.rotation_conflicts[0]
    assert conflict.patient_id == pid
    assert conflict.staff_id == s1.staff_id
    assert conflict.course_id == c_mon.course_id
    assert conflict.weekday == 0
    assert conflict.recent_index == 0


def test_rotation_conflict_recent_index_1_when_two_back() -> None:
    """直近担当者リスト index1 (= 2 個前) の staff を再割り当て → recent_index=1.

    s1 (= 1 つ前) と s2 (= 2 個前) が居て、 s1 が別コースで取られると
    pid のコースには s2 しか残らず、 s2 (index1) を再割り当てする.
    """
    pid_a = uuid4()
    pid_b = uuid4()
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")

    # 月曜: コース A (pid_a) と コース B (pid_b).
    # pid_a の直近 = [s1, s2] (s1 が 1 つ前).  pid_b の直近 = [s1] (s1 が最適回避先).
    # s1 を pid_b 側へ誘導し、 pid_a には s2 (index1) を当てさせる構図にする.
    c_a = _make_course(weekday=0, code="A", patient_id=pid_a)
    c_b = _make_course(weekday=0, code="B", patient_id=pid_b)

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_a, c_b],
        [s1, s2],
        patient_recent_staff={
            pid_a: [s1.staff_id, s2.staff_id],  # s1=1つ前, s2=2個前
            pid_b: [s2.staff_id, s1.staff_id],  # s2=1つ前, s1=2個前
        },
    )

    # 2 コース 2 staff → 全割当. ペナルティ最小化は:
    #   pid_a に s2 (index1=5e5) + pid_b に s1 (index1=5e5) = 1e6
    #   vs pid_a に s1 (index0=1e6) + pid_b に s2 (index0=1e6) = 2e6
    # → 前者 (各 index1) が選ばれる.
    assert len(result.assignments) == 2
    by_course = {a.course_id: a.staff_id for a in result.assignments}
    assert by_course[c_a.course_id] == s2.staff_id
    assert by_course[c_b.course_id] == s1.staff_id
    # 両コースとも index1 衝突
    assert len(result.rotation_conflicts) == 2
    for conflict in result.rotation_conflicts:
        assert conflict.recent_index == 1


def test_no_rotation_conflict_when_enough_staff() -> None:
    """候補に直近担当者でない staff が居る → 衝突なし (rotation_conflicts 空)."""
    pid = uuid4()
    s_recent = _make_staff("S-recent")
    s_fresh = _make_staff("S-fresh")
    c_mon = _make_course(weekday=0, code="A", patient_id=pid)

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_mon],
        [s_recent, s_fresh],
        patient_recent_staff={pid: [s_recent.staff_id]},
    )

    assert len(result.assignments) == 1
    # fresh staff が選ばれ、 衝突なし
    assert result.assignments[0].staff_id == s_fresh.staff_id
    assert result.rotation_conflicts == []


def test_rotation_conflicts_empty_without_history() -> None:
    """patient_recent_staff 無し (= 履歴なし) → 衝突は出ない (regression)."""
    pid = uuid4()
    s1 = _make_staff("S1")
    c_mon = _make_course(weekday=0, code="A", patient_id=pid)

    assigner = Layer3Assigner()
    result = assigner.solve([c_mon], [s1])

    assert len(result.assignments) == 1
    assert result.rotation_conflicts == []


# ---------------------------------------------------------------------------
# Endpoint-level tests (assign-staff-only)
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unassigned_warning_when_no_staff_available(client, db) -> None:
    """visits>0 のコースに割当可能な staff が居ない → unassigned_warnings 1 件."""
    admin = await _make_user(db, "g89-unassigned@example.com")

    office = Office(name="G89 拠点 unassigned", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    # staff は居るが当該曜日 (月) に勤務しない → コースを埋められない
    staff = Staff(
        code="G89-NS",
        name="非稼働スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=1, is_on=True))  # 火曜のみ稼働
    await db.flush()

    patient = Patient(code="G89-P1", name="G89 患者", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.flush()

    course = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_PROPOSED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=G89_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": G89_ISO_YEAR, "iso_week": G89_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "unassigned_warnings" in body
    uw = body["unassigned_warnings"]
    assert len(uw) == 1, f"未割当 1 件想定だが {len(uw)} 件: {uw}"
    w = uw[0]
    assert w["course_id"] == str(course.id)
    assert w["course_code"] == "A"
    assert w["weekday"] == 0
    assert w["visit_start_time"] == "09:00:00"
    assert str(patient.id) in w["patient_ids"]
    assert "G89 患者" in w["patient_names"]
    # 衝突は無い
    assert body["rotation_warnings"] == []


@pytest.mark.asyncio
async def test_no_warnings_when_assignment_clean(client, db) -> None:
    """十分な人員で割当成功 → rotation_warnings / unassigned_warnings 両方空."""
    admin = await _make_user(db, "g89-clean@example.com")

    office = Office(name="G89 拠点 clean", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="G89-OK",
        name="稼働スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    patient = Patient(code="G89-P2", name="G89 患者2", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.flush()

    course = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_PROPOSED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=G89_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": G89_ISO_YEAR, "iso_week": G89_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["courses_assigned"] == 1
    assert body["rotation_warnings"] == []
    assert body["unassigned_warnings"] == []


@pytest.mark.asyncio
async def test_rotation_warning_when_forced_repeat(client, db) -> None:
    """直近担当者しか稼働しておらず再割り当て → rotation_warnings (連続) 1 件.

    前週 (lookback 内) に staff が当該患者を担当した履歴 (staff_assigned コース +
    VisitStaffAssignment) を seed し、 当該週は同 staff しか稼働させない.
    → 直近担当者を再割り当てせざるを得ず、 rotation_warnings に is_consecutive=True.

    注意: 前週コースは別コード (B) にする. 同コード (A) だと course_code 単位の
    rotation 履歴ハード除外 (ROTATION_EXCLUSION_WEEKS=1) が効いて staff が候補から
    消え、 「衝突」 ではなく「未割当」 になってしまうため (= patient 中心ローテの
    衝突を観測するには course_code レベルの除外と切り離す必要がある).
    """
    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g89-rot@example.com")

    office = Office(name="G89 拠点 rot", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="G89-R1",
        name="連続スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    patient = Patient(code="G89-PR", name="連続患者", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.flush()

    # ----- 前週 (= 直近担当履歴) の staff_assigned コース + VSA -----
    prev_monday = date(2026, 6, 8)
    # 前週は別コード (B) にして course_code 単位の rotation 履歴ハード除外
    # (ROTATION_EXCLUSION_WEEKS=1) を回避し、 patient 中心ローテ (=直近担当者)
    # の衝突のみを発生させる. 当該週は code='A'.
    prev_course = Course(
        iso_year=2026,
        iso_week=24,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(prev_course)
    await db.flush()
    prev_visit = Visit(
        patient_id=patient.id,
        visit_date=prev_monday,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=prev_course.id,
        primary_staff_id=staff.id,
    )
    db.add(prev_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=staff.id))
    await db.flush()

    # ----- 当該週のコース + visit (同 staff しか稼働しない) -----
    course = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_PROPOSED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=G89_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": G89_ISO_YEAR, "iso_week": G89_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 割当は埋まる (= 訪問確保)
    assert body["courses_assigned"] == 1
    rw = body["rotation_warnings"]
    assert len(rw) == 1, f"ローテ衝突 1 件想定だが {len(rw)} 件: {rw}"
    w = rw[0]
    assert w["patient_id"] == str(patient.id)
    assert w["patient_name"] == "連続患者"
    assert w["staff_id"] == str(staff.id)
    assert w["staff_name"] == "連続スタッフ"
    assert w["course_code"] == "A"
    assert w["weekday"] == 0
    assert w["recent_index"] == 0
    assert w["is_consecutive"] is True
    assert w["visit_start_time"] == "09:00:00"
    # 埋まったので未割当なし
    assert body["unassigned_warnings"] == []
