"""Layer 3 ローテーション: 決定的ランダム + 患者中心ローテ + 週跨ぎ重複回避テスト.

Phase 2 で旧 Wave5 (前日/前々日/前週金土の継ぎ接ぎ) を「患者中心の直近 N 回
ローテ」 に作り替えた. 本ファイルは新機構 (``patient_recent_staff`` /
``_load_patient_recent_staff`` / 段階ペナルティ) の検証に更新済み.

検証観点:
  1. deterministic random helper (補助タイブレーク) の再現性 / 週次変動
  2. 患者中心ローテ: 直近担当者を段階ペナルティで避ける (週・曜日横断)
  3. 週内伝搬: 月→火 同患者で別 staff (working_recent prepend)
  4. 週跨ぎ重複: 前週担当者を今週回避 (DB ローダー経由)
  5. sex_restriction はランダムを入れてもハード制約のまま
  6. 既存 W25 fix の挙動維持 (staff_assigned コースは変更されない)
  7. deterministic seed: 同じ (iso_year, iso_week) で 2 回実行すると同じ結果
"""

from __future__ import annotations

from datetime import date, time, timedelta
from uuid import UUID, uuid4

import pytest

from app.models import (
    Course,
    Office,
    Patient,
    Staff,
    StaffShift,
    Visit,
    VisitStaffAssignment,
)
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    COST_PATIENT_RECENT_1,
    COST_PATIENT_RECENT_2,
    COST_PATIENT_RECENT_3,
    COST_W5_ROTATION_MAX,
    HUNGARIAN_INFINITY,
    PATIENT_RECENT_DEPTH,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    _deterministic_random,
)

# 2026-W22: 月曜 = 2026-05-25.
W5_ISO_YEAR = 2026
W5_ISO_WEEK = 22
W5_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def _make_staff(
    name: str,
    *,
    sex: str | None = None,
    role: str = "staff",
    work_days: frozenset[int] = frozenset(range(6)),
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


def _make_course(
    weekday: int,
    code: str,
    *,
    patient_ids: list[UUID] | None = None,
    gender_restrictions: frozenset[str] = frozenset(),
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=gender_restrictions,
        patient_ids=patient_ids if patient_ids is not None else [],
    )


# ---------------------------------------------------------------------------
# 1. deterministic_random helper (補助タイブレーク)
# ---------------------------------------------------------------------------


def test_deterministic_random_is_reproducible() -> None:
    """同じ seed なら何度呼んでも同じ値を返す."""
    pid = uuid4()
    sid = uuid4()
    a = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    b = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    assert a == b
    assert 0.0 <= a < COST_W5_ROTATION_MAX


def test_deterministic_random_changes_across_weeks() -> None:
    """同じ patient × staff でも iso_week が変われば値が変わる (= ローテーション)."""
    pid = uuid4()
    sid = uuid4()
    v1 = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    v2 = _deterministic_random(iso_year=2026, iso_week=23, patient_id=pid, staff_id=sid)
    v3 = _deterministic_random(iso_year=2026, iso_week=24, patient_id=pid, staff_id=sid)
    assert len({v1, v2, v3}) >= 2


def test_recent_penalties_are_staged_and_below_infinity() -> None:
    """段階ペナルティは 1>2>3 の順で、 いずれも HUNGARIAN_INFINITY 未満 (= 段階的緩和)."""
    assert COST_PATIENT_RECENT_1 > COST_PATIENT_RECENT_2 > COST_PATIENT_RECENT_3 > 0.0
    assert COST_PATIENT_RECENT_1 < HUNGARIAN_INFINITY
    # distance(α×〜30) や random(<=10) より桁違いに大きいこと
    assert COST_PATIENT_RECENT_3 > COST_W5_ROTATION_MAX * 100


# ---------------------------------------------------------------------------
# 2. ランダム振り分け (deterministic seed) — solve() level
# ---------------------------------------------------------------------------


def test_rotation_assigns_different_staff_across_weeks() -> None:
    """同 patient で複数週に渡り、 特定 1 staff に固定されない (random タイブレーク)."""
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    s3 = _make_staff("S3")
    s4 = _make_staff("S4")
    pid = uuid4()

    asgn = Layer3Assigner()
    chosen_staff: set[UUID] = set()
    for w in range(20, 30):
        course = _make_course(weekday=0, code="A", patient_ids=[pid])
        result = asgn.solve([course], [s1, s2, s3, s4], iso_year=2026, iso_week=w)
        for a in result.assignments:
            if a.course_id == course.course_id:
                chosen_staff.add(a.staff_id)
    assert len(chosen_staff) >= 2, (
        f"rotation not working: only {len(chosen_staff)} unique staff over 10 weeks"
    )


# ---------------------------------------------------------------------------
# 3. 患者中心ローテ: 直近担当者を段階ペナルティで避ける
# ---------------------------------------------------------------------------


def test_recent_staff_avoided_via_patient_recent_staff() -> None:
    """``patient_recent_staff`` で直近担当 (= index 0) の staff が回避される."""
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    course = _make_course(weekday=1, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    # 「直近担当者 = S-A」 を渡す
    recent: dict[UUID, list[UUID]] = {pid: [s_a.staff_id]}

    result = asgn.solve(
        [course],
        [s_a, s_b, s_c],
        iso_year=2026,
        iso_week=22,
        patient_recent_staff=recent,
    )
    a = next((x for x in result.assignments if x.course_id == course.course_id), None)
    assert a is not None
    # RECENT_1 (1e6) >> random(<=10) なので S-A は避けられる
    assert a.staff_id != s_a.staff_id, f"recent staff not avoided: got {a.staff_id}"


def test_within_week_carryover_avoids_consecutive_days() -> None:
    """solve() 内 working_recent prepend で、 同週内 (月→火) の同 staff 連続を回避."""
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    mon_course = _make_course(weekday=0, code="A", patient_ids=[pid])
    tue_course = _make_course(weekday=1, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    result = asgn.solve(
        [mon_course, tue_course],
        [s_a, s_b, s_c],
        iso_year=2026,
        iso_week=22,
    )
    mon_a = next(a for a in result.assignments if a.course_id == mon_course.course_id)
    tue_a = next(a for a in result.assignments if a.course_id == tue_course.course_id)
    assert mon_a.staff_id != tue_a.staff_id, (
        f"within-week continuity failed: mon=tue={mon_a.staff_id}"
    )


def test_three_day_gap_same_patient_different_staff() -> None:
    """Phase 2 要件①②: 月→金 (3日差) 同患者でも別 staff になる (旧 Wave5 の穴)."""
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    mon = _make_course(weekday=0, code="A", patient_ids=[pid])
    fri = _make_course(weekday=4, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    result = asgn.solve([mon, fri], [s_a, s_b, s_c], iso_year=2026, iso_week=22)
    mon_a = next(a for a in result.assignments if a.course_id == mon.course_id)
    fri_a = next(a for a in result.assignments if a.course_id == fri.course_id)
    assert mon_a.staff_id != fri_a.staff_id, (
        f"3-day-gap continuity failed: mon=fri={mon_a.staff_id} (旧Wave5 の月→金穴)"
    )


def test_graceful_relaxation_fills_when_only_one_eligible() -> None:
    """Phase 2 要件④: 適任者が 1 人しか居ない時は未割当でなく埋まる (段階的緩和).

    直近担当者であっても、 他に適任 (= 当日勤務 + 性別適合) が居なければ
    ペナルティを払ってでも割当する (= HUNGARIAN_INFINITY 未満なので埋まる).
    """
    s_only = _make_staff("S-ONLY")
    pid = uuid4()
    course = _make_course(weekday=0, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    # S-ONLY が直近担当者でも、 他に居ないので埋まるべき
    recent: dict[UUID, list[UUID]] = {pid: [s_only.staff_id]}
    result = asgn.solve(
        [course],
        [s_only],
        iso_year=2026,
        iso_week=22,
        patient_recent_staff=recent,
    )
    a = next((x for x in result.assignments if x.course_id == course.course_id), None)
    assert a is not None and a.staff_id == s_only.staff_id, (
        "段階的緩和が効かず未割当になった (適任者 1 人なら埋めるべき)"
    )


def test_recent_list_truncated_to_depth() -> None:
    """working_recent は最大 DEPTH 件で truncate される (= それ以前は忘れる)."""
    # DEPTH 人 + 1 を連続で当てると、 最古の担当者は recent から落ちる.
    staffs = [_make_staff(f"S{i}") for i in range(PATIENT_RECENT_DEPTH + 2)]
    pid = uuid4()
    asgn = Layer3Assigner()

    # 各曜日に 1 コース (同患者). DEPTH+2 曜日分.
    courses = [
        _make_course(weekday=wd, code="A", patient_ids=[pid])
        for wd in range(PATIENT_RECENT_DEPTH + 2)
    ]
    result = asgn.solve(courses, staffs, iso_year=2026, iso_week=22)
    by_wd = {
        next(c for c in courses if c.course_id == a.course_id).weekday: a.staff_id
        for a in result.assignments
    }
    # 連続する DEPTH+1 曜日は全て distinct (= 直近 DEPTH を避けるため)
    window = [by_wd[wd] for wd in range(PATIENT_RECENT_DEPTH + 1) if wd in by_wd]
    assert len(window) == len(set(window)), f"recent window has duplicates within DEPTH+1: {window}"


# ---------------------------------------------------------------------------
# 4. sex_restriction はランダム要素が入ってもハード制約
# ---------------------------------------------------------------------------


def test_sex_restriction_remains_hard_constraint() -> None:
    """female_only patient に male staff は割当てられない (ランダム要素があっても)."""
    male_staff = _make_staff("M-1", sex="male")
    male_staff2 = _make_staff("M-2", sex="male")
    female_staff = _make_staff("F-1", sex="female")
    pid = uuid4()

    asgn = Layer3Assigner()
    for w in range(20, 30):
        result = asgn.solve(
            [
                _make_course(
                    weekday=0,
                    code="A",
                    patient_ids=[pid],
                    gender_restrictions=frozenset({"female"}),
                )
            ],
            [male_staff, male_staff2, female_staff],
            iso_year=2026,
            iso_week=w,
        )
        assigned = result.assignments
        assert len(assigned) == 1
        assert assigned[0].staff_id == female_staff.staff_id, (
            f"week={w}: female-only violated, got {assigned[0].staff_id}"
        )


# ---------------------------------------------------------------------------
# 5. deterministic seed: 2 回実行で同じ結果
# ---------------------------------------------------------------------------


def test_deterministic_seed_reproducible() -> None:
    """同じ (iso_year, iso_week) で 2 回 solve() しても同じ結果 (random は決定的)."""
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    s3 = _make_staff("S3")
    s4 = _make_staff("S4")
    p1, p2, p3 = uuid4(), uuid4(), uuid4()

    def _build_courses() -> list[CourseAssignmentTarget]:
        return [
            _make_course(weekday=0, code="A", patient_ids=[p1]),
            _make_course(weekday=0, code="B", patient_ids=[p2]),
            _make_course(weekday=0, code="C", patient_ids=[p3]),
        ]

    asgn = Layer3Assigner()
    courses_a = _build_courses()
    courses_b = _build_courses()
    res_a = asgn.solve(courses_a, [s1, s2, s3, s4], iso_year=2026, iso_week=22)
    res_b = asgn.solve(courses_b, [s1, s2, s3, s4], iso_year=2026, iso_week=22)

    def _staff_by_first_patient(
        courses: list[CourseAssignmentTarget], assignments: list
    ) -> dict[UUID, UUID]:
        m: dict[UUID, UUID] = {}
        for a in assignments:
            c = next((cc for cc in courses if cc.course_id == a.course_id), None)
            if c is not None and c.patient_ids:
                m[c.patient_ids[0]] = a.staff_id
        return m

    map_a = _staff_by_first_patient(courses_a, res_a.assignments)
    map_b = _staff_by_first_patient(courses_b, res_b.assignments)
    assert map_a == map_b, f"non-deterministic: {map_a} vs {map_b}"


# ---------------------------------------------------------------------------
# 6. 既存 W25 fix 維持: staff_assigned コースは変更されない (DB integration)
# ---------------------------------------------------------------------------


async def _seed_office_and_staff(db) -> dict[str, object]:
    """integration test 用の office + staff seed."""
    inage = Office(name="本店(稲毛)", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    s1 = Staff(
        code="W5-01",
        name="S1",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    s2 = Staff(
        code="W5-02",
        name="S2",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    s3 = Staff(
        code="W5-03",
        name="S3",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add_all([s1, s2, s3])
    await db.flush()

    for staff in [s1, s2, s3]:
        for wd in range(6):
            db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    await db.flush()

    return {"office": inage, "staff": [s1, s2, s3]}


@pytest.mark.asyncio
async def test_existing_assigned_staff_not_overridden(db) -> None:
    """既に staff_assigned 状態のコースは Layer 3 再実行で staff が変わらない (W25 fix 維持)."""
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    staff_list = seeds["staff"]

    p1 = Patient(code="W5-P1", name="P1", status="active")
    db.add(p1)
    await db.flush()

    locked_staff = staff_list[0]
    course = Course(
        iso_year=W5_ISO_YEAR,
        iso_week=W5_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=locked_staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    visit = Visit(
        patient_id=p1.id,
        visit_date=W5_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=locked_staff.id,
    )
    db.add(visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=locked_staff.id))
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W5_ISO_YEAR, iso_week=W5_ISO_WEEK)
    await db.commit()

    a = next((x for x in result.assignments if x.course_id == course.id), None)
    assert a is not None
    assert a.staff_id == locked_staff.id, (
        f"staff_assigned course was overridden: expected {locked_staff.id}, got {a.staff_id}"
    )


# ---------------------------------------------------------------------------
# 7. 週跨ぎ重複回避 (前週担当 → 今週回避) — DB ローダー経由
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_crossover_db_integration(db) -> None:
    """Phase 2 要件③: 前週金 visit が S1 のとき、 今週月の同 patient は S1 以外."""
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, s2, s3 = seeds["staff"]

    p1 = Patient(code="W5-P2", name="P2", status="active")
    db.add(p1)
    await db.flush()

    prev_week_friday = W5_WEEK_MONDAY - timedelta(days=3)  # 前週金曜
    assert prev_week_friday.weekday() == 4

    prev_course = Course(
        iso_year=2026,
        iso_week=21,
        weekday=4,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=s1.id,
        office_id=office.id,
    )
    db.add(prev_course)
    await db.flush()

    prev_visit = Visit(
        patient_id=p1.id,
        visit_date=prev_week_friday,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=prev_course.id,
        primary_staff_id=s1.id,
    )
    db.add(prev_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=s1.id))

    cur_course = Course(
        iso_year=W5_ISO_YEAR,
        iso_week=W5_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(cur_course)
    await db.flush()

    cur_visit = Visit(
        patient_id=p1.id,
        visit_date=W5_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=cur_course.id,
    )
    db.add(cur_visit)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W5_ISO_YEAR, iso_week=W5_ISO_WEEK)
    await db.commit()

    a = next((x for x in result.assignments if x.course_id == cur_course.id), None)
    assert a is not None
    assert a.staff_id != s1.id, (
        f"week-crossover (DB) failed: today=S1 same as prev-fri, got {a.staff_id}"
    )


# ---------------------------------------------------------------------------
# 8. re-run swing 回避: course_fixed の VSA は history 対象外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_loader_only_loads_staff_assigned_courses(db) -> None:
    """前回 Layer 3 実行の残骸 (course_fixed のままの VSA) は recent に含まれない.

    再実行時の swing 原因を排除するための回帰テスト:
      - 前週金: ``course_fixed`` の course + VSA(S1)  → recent 対象外
    """
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, _s2, _s3 = seeds["staff"]

    p1 = Patient(code="W5-P3", name="P3", status="active")
    db.add(p1)
    await db.flush()

    prev_week_friday = W5_WEEK_MONDAY - timedelta(days=3)
    assert prev_week_friday.weekday() == 4

    prev_course = Course(
        iso_year=2026,
        iso_week=21,
        weekday=4,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,  # ← staff_assigned ではない
        office_id=office.id,
    )
    db.add(prev_course)
    await db.flush()

    prev_visit = Visit(
        patient_id=p1.id,
        visit_date=prev_week_friday,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=prev_course.id,
    )
    db.add(prev_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=s1.id))
    await db.flush()

    assigner = Layer3Assigner()
    recent = await assigner._load_patient_recent_staff(db, week_monday=W5_WEEK_MONDAY)
    # course_fixed の VSA は除外されるので、 recent に S1 が現れないはず.
    assert p1.id not in recent or s1.id not in recent.get(p1.id, []), (
        f"course_fixed VSA leaked into recent: {recent}"
    )

    # 対照: staff_assigned に変えたら recent に乗ること.
    prev_course.course_status = COURSE_STATUS_STAFF_ASSIGNED
    await db.flush()
    recent_after = await assigner._load_patient_recent_staff(db, week_monday=W5_WEEK_MONDAY)
    assert p1.id in recent_after, "staff_assigned VSA should be loaded into recent"
    assert s1.id in recent_after[p1.id]


@pytest.mark.asyncio
async def test_recent_loader_orders_newest_first_and_caps_depth(db) -> None:
    """ローダーは visit_date desc 順で distinct staff を最大 DEPTH 件まで返す."""
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, s2, s3 = seeds["staff"]

    p1 = Patient(code="W5-P4", name="P4", status="active")
    db.add(p1)
    await db.flush()

    # 過去 3 週: 古い順に s3 (3週前), s2 (2週前), s1 (1週前) を当てる.
    assigner = Layer3Assigner()
    week_offsets_staff = [(3, s3), (2, s2), (1, s1)]
    for weeks_ago, staff in week_offsets_staff:
        vdate = W5_WEEK_MONDAY - timedelta(weeks=weeks_ago)
        iso = vdate.isocalendar()
        c = Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=vdate.weekday(),
            code="A",
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            assigned_staff_id=staff.id,
            office_id=office.id,
        )
        db.add(c)
        await db.flush()
        v = Visit(
            patient_id=p1.id,
            visit_date=vdate,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=c.id,
        )
        db.add(v)
        await db.flush()
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=staff.id))
    await db.commit()

    recent = await assigner._load_patient_recent_staff(db, week_monday=W5_WEEK_MONDAY)
    assert p1.id in recent
    # 最近順 = [s1 (1週前), s2 (2週前), s3 (3週前)]
    assert recent[p1.id][0] == s1.id, f"newest-first violated: {recent[p1.id]}"
    assert len(recent[p1.id]) <= PATIENT_RECENT_DEPTH


@pytest.mark.asyncio
async def test_recent_loader_tie_break_is_deterministic_by_staff_id(db) -> None:
    """同時刻・同患者の複数 staff (required_staff_count>1) は staff_id 昇順で安定化.

    修正1 (loader tie-break 決定性) の回帰テスト:
      - 1 visit (同 visit_date, 同 start_time) に 2 staff の VSA を付与する.
      - (visit_date, start_time) が完全一致するため、 staff_id.asc() の
        tie-break が無いと recent list の順序は DB 物理行順依存で非決定的になる.
      - tie-break 追加後は、 同時刻同患者の複数 staff が staff_id 昇順で並ぶことを検証.
    """
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, s2, _s3 = seeds["staff"]

    p1 = Patient(code="W5-P5", name="P5", status="active")
    db.add(p1)
    await db.flush()

    vdate = W5_WEEK_MONDAY - timedelta(weeks=1)
    iso = vdate.isocalendar()
    course = Course(
        iso_year=iso.year,
        iso_week=iso.week,
        weekday=vdate.weekday(),
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    visit = Visit(
        patient_id=p1.id,
        visit_date=vdate,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        course_id=course.id,
    )
    db.add(visit)
    await db.flush()
    # 2 staff を同一 (visit_date, start_time) の visit に紐付ける.
    # 物理 INSERT 順を「staff_id 降順」 にして、 tie-break が無いと
    # 先頭が大きい方になる (= 非決定/規約違反) ことを突く.
    first_id, second_id = sorted([s1.id, s2.id])
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=second_id))
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=first_id))
    await db.commit()

    assigner = Layer3Assigner()
    recent = await assigner._load_patient_recent_staff(db, week_monday=W5_WEEK_MONDAY)
    assert p1.id in recent
    # staff_id 昇順で安定化されるので、 INSERT 順に依らず [first_id, second_id].
    assert recent[p1.id] == [first_id, second_id], (
        f"tie-break by staff_id.asc() violated: {recent[p1.id]}"
    )
