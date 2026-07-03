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

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_PROPOSED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    L3_FIX_PRIMARY_STAFF_FEATURE_KEY,
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
    # Phase G-91: クリーンなコースのみ → review_items は空.
    assert body["review_items"] == []


@pytest.mark.asyncio
async def test_rotation_warning_when_forced_repeat(client, db) -> None:
    """不可避連続 (代替候補なし) → review に出さず自動確定 + auto_committed_notices.

    Wave N-1 (不可避連続の自動確定):
    代替候補が 0 名の連続は review_items に出さず、 クリーンコースと同じ経路で
    自動確定し auto_committed_notices として返す.

    前週 (lookback 内) に staff が当該患者を担当した履歴 (staff_assigned コース +
    VisitStaffAssignment) を seed し、 当該週は同 staff しか稼働させない.
    → 代替候補 0 名 = 不可避連続 → 自動確定 (courses_assigned==1) + notice 1 件.

    注意: 前週コースは別コード (B) にする. 同コード (A) だと course_code 単位の
    rotation 履歴ハード除外 (ROTATION_EXCLUSION_WEEKS=1) が効いて staff が候補から
    消え、 「連続」 ではなく「未割当」 になってしまうため (= patient 中心ローテの
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
    # Wave N-1: 不可避連続 (代替候補 0 名) は自動確定 → courses_assigned==1.
    assert body["courses_assigned"] == 1, (
        f"不可避連続は自動確定されるはず (courses_assigned=1): {body['courses_assigned']}"
    )
    # review_items には出ない.
    assert body["review_items"] == [], f"不可避連続は review_items に出ない: {body['review_items']}"
    # auto_committed_notices に 1 件 (reason_kind='single_staff').
    notices = body["auto_committed_notices"]
    assert len(notices) == 1, f"notice 1 件想定だが {len(notices)} 件: {notices}"
    notice = notices[0]
    assert notice["course_id"] == str(course.id)
    assert notice["course_code"] == "A"
    assert notice["weekday"] == 0
    assert notice["reason_kind"] == "single_staff"
    assert notice["staff_name"] == "連続スタッフ"
    assert "連続患者" in notice["cause_patient_names"]
    assert "reason_text" in notice and notice["reason_text"]
    # 連続は未割当ではない (= 候補が存在する) ので unassigned_warnings には出ない.
    assert body["unassigned_warnings"] == []


@pytest.mark.asyncio
async def test_applied_consecutive_not_resurfaced_on_rerun(client, db) -> None:
    """Phase G-91 (修正A): 適用済み連続コースは再実行で review に再浮上しない.

    シナリオ (本番事故): 連続コース X を一度 apply して
    course_status='staff_assigned' + assigned_staff_id=S にした後、 同一週で
    「自動スタッフ割付」 を再実行する. 旧挙動では _load_patient_recent_staff が
    当該週を除外して pre-week 履歴で index0 を再検出し、 _build_review_items に
    staff_assigned ガードが無かったため毎回 review に再浮上 + courses_assigned から
    も落ちて件数過少になっていた.

    修正A: solve 候補 (= S) が DB 上の確定 staff (= S, NOT NULL) と一致する連続
    コースは「承認済み」 とみなし、 (1) review_items に出さない (2) commit に残す
    (= courses_assigned に数える). これを検証する.

    setup は ``test_rotation_warning_when_forced_repeat`` と同型だが、 当該週の
    コースを **最初から staff_assigned + S** にしておく (= apply 相当の状態).
    """
    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-applied-rerun@example.com")

    office = Office(name="G91 拠点 applied", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="G91-AP1",
        name="適用済みスタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    patient = Patient(
        code="G91-PA", name="適用済み患者", status="active", primary_office_id=office.id
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
        )
    )
    await db.flush()

    # ----- 前週 (= 直近担当履歴) の staff_assigned コース + VSA (別コード B) -----
    prev_monday = date(2026, 6, 8)
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

    # ----- 当該週のコース: 既に apply 済 (staff_assigned + S) -----
    course = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    cur_visit = Visit(
        patient_id=patient.id,
        visit_date=G89_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=staff.id,
    )
    db.add(cur_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=cur_visit.id, staff_id=staff.id))
    await db.commit()
    # expire 前に ID を確定取得しておく (= 後段の DB 検証で expire 後の lazy IO を回避).
    course_id = course.id
    staff_id = staff.id

    # ----- 同一週で再実行 -----
    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": G89_ISO_YEAR, "iso_week": G89_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # 修正A: 適用済み連続コースは review_items に出ない.
    items = body["review_items"]
    course_ids = {it["course_id"] for it in items}
    assert str(course_id) not in course_ids, f"適用済みコースが review に再浮上した: {items}"
    assert items == [], f"適用済みのみなら review は空のはず: {items}"

    # 修正A: 適用済みコースは commit に残り courses_assigned に数えられる.
    assert body["courses_assigned"] == 1, (
        f"適用済みコースは確定カウントされるはず: {body['courses_assigned']}"
    )

    # DB 上も staff_assigned + S のまま (= 再実行で剥がれない).
    from sqlalchemy import select as _select

    db.expire_all()
    row = (
        await db.execute(
            _select(Course.course_status, Course.assigned_staff_id).where(Course.id == course_id)
        )
    ).one()
    assert row[0] == COURSE_STATUS_STAFF_ASSIGNED
    assert row[1] == staff_id


@pytest.mark.asyncio
async def test_index1_two_back_is_committed_not_reviewed(client, db) -> None:
    """Phase G-91: 2 個前 (recent_index==1) は自動 commit され review 対象外.

    患者の直近担当者リストを ``[Sother(1つ前), S(2個前)]`` にし、 当該週は S しか
    稼働させない. → S は recent_index==1 (2個前) で再割り当て = **連続ではない**
    ため自動 commit される (courses_assigned==1)。 review_items には入らない.
    """
    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-index1@example.com")

    office = Office(name="G91 拠点 idx1", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    # 当該週に稼働するのは s_target のみ (= 2個前担当). s_other は当該週休み.
    s_target = Staff(
        code="G91-IDX1-T",
        name="2個前スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s_other = Staff(
        code="G91-IDX1-O",
        name="1つ前スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([s_target, s_other])
    await db.flush()
    db.add(StaffShift(staff_id=s_target.id, weekday=0, is_on=True))
    # s_other は稼働させない (= 当該週シフト無し).
    await db.flush()

    patient = Patient(
        code="G91-IDX1-P", name="idx1 患者", status="active", primary_office_id=office.id
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
        )
    )
    await db.flush()

    # 履歴: week-2 (古い) = s_target, week-1 (新しい) = s_other.
    # _load_patient_recent_staff は新しい順 → [s_other, s_target] = s_target が index1.
    # course_code は当該週 (A) と別コード (B/C) にして course_code 単位ハード除外を回避.
    for iso_week, code, sid, mon in (
        (23, "C", s_target.id, date(2026, 6, 1)),  # 2 週前
        (24, "B", s_other.id, date(2026, 6, 8)),  # 1 週前
    ):
        prev_course = Course(
            iso_year=2026,
            iso_week=iso_week,
            weekday=0,
            code=code,
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            assigned_staff_id=sid,
            office_id=office.id,
        )
        db.add(prev_course)
        await db.flush()
        prev_visit = Visit(
            patient_id=patient.id,
            visit_date=mon,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=prev_course.id,
            primary_staff_id=sid,
        )
        db.add(prev_visit)
        await db.flush()
        db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=sid))
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
    # index1 (2個前) は連続ではない → 自動 commit.
    assert body["courses_assigned"] == 1
    assert body["review_items"] == [], f"index1 は review 対象外のはず: {body['review_items']}"
    # DB 上は s_target が割り当たっている.
    from sqlalchemy import select as _select

    course_id = course.id
    target_id = s_target.id
    db.expire_all()
    assigned = (
        await db.execute(_select(Course.assigned_staff_id).where(Course.id == course_id))
    ).scalar_one()
    assert assigned == target_id


@pytest.mark.asyncio
async def test_gender_block_produces_review_item_with_candidate(client, db) -> None:
    """Phase G-91: 性別ブロックコース → 性別無視時の候補付き review_item (gender).

    女性のみ患者のコースに男性スタッフしか居ない → 性別ハード制約で未割当.
    性別を無視すれば男性スタッフが候補になるため、 reason='gender' の review_item が
    candidate=男性スタッフで返る. 原因患者 (sex_restriction='female_only') は is_cause.
    """
    admin = await _make_user(db, "g91-gender@example.com")

    office = Office(name="G91 拠点 gender", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    # 男性スタッフのみ (= 女性のみ患者を満たせない).
    male_staff = Staff(
        code="G91-GEN-M",
        name="男性スタッフ",
        role="staff",
        status="active",
        sex="male",
        primary_office_id=office.id,
    )
    db.add(male_staff)
    await db.flush()
    db.add(StaffShift(staff_id=male_staff.id, weekday=0, is_on=True))
    await db.flush()

    # 女性のみ患者.
    patient = Patient(
        code="G91-GEN-P",
        name="女性のみ患者",
        status="active",
        sex_restriction="female_only",
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
    # 性別で未割当 → commit 0.
    assert body["courses_assigned"] == 0
    items = body["review_items"]
    assert len(items) == 1, f"gender review 1 件想定だが {len(items)} 件: {items}"
    item = items[0]
    assert item["course_id"] == str(course.id)
    assert item["reason"] == "gender"
    # candidate = 性別を無視した候補 (= 男性スタッフ).
    assert item["candidate_staff_id"] == str(male_staff.id)
    assert item["candidate_staff_sex"] == "male"
    # 原因患者 (sex_restriction あり) は is_cause=True.
    assert len(item["visits"]) == 1
    v = item["visits"][0]
    assert v["patient_id"] == str(patient.id)
    assert v["sex_restriction"] == "female_only"
    assert v["is_cause"] is True
    # DB 未割当のまま (= 自動では割り付けない).
    from sqlalchemy import select as _select

    course_id = course.id
    db.expire_all()
    assigned = (
        await db.execute(_select(Course.assigned_staff_id).where(Course.id == course_id))
    ).scalar_one()
    assert assigned is None


# ---------------------------------------------------------------------------
# Phase G-91 (修正1): apply-staff-review endpoint = _persist 経路で反映
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_review_persists_via_persist_path(client, db) -> None:
    """Phase G-91 (修正1): apply-staff-review は _persist 経由で完全反映する.

    レビューで承認した course を apply すると、 PATCH /courses (assigned_staff_id
    のみ) では起きなかった以下が全て反映されることを確認する:
      (a) course.assigned_staff_id + course_status='staff_assigned'
      (b) VisitStaffAssignment INSERT (= スタッフの visit 可視性)
      (c) visits.primary_staff_id 同期
    """
    from sqlalchemy import select as _select

    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-apply@example.com")

    office = Office(name="G91 拠点 apply", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="G91-APPLY",
        name="apply スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    patient = Patient(
        code="G91-AP", name="apply 患者", status="active", primary_office_id=office.id
    )
    db.add(patient)
    await db.flush()

    course = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    visit = Visit(
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
    db.add(visit)
    await db.commit()
    # ID 値を expire 前に確保 (= expire_all 後の ORM 属性 lazy-load 回避).
    course_id = course.id
    staff_id = staff.id
    visit_id = visit.id

    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": G89_ISO_YEAR,
            "iso_week": G89_ISO_WEEK,
            "items": [{"course_id": str(course_id), "staff_id": str(staff_id)}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_count"] == 1
    assert any(r["course_id"] == str(course_id) and r["ok"] for r in body["results"])

    db.expire_all()
    # (a) course 更新.
    c = (await db.execute(_select(Course).where(Course.id == course_id))).scalar_one()
    assert c.assigned_staff_id == staff_id
    assert c.course_status == COURSE_STATUS_STAFF_ASSIGNED
    # (b) VisitStaffAssignment INSERT.
    vsa = (
        (
            await db.execute(
                _select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(vsa) == 1
    assert vsa[0].staff_id == staff_id
    # (c) visits.primary_staff_id 同期.
    v = (await db.execute(_select(Visit).where(Visit.id == visit_id))).scalar_one()
    assert v.primary_staff_id == staff_id


@pytest.mark.asyncio
async def test_apply_staff_review_unknown_course_marked_failed(client, db) -> None:
    """Phase G-91 (修正1/修正5): 当該週に存在しない course は ok=False で返す."""
    admin = await _make_user(db, "g91-apply-bad@example.com")

    office = Office(name="G91 拠点 apply-bad", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()
    staff = Staff(
        code="G91-APPLY-BAD",
        name="apply スタッフ bad",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": G89_ISO_YEAR,
            "iso_week": G89_ISO_WEEK,
            "items": [{"course_id": str(uuid4()), "staff_id": str(staff.id)}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_count"] == 0
    assert len(body["results"]) == 1
    assert body["results"][0]["ok"] is False


@pytest.mark.asyncio
async def test_apply_staff_review_resolves_two_person_secondary(client, db) -> None:
    """Phase G-91 (修正4): 2 名体制ペアを同時 apply すると secondary が解決される.

    course X (staff A) と course Y (staff B) が同一 visit_group (2 名体制) のとき、
    両 course を 1 回の apply-staff-review に渡すと、 X 側 visit の
    secondary_staff_id=B / Y 側 visit の secondary_staff_id=A が _persist 経由で
    正しく解決される (= half-assigned にならない).
    """
    from uuid import uuid4 as _uuid4

    from sqlalchemy import select as _select

    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-pair@example.com")

    office = Office(name="G91 拠点 pair", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff_a = Staff(
        code="G91-PAIR-A",
        name="ペア A",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    staff_b = Staff(
        code="G91-PAIR-B",
        name="ペア B",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([staff_a, staff_b])
    await db.flush()

    patient = Patient(
        code="G91-PAIR-P", name="2 名体制患者", status="active", primary_office_id=office.id
    )
    db.add(patient)
    await db.flush()

    course_x = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    course_y = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add_all([course_x, course_y])
    await db.flush()

    group_id = _uuid4()
    # 2 名体制: 同 visit_group_id を持つ 2 visit (= 別コースに所属).
    visit_x = Visit(
        patient_id=patient.id,
        visit_date=G89_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_x.id,
    )
    visit_y = Visit(
        patient_id=patient.id,
        visit_date=G89_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_y.id,
    )
    db.add_all([visit_x, visit_y])
    await db.commit()

    cx_id, cy_id = course_x.id, course_y.id
    sa_id, sb_id = staff_a.id, staff_b.id
    vx_id, vy_id = visit_x.id, visit_y.id

    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": G89_ISO_YEAR,
            "iso_week": G89_ISO_WEEK,
            "items": [
                {"course_id": str(cx_id), "staff_id": str(sa_id)},
                {"course_id": str(cy_id), "staff_id": str(sb_id)},
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied_count"] == 2

    db.expire_all()
    # X 側 visit: primary=A, secondary=B.
    vx = (await db.execute(_select(Visit).where(Visit.id == vx_id))).scalar_one()
    assert vx.primary_staff_id == sa_id
    assert vx.secondary_staff_id == sb_id
    # Y 側 visit: primary=B, secondary=A.
    vy = (await db.execute(_select(Visit).where(Visit.id == vy_id))).scalar_one()
    assert vy.primary_staff_id == sb_id
    assert vy.secondary_staff_id == sa_id
    # VSA は X visit に A+B の 2 行.
    vsa_x = (
        (
            await db.execute(
                _select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == vx_id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.staff_id for row in vsa_x} == {sa_id, sb_id}


@pytest.mark.asyncio
async def test_apply_review_secondary_resolves_when_partner_already_committed(client, db) -> None:
    """Phase G-91 (修正4 + review fix): partner Y が確定済みでも X の secondary が解決.

    Y を先に確定 (staff_assigned + assigned_staff_id=B) しておき、 X (= 連続で
    review に出た側) だけを apply する. apply-staff-review は確定済み Y を本割付に
    含めない (= 不要な VSA 再生成回避) が、 _persist が Y の現 DB 値を読んで
    X の secondary=B を解決するため、 X 側 visit の secondary は正しく B になる.
    """
    from uuid import uuid4 as _uuid4

    from sqlalchemy import select as _select

    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-partner-committed@example.com")

    office = Office(name="G91 拠点 partner-committed", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff_a = Staff(
        code="G91-PC-A", name="PC A", role="staff", status="active", primary_office_id=office.id
    )
    staff_b = Staff(
        code="G91-PC-B", name="PC B", role="staff", status="active", primary_office_id=office.id
    )
    db.add_all([staff_a, staff_b])
    await db.flush()

    patient = Patient(code="G91-PC-P", name="PC 患者", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()

    course_x = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    # Y は先に確定済み (staff_assigned + B).
    course_y = Course(
        iso_year=G89_ISO_YEAR,
        iso_week=G89_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff_b.id,
        office_id=office.id,
    )
    db.add_all([course_x, course_y])
    await db.flush()

    group_id = _uuid4()
    visit_x = Visit(
        patient_id=patient.id,
        visit_date=G89_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_x.id,
    )
    visit_y = Visit(
        patient_id=patient.id,
        visit_date=G89_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_y.id,
        primary_staff_id=staff_b.id,
    )
    db.add_all([visit_x, visit_y])
    await db.flush()
    # Y は確定済みなので VSA も既にある想定.
    db.add(VisitStaffAssignment(visit_id=visit_y.id, staff_id=staff_b.id))
    await db.commit()

    cx_id, sa_id, sb_id = course_x.id, staff_a.id, staff_b.id
    vx_id = visit_x.id

    # X だけを apply (Y は items に含めない).
    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": G89_ISO_YEAR,
            "iso_week": G89_ISO_WEEK,
            "items": [{"course_id": str(cx_id), "staff_id": str(sa_id)}],
        },
    )
    assert res.status_code == 200, res.text
    # 確定済み Y は本割付に含めない → applied_count は X の 1 件のみ.
    assert res.json()["applied_count"] == 1

    db.expire_all()
    # X 側 visit: primary=A, secondary=B (= _persist が Y の現 DB 値から解決).
    vx = (await db.execute(_select(Visit).where(Visit.id == vx_id))).scalar_one()
    assert vx.primary_staff_id == sa_id
    assert vx.secondary_staff_id == sb_id
    vsa_x = (
        (
            await db.execute(
                _select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == vx_id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.staff_id for row in vsa_x} == {sa_id, sb_id}


@pytest.mark.asyncio
async def test_fixed_course_consecutive_surfaces_to_review(client, db) -> None:
    """Wave N-1: 固定コース (l3_fix_primary_staff 拠点 / 1 名) の不可避連続は自動確定 + notice.

    設計書 R-2 (オーナー決定 B の上書き): 代替候補が 0 名 (= 不可避) な連続は
    review に出さず自動確定し、 auto_committed_notices として理由を通知する.
    (旧 G-91 修正3 では「1 名拠点でも連続は必ず review」 だったが N-1 で変更.)
    """
    from sqlalchemy import select as _select

    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "g91-fixed@example.com")

    # Wave N-1: feature flag で primary staff 固定割当を有効化した拠点.
    office = Office(name="N1 固定 G91 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()
    db.add(
        OfficeFeatureFlag(
            office_id=office.id,
            feature_key=L3_FIX_PRIMARY_STAFF_FEATURE_KEY,
            enabled_at=datetime.now(tz=UTC),
        )
    )
    await db.flush()

    # 拠点の唯一の staff (= 1 名).
    honmyo = Staff(
        code="G91-TSUGA",
        name="本名さん",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(honmyo)
    await db.flush()
    db.add(StaffShift(staff_id=honmyo.id, weekday=0, is_on=True))
    await db.flush()

    patient = Patient(code="G91-TSP", name="都賀患者", status="active", primary_office_id=office.id)
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

    # 前週 (= 直近担当履歴) は別コード (B) で本名が当該患者を担当 (連続の素地).
    prev_course = Course(
        iso_year=2026,
        iso_week=24,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=honmyo.id,
        office_id=office.id,
    )
    db.add(prev_course)
    await db.flush()
    prev_visit = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 6, 8),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=prev_course.id,
        primary_staff_id=honmyo.id,
    )
    db.add(prev_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=honmyo.id))
    await db.flush()

    # 当該週の 都賀 A コース (固定割当対象).
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
    course_id = course.id
    honmyo_id = honmyo.id

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": G89_ISO_YEAR, "iso_week": G89_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Wave N-1: 代替候補 0 名 → 不可避連続は自動確定 (courses_assigned==1).
    assert body["courses_assigned"] == 1, (
        f"不可避固定連続は自動確定されるはず (courses_assigned=1): {body['courses_assigned']}"
    )
    # review_items には出ない.
    assert body["review_items"] == [], f"不可避連続は review_items に出ない: {body['review_items']}"
    # auto_committed_notices に 1 件.
    notices = body["auto_committed_notices"]
    assert len(notices) == 1, f"notice 1 件想定だが {len(notices)} 件: {notices}"
    notice = notices[0]
    assert notice["course_id"] == str(course_id)
    assert notice["reason_kind"] in ("single_staff", "all_recent")
    assert notice["staff_name"] == "本名さん"
    # DB に確定済み.
    db.expire_all()
    assigned = (
        await db.execute(_select(Course.assigned_staff_id).where(Course.id == course_id))
    ).scalar_one()
    assert assigned == honmyo_id, f"自動確定済みなので DB に staff_id があるはず: {assigned}"
