"""NG スタッフ (患者×スタッフ割当禁止) の Layer3 ハード制約テスト.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §5 (エンジン変更).
手本 = ``test_layer3_phase1_fixed_gender.py`` / ``test_layer3_w11_gender_unresolved.py``
/ ``test_layer3_visit_assignments.py`` の fixture 流儀.

検証観点:
  1. NG 該当スタッフが ``_cost_single_cell`` で INF 除外され別スタッフに割当される
  2. 固定割当 (Phase1 ルート) の NG 違反は確定されず free へ回る / 適合者ゼロなら未割当
  3. E2E: 全候補 NG → review_items(reason='ng_staff') + DB 未割当のまま + NG 緩和候補
  4. E2E: 性別 + NG が同時ブロック → reason='gender' + also_violates=['ng_staff']
  5. 残留警告 ``unresolved_ng_warnings`` (現担当が NG・候補ゼロ)
  6. 2 名体制 secondary の検証 (PO 決定4): 割当はされるが警告に載る (NG / 性別)
  7. NG なし患者だけの週では全リストが空 (リグレッション)
"""

from __future__ import annotations

import uuid
from datetime import date, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED
from app.models.patient_ng_staff import PatientNgStaff
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffAssignment,
    StaffInfo,
    _staff_satisfies_ng,
)

# 月曜 = 2026-05-25 (week 22). 既存 Layer3 テスト群と同じ週を使う.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Helpers (純粋 solve 用)
# ---------------------------------------------------------------------------


def _make_course(
    *,
    code: str = "A",
    weekday: int = 0,
    restrictions: frozenset[str] = frozenset(),
    patient_ids: list[UUID] | None = None,
    ng_staff_ids: frozenset[UUID] = frozenset(),
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=restrictions,
        patient_ids=patient_ids if patient_ids is not None else [uuid4()],
        visits=[],
        ng_staff_ids=ng_staff_ids,
    )


def _make_staff(
    *,
    role: str = "staff",
    sex: str | None = "female",
    work_days: frozenset[int] = frozenset(range(7)),
    name: str = "ng-staff",
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=sex,
        role=role,
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days,
    )


# ---------------------------------------------------------------------------
# Helpers (DB fixture 用)
# ---------------------------------------------------------------------------


async def _make_office(db, *, name: str) -> Office:
    office = Office(name=name, lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()
    return office


async def _make_db_staff(
    db,
    *,
    office_id: UUID,
    code: str,
    name: str,
    sex: str = "female",
    role: str = "staff",
) -> Staff:
    s = Staff(
        code=code,
        name=name,
        sex=sex,
        role=role,
        status="active",
        primary_office_id=office_id,
    )
    db.add(s)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return s


async def _make_db_patient(
    db,
    *,
    code: str,
    name: str | None = None,
    sex_restriction: str | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"NG {code}",
        status="active",
        sex_restriction=sex_restriction,
        lat=35.6383,
        lng=140.1041,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_db_course(
    db,
    *,
    office_id: UUID,
    code: str = "A",
    weekday: int = 0,
    course_status: str = COURSE_STATUS_COURSE_FIXED,
    assigned_staff_id: UUID | None = None,
) -> Course:
    c = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=course_status,
        assigned_staff_id=assigned_staff_id,
        office_id=office_id,
    )
    db.add(c)
    await db.flush()
    return c


async def _make_db_visit(
    db,
    *,
    patient_id: UUID,
    course_id: UUID,
    required_staff_count: int = 1,
    visit_group_id: UUID | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient_id,
        course_id=course_id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=required_staff_count,
        visit_group_id=visit_group_id,
    )
    db.add(v)
    await db.flush()
    return v


async def _add_ng(db, *, patient_id: UUID, staff_id: UUID, note: str | None = None) -> None:
    db.add(PatientNgStaff(patient_id=patient_id, staff_id=staff_id, note=note))
    await db.flush()


# ---------------------------------------------------------------------------
# 1) ヘルパー + INF 除外: NG 該当は別スタッフへ回る
# ---------------------------------------------------------------------------


def test_staff_satisfies_ng_semantics() -> None:
    """``_staff_satisfies_ng`` = 「course の NG 集合に居なければ True」."""
    s = _make_staff()
    assert _staff_satisfies_ng(s, _make_course()) is True  # NG 未設定 → 常に True
    assert _staff_satisfies_ng(s, _make_course(ng_staff_ids=frozenset({s.staff_id}))) is False
    assert _staff_satisfies_ng(s, _make_course(ng_staff_ids=frozenset({uuid4()}))) is True


def test_ng_staff_excluded_and_other_staff_assigned() -> None:
    """NG スタッフは INF 除外され、 同曜日の別スタッフが割り当たる."""
    ng = _make_staff(name="ng-target")
    ok = _make_staff(name="ok-staff")

    course = _make_course(ng_staff_ids=frozenset({ng.staff_id}))

    assigner = Layer3Assigner()
    result = assigner.solve([course], [ng, ok])

    a = next((x for x in result.assignments if x.course_id == course.course_id), None)
    assert a is not None, "NG でないスタッフが居るのにコースが埋まっていない"
    assert a.staff_id == ok.staff_id, "NG スタッフが割り当てられた (INF 除外の穴)"


def test_all_candidates_ng_leaves_course_unassigned() -> None:
    """全候補が NG のコースは埋めない (= 誤割当より未割当を選ぶ)."""
    s1 = _make_staff(name="ng1")
    s2 = _make_staff(name="ng2")
    course = _make_course(ng_staff_ids=frozenset({s1.staff_id, s2.staff_id}))

    assigner = Layer3Assigner()
    result = assigner.solve([course], [s1, s2])

    assert all(a.course_id != course.course_id for a in result.assignments), (
        "全候補 NG のコースに誰かが割り当てられた"
    )


# ---------------------------------------------------------------------------
# 2) 固定割当 (Phase1 ルート) の NG 違反は free へ回る
# ---------------------------------------------------------------------------


def test_fixed_assignment_ng_violation_routed_to_other_staff() -> None:
    """NG 該当の固定指定は確定されず、 通常ハンガリアンで別スタッフへ回る."""
    fixed = _make_staff(role="manager", name="fixed-ng")
    other = _make_staff(role="staff", name="other")

    m_course = _make_course(code="M", ng_staff_ids=frozenset({fixed.staff_id}))

    assigner = Layer3Assigner()
    result = assigner.solve(
        [m_course],
        [fixed, other],
        fixed_staff_by_course={m_course.course_id: fixed.staff_id},
    )

    a = next((x for x in result.assignments if x.course_id == m_course.course_id), None)
    assert a is not None, "NG でないスタッフが居るのに M コースが未割当"
    assert a.staff_id == other.staff_id, "NG スタッフが固定割当で確定された (固定ルートの穴)"


def test_dropped_ng_fixed_staff_reusable_for_other_course() -> None:
    """NG でドロップした固定スタッフは同曜日の別コースで再利用できる."""
    fixed = _make_staff(role="manager", name="fixed-ng")
    other = _make_staff(role="staff", name="other")

    m_course = _make_course(code="M", ng_staff_ids=frozenset({fixed.staff_id}))
    b_course = _make_course(code="B")  # NG なし

    assigner = Layer3Assigner()
    result = assigner.solve(
        [m_course, b_course],
        [fixed, other],
        fixed_staff_by_course={m_course.course_id: fixed.staff_id},
    )

    by_course = {a.course_id: a.staff_id for a in result.assignments}
    assert by_course.get(m_course.course_id) == other.staff_id
    assert by_course.get(b_course.course_id) == fixed.staff_id, (
        "ドロップした固定スタッフが別コース B で再利用されていない"
    )


def test_fixed_ng_violation_unassigned_when_no_valid_staff() -> None:
    """適合者が居ない場合、 NG 違反の固定は確定されず未割当のまま."""
    fixed = _make_staff(role="manager", name="fixed-ng")
    course = _make_course(code="M", ng_staff_ids=frozenset({fixed.staff_id}))

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [fixed],
        fixed_staff_by_course={course.course_id: fixed.staff_id},
    )
    assert all(a.course_id != course.course_id for a in result.assignments), (
        "NG コースに固定スタッフが割り当てられた"
    )


# ---------------------------------------------------------------------------
# 3) E2E: 全候補 NG → review_items(reason='ng_staff') + DB 未割当のまま
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_all_ng_produces_ng_staff_review_item(db) -> None:
    """唯一の適格者が NG → review_items(reason='ng_staff') に出て DB は未割当のまま."""
    office = await _make_office(db, name="NG-E2E 拠点")
    staff = await _make_db_staff(db, office_id=office.id, code="NG-E1", name="NG E1 担当")
    patient = await _make_db_patient(db, code="NG-E1-P", name="NG E1 患者")
    course = await _make_db_course(db, office_id=office.id)
    await _make_db_visit(db, patient_id=patient.id, course_id=course.id)
    await _add_ng(db, patient_id=patient.id, staff_id=staff.id, note="相性")
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    await db.commit()

    ng_items = [i for i in result.review_items if i.reason == "ng_staff"]
    assert len(ng_items) == 1, f"reason='ng_staff' のレビュー 1 件想定: {result.review_items}"
    item = ng_items[0]
    assert item.course_id == course.id
    # NG を緩和したときの候補 = 唯一の適格者.
    assert item.candidate_staff_id == staff.id
    assert item.also_violates == [], "性別は絡まないので also_violates は空"
    # 原因患者に is_cause が立つ.
    causes = [v.patient_id for v in item.visits if v.is_cause]
    assert causes == [patient.id], f"NG 指定患者が is_cause になっていない: {item.visits}"

    # DB は未割当のまま (= 管理者承認待ち).
    refreshed = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed.assigned_staff_id is None, "NG スタッフが DB に確定されてしまった"

    # 残留警告は出ない (= 現担当が居ないため).
    assert result.unresolved_ng_warnings == []


# ---------------------------------------------------------------------------
# 4) E2E: 性別 + NG 両方ブロック → reason='gender' + also_violates=['ng_staff']
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_gender_and_ng_both_blocked_sets_also_violates(db) -> None:
    """性別だけ / NG だけの緩和では候補ゼロ → reason='gender' + also_violates."""
    office = await _make_office(db, name="NG-E2 拠点")
    male = await _make_db_staff(
        db, office_id=office.id, code="NG-E2-M", name="NG E2 男性", sex="male"
    )
    patient = await _make_db_patient(
        db, code="NG-E2-P", name="NG E2 患者", sex_restriction="female_only"
    )
    course = await _make_db_course(db, office_id=office.id)
    await _make_db_visit(db, patient_id=patient.id, course_id=course.id)
    # 唯一の適格者が「性別も NG も」違反する状態を作る.
    await _add_ng(db, patient_id=patient.id, staff_id=male.id)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    await db.commit()

    items = [i for i in result.review_items if i.course_id == course.id]
    assert len(items) == 1, f"レビュー 1 件想定: {result.review_items}"
    item = items[0]
    assert item.reason == "gender", "段階式: 両方緩和で出た候補は reason='gender' に寄せる"
    assert item.also_violates == ["ng_staff"], (
        f"NG も同時違反であることが併記されていない: {item.also_violates}"
    )
    assert item.candidate_staff_id == male.id

    refreshed = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed.assigned_staff_id is None


# ---------------------------------------------------------------------------
# 5) 残留警告 unresolved_ng_warnings (現担当が NG・候補ゼロ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unresolved_ng_warning_when_current_staff_is_ng(db) -> None:
    """現担当が NG 該当のまま残っている → 残留警告 1 件 (自動クリアしない)."""
    office = await _make_office(db, name="NG-W1 拠点")
    staff = await _make_db_staff(db, office_id=office.id, code="NG-W1-S", name="NG W1 担当")
    patient = await _make_db_patient(db, code="NG-W1-P", name="NG W1 患者")
    course = await _make_db_course(
        db,
        office_id=office.id,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
    )
    await _add_ng(db, patient_id=patient.id, staff_id=staff.id)
    await db.commit()

    target = CourseAssignmentTarget(
        course_id=course.id,
        weekday=0,
        course_code="A",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        patient_ids=[patient.id],
        office_id=office.id,
        ng_staff_ids=frozenset({staff.id}),
        ng_staff_by_patient={patient.id: frozenset({staff.id})},
    )

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_ng_warnings(
        db, course_ids={course.id}, targets_by_id={course.id: target}
    )

    assert len(warnings) == 1, f"残留違反 1 件想定: {warnings}"
    w = warnings[0]
    assert w.course_id == course.id
    assert w.course_code == "A"
    assert w.weekday == 0
    assert w.office_name == "NG-W1 拠点"
    assert w.current_staff_name == "NG W1 担当"
    assert w.patient_names == ["NG W1 患者"]
    assert "NG W1 担当" in w.reason_text
    assert "NG W1 患者" in w.reason_text
    assert "手動" in w.reason_text


@pytest.mark.asyncio
async def test_unresolved_ng_warning_absent_when_current_staff_not_ng(db) -> None:
    """現担当が NG でない → 残留違反ではない → 警告 0 件 (誤検知なし)."""
    office = await _make_office(db, name="NG-W2 拠点")
    ng_staff = await _make_db_staff(db, office_id=office.id, code="NG-W2-A", name="NG W2 NG者")
    other = await _make_db_staff(db, office_id=office.id, code="NG-W2-B", name="NG W2 別人")
    patient = await _make_db_patient(db, code="NG-W2-P", name="NG W2 患者")
    course = await _make_db_course(
        db,
        office_id=office.id,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=other.id,
    )
    await _add_ng(db, patient_id=patient.id, staff_id=ng_staff.id)
    await db.commit()

    target = CourseAssignmentTarget(
        course_id=course.id,
        weekday=0,
        course_code="A",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        patient_ids=[patient.id],
        office_id=office.id,
        ng_staff_ids=frozenset({ng_staff.id}),
        ng_staff_by_patient={patient.id: frozenset({ng_staff.id})},
    )

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_ng_warnings(
        db, course_ids={course.id}, targets_by_id={course.id: target}
    )
    assert warnings == [], f"NG でない担当に警告が出た: {warnings}"


@pytest.mark.asyncio
async def test_e2e_unresolved_ng_warning_when_no_candidate(db) -> None:
    """E2E: 現担当が NG かつ override 候補ゼロ → unresolved_ng_warnings に載る.

    候補ゼロを作るため、 現担当を退職 (status='inactive') とし当該拠点に他の
    稼働スタッフを置かない (= 純粋人手不足). 残留違反は自動クリアせず可視化のみ.
    """
    office = await _make_office(db, name="NG-W3 拠点")
    retired = await _make_db_staff(db, office_id=office.id, code="NG-W3-S", name="NG W3 退職者")
    retired.status = "inactive"
    await db.flush()

    patient = await _make_db_patient(db, code="NG-W3-P", name="NG W3 患者")
    course = await _make_db_course(
        db,
        office_id=office.id,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=retired.id,
    )
    await _make_db_visit(db, patient_id=patient.id, course_id=course.id)
    await _add_ng(db, patient_id=patient.id, staff_id=retired.id)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    await db.commit()

    assert len(result.unresolved_ng_warnings) == 1, (
        f"NG 残留違反が可視化されていない: {result.unresolved_ng_warnings}"
    )
    w = result.unresolved_ng_warnings[0]
    assert w.course_id == course.id
    assert w.current_staff_name == "NG W3 退職者"
    assert w.patient_names == ["NG W3 患者"]
    # 自動クリアしない (= 担当はそのまま残る).
    refreshed = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed.assigned_staff_id == retired.id


# ---------------------------------------------------------------------------
# 6) 2 名体制 secondary の検証 (PO 決定4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secondary_ng_is_assigned_but_warned(db) -> None:
    """partner course の担当が患者の NG → 割当はされるが警告に載る."""
    office = await _make_office(db, name="NG-S1 拠点")
    s1 = await _make_db_staff(db, office_id=office.id, code="NG-S1-A", name="NG S1 一号")
    s2 = await _make_db_staff(db, office_id=office.id, code="NG-S1-B", name="NG S1 二号")

    course_a = await _make_db_course(db, office_id=office.id, code="A")
    course_b = await _make_db_course(db, office_id=office.id, code="B")
    patient = await _make_db_patient(db, code="NG-S1-P", name="NG S1 患者")

    group_id = uuid.uuid4()
    visit_a = await _make_db_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    await _make_db_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    # 患者は s2 を NG 指定 (= B コースの担当が secondary で入ってくる).
    await _add_ng(db, patient_id=patient.id, staff_id=s2.id)
    await db.commit()

    assigner = Layer3Assigner()
    warnings = await assigner._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=course_a.id, staff_id=s1.id),
            StaffAssignment(weekday=0, course_code="B", course_id=course_b.id, staff_id=s2.id),
        ],
    )
    await db.commit()

    # 割当自体は行われる (= 構造的ペアリングを壊さない).
    refreshed_a = await db.scalar(select(Visit).where(Visit.id == visit_a.id))
    assert refreshed_a.primary_staff_id == s1.id
    assert refreshed_a.secondary_staff_id == s2.id

    ng_warnings = [w for w in warnings if w.kind == "ng_staff"]
    assert len(ng_warnings) >= 1, f"secondary の NG 違反が警告されていない: {warnings}"
    w = next(w for w in ng_warnings if w.course_id == course_a.id)
    assert w.staff_id == s2.id
    assert w.staff_name == "NG S1 二号"
    assert w.patient_id == patient.id
    assert w.patient_name == "NG S1 患者"
    assert w.course_code == "A"
    assert w.office_name == "NG-S1 拠点"
    assert w.weekday == 0


@pytest.mark.asyncio
async def test_secondary_gender_is_assigned_but_warned(db) -> None:
    """PO 決定4 (性別版): secondary が患者の性別制限に違反 → 割当 + 警告."""
    office = await _make_office(db, name="NG-S2 拠点")
    male = await _make_db_staff(
        db, office_id=office.id, code="NG-S2-A", name="NG S2 男性", sex="male"
    )
    female = await _make_db_staff(
        db, office_id=office.id, code="NG-S2-B", name="NG S2 女性", sex="female"
    )

    course_a = await _make_db_course(db, office_id=office.id, code="A")
    course_b = await _make_db_course(db, office_id=office.id, code="B")
    patient = await _make_db_patient(
        db, code="NG-S2-P", name="NG S2 患者", sex_restriction="female_only"
    )

    group_id = uuid.uuid4()
    visit_a = await _make_db_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    await _make_db_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    await db.commit()

    assigner = Layer3Assigner()
    warnings = await assigner._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=course_a.id, staff_id=female.id),
            StaffAssignment(weekday=0, course_code="B", course_id=course_b.id, staff_id=male.id),
        ],
    )
    await db.commit()

    refreshed_a = await db.scalar(select(Visit).where(Visit.id == visit_a.id))
    assert refreshed_a.secondary_staff_id == male.id, "違反しても secondary は立てる"

    gender_warnings = [w for w in warnings if w.kind == "gender" and w.course_id == course_a.id]
    assert len(gender_warnings) == 1, f"secondary の性別違反が警告されていない: {warnings}"
    assert gender_warnings[0].staff_id == male.id
    assert gender_warnings[0].patient_id == patient.id


# ---------------------------------------------------------------------------
# 7) リグレッション: NG なしの週では全リストが空
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_ng_week_yields_empty_lists(db) -> None:
    """NG 未設定の患者だけの週では NG 系リストが全て空 (= 既存挙動不変)."""
    office = await _make_office(db, name="NG-R1 拠点")
    await _make_db_staff(db, office_id=office.id, code="NG-R1-A", name="NG R1 一号")
    await _make_db_staff(db, office_id=office.id, code="NG-R1-B", name="NG R1 二号")
    patient = await _make_db_patient(db, code="NG-R1-P", name="NG R1 患者")
    course = await _make_db_course(db, office_id=office.id)
    await _make_db_visit(db, patient_id=patient.id, course_id=course.id)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    await db.commit()

    assert result.unresolved_ng_warnings == []
    assert result.secondary_constraint_warnings == []
    assert [i for i in result.review_items if i.reason == "ng_staff"] == []
    assert all(i.also_violates == [] for i in result.review_items)

    # 通常どおり誰かが割り当たる (= NG 実装で既存割付が壊れていない).
    refreshed = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed.assigned_staff_id is not None
    assert refreshed.course_status == COURSE_STATUS_STAFF_ASSIGNED
