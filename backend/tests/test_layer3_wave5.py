"""Wave 5: Layer 3 ランダム振り分け + 連続割付防止 + 週跨ぎ重複回避テスト.

検証観点:
  1. 同 patient で iso_week=N と N+1 で staff の選択が変わる傾向 (deterministic random)
  2. 連続割付 penalty: 月曜 staff A → 火曜は staff B 優先
  3. 週跨ぎ重複 penalty: 前週金 staff A → 今週月は staff B 優先
  4. sex_restriction はランダムを入れてもハード制約のまま (female_only → male NG)
  5. 既存 W25 fix の挙動維持 (staff_assigned コースは変更されない)
  6. deterministic seed: 同じ (iso_year, iso_week) で 2 回実行すると同じ結果
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
    COST_W5_PATIENT_PREV2_DAY,
    COST_W5_PATIENT_PREV_DAY,
    COST_W5_ROTATION_MAX,
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
# 1. deterministic_random helper
# ---------------------------------------------------------------------------


def test_w5_deterministic_random_is_reproducible() -> None:
    """同じ seed なら何度呼んでも同じ値を返す."""
    pid = uuid4()
    sid = uuid4()
    a = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    b = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    assert a == b
    assert 0.0 <= a < COST_W5_ROTATION_MAX


def test_w5_deterministic_random_changes_across_weeks() -> None:
    """同じ patient × staff でも iso_week が変われば値が変わる (= ローテーション)."""
    pid = uuid4()
    sid = uuid4()
    v1 = _deterministic_random(iso_year=2026, iso_week=22, patient_id=pid, staff_id=sid)
    v2 = _deterministic_random(iso_year=2026, iso_week=23, patient_id=pid, staff_id=sid)
    v3 = _deterministic_random(iso_year=2026, iso_week=24, patient_id=pid, staff_id=sid)
    # 3 つの異なる週で同じ値になる確率は (1/2^64)^2 でほぼゼロ
    assert len({v1, v2, v3}) >= 2


# ---------------------------------------------------------------------------
# 2. ランダム振り分け (deterministic seed) — solve() level
# ---------------------------------------------------------------------------


def test_w5_rotation_assigns_different_staff_across_weeks() -> None:
    """同 patient で iso_week=22 と 23 で staff の傾向が変わる.

    history なし / 距離なし / 同条件下で、cost に rotation_random が乗ることで
    異なる週の最適解が変わりうることを確認する。
    """
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    s3 = _make_staff("S3")
    s4 = _make_staff("S4")

    # 1 コース / 1 患者. 各週で同じコース構造 (course_id は週ごとに作り直す).
    pid = uuid4()

    asgn = Layer3Assigner()

    # 10 週分シミュレートして「特定の 1 staff に固定」されないことを確認.
    chosen_staff: set[UUID] = set()
    for w in range(20, 30):
        course = _make_course(weekday=0, code="A", patient_ids=[pid])
        result = asgn.solve(
            [course],
            [s1, s2, s3, s4],
            iso_year=2026,
            iso_week=w,
        )
        # この course に対する staff
        for a in result.assignments:
            if a.course_id == course.course_id:
                chosen_staff.add(a.staff_id)
    # 10 週中、複数の staff が選ばれている = ローテーションが回っている
    assert len(chosen_staff) >= 2, (
        f"rotation not working: only {len(chosen_staff)} unique staff over 10 weeks"
    )


# ---------------------------------------------------------------------------
# 3. 連続割付防止 (前日 penalty)
# ---------------------------------------------------------------------------


def test_w5_continuity_penalty_prevents_consecutive_days() -> None:
    """同 patient × 月曜割当 staff_A が居る場合、火曜は別 staff が選ばれる.

    history を patient_prev_day_history で渡し、火曜のセル評価で
    +1000 のソフトペナルティが効くことを検証する。
    """
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    # 火曜 (weekday=1) の単一コース
    tue_course = _make_course(weekday=1, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()

    # 「月曜の visit で同 patient に S-A が割当てられていた」と DB 履歴で与える
    prev_day_hist: dict[tuple[int, UUID], set[UUID]] = {
        (1, pid): {s_a.staff_id},  # weekday=1 のセル評価で「前日 (= 月曜) = S-A」
    }

    result = asgn.solve(
        [tue_course],
        [s_a, s_b, s_c],
        iso_year=2026,
        iso_week=22,
        patient_prev_day_history=prev_day_hist,
    )

    tue_assignment = next(
        (a for a in result.assignments if a.course_id == tue_course.course_id), None
    )
    assert tue_assignment is not None
    # rotation_random (<=10) << 前日 penalty (1000) なので、S-A は回避されるはず
    assert tue_assignment.staff_id != s_a.staff_id, (
        f"continuity penalty failed: tue staff = {tue_assignment.staff_id}"
    )


def test_w5_prev2_day_penalty_softer_than_prev_day() -> None:
    """前々日 penalty は前日 penalty より小さい (200 vs 1000)."""
    assert COST_W5_PATIENT_PREV2_DAY < COST_W5_PATIENT_PREV_DAY
    assert COST_W5_PATIENT_PREV2_DAY == 200.0
    assert COST_W5_PATIENT_PREV_DAY == 1000.0


def test_w5_within_week_carryover_avoids_consecutive_days() -> None:
    """solve() 内のループで当日割当が patient レベルに伝搬され、翌日に同 staff が回避される.

    DB 履歴を渡さずとも、同週内 (月→火) で同 staff が連続しないことを確認する。
    """
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
    # 月曜と火曜で同じ staff が同 patient を取らないことを確認
    assert mon_a.staff_id != tue_a.staff_id, (
        f"within-week continuity failed: mon=tue={mon_a.staff_id}"
    )


# ---------------------------------------------------------------------------
# 4. 週跨ぎ重複回避 (前週金/土 → 今週月)
# ---------------------------------------------------------------------------


def test_w5_week_crossover_avoids_friday_to_monday() -> None:
    """前週金に S-A → 今週月の同 patient は S-A 以外が選ばれる."""
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    mon_course = _make_course(weekday=0, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    prev_week_hist: dict[UUID, set[UUID]] = {pid: {s_a.staff_id}}

    result = asgn.solve(
        [mon_course],
        [s_a, s_b, s_c],
        iso_year=2026,
        iso_week=22,
        patient_prev_week_fri_sat_history=prev_week_hist,
    )

    mon_a = next(a for a in result.assignments if a.course_id == mon_course.course_id)
    # 800 >> rotation_random max 10 なので S-A は回避
    assert mon_a.staff_id != s_a.staff_id, (
        f"week-crossover penalty failed: mon staff = {mon_a.staff_id}"
    )


def test_w5_week_crossover_only_applies_on_monday() -> None:
    """前週金/土 penalty は月曜 (weekday=0) でのみ適用される.

    火曜のコースには前週金/土 history は影響しないことを確認.
    """
    s_a = _make_staff("S-A")
    s_b = _make_staff("S-B")
    s_c = _make_staff("S-C")

    pid = uuid4()
    # weekday=1 (火曜) の単一コース. history は前週金/土 = S-A.
    tue_course = _make_course(weekday=1, code="A", patient_ids=[pid])

    asgn = Layer3Assigner()
    prev_week_hist: dict[UUID, set[UUID]] = {pid: {s_a.staff_id}}

    # S-A を強制的に選ばせるため、他の staff は work_days を空に
    s_b_off = StaffInfo(
        staff_id=s_b.staff_id,
        name=s_b.name,
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(),
    )
    s_c_off = StaffInfo(
        staff_id=s_c.staff_id,
        name=s_c.name,
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(),
    )

    result = asgn.solve(
        [tue_course],
        [s_a, s_b_off, s_c_off],
        iso_year=2026,
        iso_week=22,
        patient_prev_week_fri_sat_history=prev_week_hist,
    )

    # 火曜なので前週金/土 history は影響せず、S-A が普通に取れる
    tue_a = next(a for a in result.assignments if a.course_id == tue_course.course_id)
    assert tue_a.staff_id == s_a.staff_id


# ---------------------------------------------------------------------------
# 5. sex_restriction はランダム要素が入ってもハード制約
# ---------------------------------------------------------------------------


def test_w5_sex_restriction_remains_hard_constraint() -> None:
    """female_only patient に male staff は割当てられない (ランダム要素があっても)."""
    male_staff = _make_staff("M-1", sex="male")
    male_staff2 = _make_staff("M-2", sex="male")
    female_staff = _make_staff("F-1", sex="female")

    pid = uuid4()
    # female_only restriction. course は週ごとに作り直す.

    asgn = Layer3Assigner()
    # 複数週試行しても常に female が選ばれる
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
# 6. deterministic seed: 2 回実行で同じ結果
# ---------------------------------------------------------------------------


def test_w5_deterministic_seed_reproducible() -> None:
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
    # course_id は uuid4 で毎回変わるが、(staff_id ベース) の選択は patient seed に
    # 依存するため、固定の patient 順序で staff 選択が再現することを確認する。
    courses_a = _build_courses()
    courses_b = _build_courses()
    # patient_ids を同じにすれば、course_id が異なっても staff 選択は同じはず.
    # course の cost は course_code に依存しないので、患者 patient_ids[0] と
    # staff_id の hash で選択が決まる.
    res_a = asgn.solve(courses_a, [s1, s2, s3, s4], iso_year=2026, iso_week=22)
    res_b = asgn.solve(courses_b, [s1, s2, s3, s4], iso_year=2026, iso_week=22)

    # patient_id 経由で staff 選択をマップ化
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
# 7. 既存 W25 fix 維持: staff_assigned コースは変更されない (DB integration)
# ---------------------------------------------------------------------------


async def _seed_office_and_staff(db) -> dict[str, object]:
    """W5 integration test 用の office + staff seed."""
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
async def test_w5_existing_assigned_staff_not_overridden(db) -> None:
    """既に staff_assigned 状態のコースは Layer 3 再実行で staff が変わらない (W25 fix 維持)."""
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    staff_list = seeds["staff"]

    # 患者
    p1 = Patient(code="W5-P1", name="P1", status="active")
    db.add(p1)
    await db.flush()

    # 月曜のコース: 既に staff_assigned 状態 (assigned_staff_id = S1)
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


@pytest.mark.asyncio
async def test_w5_week_crossover_db_integration(db) -> None:
    """DB 経由 — 前週金 visit が S1 のとき、今週月の同 patient は S1 以外が選ばれる."""
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, s2, s3 = seeds["staff"]

    # 患者
    p1 = Patient(code="W5-P2", name="P2", status="active")
    db.add(p1)
    await db.flush()

    # 前週 (W21) の金曜日 visit に S1 が割り当てられていた履歴
    prev_week_friday = W5_WEEK_MONDAY - timedelta(days=3)  # 前週金曜
    assert prev_week_friday.weekday() == 4  # Fri

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

    # 今週 (W22) の月曜コース (course_fixed) — 同 patient
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
    # 800 >> rotation_random max 10 なので S1 は回避される
    assert a.staff_id != s1.id, (
        f"week-crossover penalty (DB) failed: today=S1 same as prev-fri, got {a.staff_id}"
    )


# ---------------------------------------------------------------------------
# 8. MEDIUM #1 (re-run swing 回避): course_fixed の VSA は history 対象外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w5_history_only_loads_staff_assigned_courses(db) -> None:
    """前回 Layer 3 実行の残骸 (course_fixed のままの VSA) は history に含まれない.

    再実行時の swing 原因を排除するための回帰テスト:
      - 前週金: ``course_status='course_fixed'`` の course + VSA(S1)  → history 対象外
      - 今週月: 同 patient の course_fixed → S1 が選ばれても OK (history 無効化済み)
    Wave 5 fix 前は前週金の VSA が week-crossover penalty を発火させて S1 が回避されていた.
    """
    seeds = await _seed_office_and_staff(db)
    office = seeds["office"]
    s1, _s2, _s3 = seeds["staff"]

    # 患者
    p1 = Patient(code="W5-P3", name="P3", status="active")
    db.add(p1)
    await db.flush()

    # 前週 (W21) の金曜日 visit — course_status は course_fixed (= 未確定の残骸)
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
    # 前回 Layer 3 実行で書き残った VSA を再現 — S1 にひも付く
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=s1.id))

    # _load_patient_visit_history を直接呼び、course_fixed の VSA が
    # history map に乗らないことを確認する.
    assigner = Layer3Assigner()
    prev_day_map, prev2_day_map, prev_week_map = await assigner._load_patient_visit_history(
        db, week_monday=W5_WEEK_MONDAY
    )
    # course_fixed の VSA は除外されるので、いずれの map にも S1 が現れないはず.
    assert p1.id not in prev_week_map, (
        f"course_fixed VSA leaked into prev_week_map: {prev_week_map}"
    )
    # prev_day / prev2_day は当該週内 visit から構築される. 前週 visit のみ用意した
    # 本シナリオでは patient_id は当該週 map には現れない.
    assert all(p1.id != pid for (_, pid) in prev_day_map)
    assert all(p1.id != pid for (_, pid) in prev2_day_map)

    # 対照: もし course を staff_assigned に変えたら history に乗ること.
    prev_course.course_status = COURSE_STATUS_STAFF_ASSIGNED
    await db.flush()
    _pd, _pd2, prev_week_map_after = await assigner._load_patient_visit_history(
        db, week_monday=W5_WEEK_MONDAY
    )
    assert p1.id in prev_week_map_after, "staff_assigned VSA should be loaded into prev_week_map"
    assert s1.id in prev_week_map_after[p1.id]


# ---------------------------------------------------------------------------
# 9. MEDIUM #2: seed_patient is deterministic even if patient_ids order differs
# ---------------------------------------------------------------------------


def test_w5_seed_patient_deterministic_with_unsorted_list() -> None:
    """同じ集合の patient_ids でも順序が異なる場合に seed (= 選択 staff) が同じ.

    Wave 5 fix: ``seed_patient = min(course.patient_ids)`` により、
    DB 取得順依存を排除する.
    """
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    s3 = _make_staff("S3")
    s4 = _make_staff("S4")

    # 3 患者 — シャッフル可能な異なる順序で 2 コースを作る.
    p_low = UUID("00000000-0000-0000-0000-000000000001")
    p_mid = UUID("00000000-0000-0000-0000-000000000002")
    p_high = UUID("00000000-0000-0000-0000-000000000003")

    course_sorted = _make_course(weekday=0, code="A", patient_ids=[p_low, p_mid, p_high])
    course_unsorted = _make_course(weekday=0, code="A", patient_ids=[p_high, p_low, p_mid])

    asgn = Layer3Assigner()
    res_sorted = asgn.solve([course_sorted], [s1, s2, s3, s4], iso_year=2026, iso_week=22)
    res_unsorted = asgn.solve([course_unsorted], [s1, s2, s3, s4], iso_year=2026, iso_week=22)

    a_sorted = next(a for a in res_sorted.assignments if a.course_id == course_sorted.course_id)
    a_unsorted = next(
        a for a in res_unsorted.assignments if a.course_id == course_unsorted.course_id
    )
    # min(patient_ids) は順序に依らず p_low なので、staff 選択も同じ.
    assert a_sorted.staff_id == a_unsorted.staff_id, (
        f"seed_patient non-deterministic w.r.t. list order: "
        f"sorted={a_sorted.staff_id} vs unsorted={a_unsorted.staff_id}"
    )
