"""4段ソルバ (マネージャー動員 Stage 2 + 拠点跨ぎ救援 Stage 3) テスト.

設計書 ``docs/plans/layer3-staged-mobilization-design.md`` v2.0 §11 のテスト観点を実装する:

    観点1: Stage 2 発動条件 (staff >= コース数の日は manager が動員されない / 不足日のみ)
    観点2: マネージャー同等ローテ (履歴 β コストで交代する = UUID 昇順固定にならない)
    観点3: Stage 3 拠点跨ぎ救援 (Stage 2 で埋まれば発動しない / 越境救援で充足 /
            性別・シフト・イベントは越境でも INF / 採用ガード / スワップ)
    観点4: via の正しさ (hungarian / fixed / manager_mobilized / cross_office)
    観点5: 回帰 (新人除外 全Stage / 1日1コース / Gini は manager_mobilized 除外 /
            βスキップ削除 = weeks_ago=1 が weight 1.0 で効く)
    観点6: W28 実況再現 (2 staff + 2 manager + 前週全埋め → 4/4割当・動員2・緩和0・
            宇田川は Stage 1 の hungarian で入る = Q3 全廃)
    観点7: エンドポイントレスポンス (新フィールドのスキーマ・空配列デフォルト)

新人除外・1日1コース原則・決定性 (乱数/時刻禁止) を全 Stage で維持することを守る.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID, uuid4

from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
)

TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_course(
    *,
    code: str = "A",
    weekday: int = 0,
    restrictions: frozenset[str] = frozenset(),
    visits: list[VisitTimeSlot] | None = None,
    patient_ids: list[UUID] | None = None,
    office_id: UUID | None = None,
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=35.6383,
        centroid_lng=140.1041,
        gender_restrictions=restrictions,
        patient_ids=patient_ids if patient_ids is not None else [uuid4()],
        visits=visits if visits is not None else [VisitTimeSlot(time(9, 0), time(9, 30))],
        office_id=office_id,
    )


def _make_staff(
    *,
    role: str = "staff",
    sex: str | None = "female",
    work_days: frozenset[int] = frozenset(range(7)),
    is_trainee: bool = False,
    name: str = "staff",
    primary_office_id: UUID | None = None,
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=sex,
        role=role,
        primary_office_lat=35.6383,
        primary_office_lng=140.1041,
        work_days=work_days,
        is_trainee=is_trainee,
        primary_office_id=primary_office_id,
    )


# ---------------------------------------------------------------------------
# 観点1: Stage 2 発動条件
# ---------------------------------------------------------------------------


def test_stage2_not_triggered_when_staff_cover_all_courses() -> None:
    """staff 数 >= コース数の日は Stage 2 が発動せず manager は 1 件も割当されない."""
    assigner = Layer3Assigner()
    courses = [_make_course(code="A", weekday=0), _make_course(code="B", weekday=0)]
    s1 = _make_staff(name="s1")
    s2 = _make_staff(name="s2")
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve(courses, [s1, s2, manager])

    assert len(result.assignments) == 2
    assert {a.via for a in result.assignments} == {"hungarian"}
    assert manager.staff_id not in {a.staff_id for a in result.assignments}


def test_stage2_triggered_only_on_shortage_day() -> None:
    """不足日 (月) のみ manager が動員され、 充足日 (火) は動員されないこと."""
    assigner = Layer3Assigner()
    # 月: 2 コース vs staff 1 名 → 1 件不足 → Stage 2 動員.
    mon_a = _make_course(code="A", weekday=0)
    mon_b = _make_course(code="B", weekday=0)
    # 火: 1 コース vs staff 1 名 → 充足 → 動員なし.
    tue_a = _make_course(code="A", weekday=1)
    staff = _make_staff(name="s1")
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve([mon_a, mon_b, tue_a], [staff, manager])

    mobilized = [a for a in result.assignments if a.via == "manager_mobilized"]
    assert len(mobilized) == 1
    assert mobilized[0].weekday == 0, "動員は不足日(月)のみのはず"
    # 火は staff で充足し manager 不使用.
    tue_assigns = [a for a in result.assignments if a.weekday == 1]
    assert len(tue_assigns) == 1
    assert tue_assigns[0].staff_id == staff.staff_id


# ---------------------------------------------------------------------------
# 観点2: マネージャー同等ローテ (履歴 β コストで交代・UUID 固定にならない)
# ---------------------------------------------------------------------------


def test_stage2_manager_rotation_is_history_driven_not_uuid_fixed() -> None:
    """2 名の manager + 履歴で、 動員が履歴 β コストにより交代する.

    旧 greedy は UUID 昇順固定で常に同じ manager を選んだ (川名/熊澤の偏り). Stage 2 の
    ハンガリアンは履歴 (前週同コードの β ソフトペナルティ) を反映するため、 前週その
    manager が担当したコードは避けられ、 もう一方の manager が選ばれる (= 同等ローテ).
    v2.0: Q3 ハード除外は全廃したが、 β ソフトペナルティ (weeks_ago=1 が weight 1.0) が
    残るため交代は成立する.
    """
    assigner = Layer3Assigner()
    m1 = _make_staff(role="manager", name="mgr1")
    m2 = _make_staff(role="manager", name="mgr2")

    # 前週 m1 が A を担当 → 今週 A の動員では m1 が β 加算され m2 (無履歴) が選ばれる.
    course_a1 = _make_course(code="A", weekday=0)
    r1 = assigner.solve([course_a1], [m1, m2], history=[(1, "A", m1.staff_id)])
    assert len(r1.assignments) == 1
    assert r1.assignments[0].staff_id == m2.staff_id
    assert r1.assignments[0].via == "manager_mobilized"

    # 前週 m2 が A を担当 → 今週は m1 が選ばれる (= UUID 固定ではなく履歴で交代).
    course_a2 = _make_course(code="A", weekday=0)
    r2 = assigner.solve([course_a2], [m1, m2], history=[(1, "A", m2.staff_id)])
    assert len(r2.assignments) == 1
    assert r2.assignments[0].staff_id == m1.staff_id


# ---------------------------------------------------------------------------
# 観点3: Stage 3 拠点跨ぎ救援 + ハード制約は緩和しない
# ---------------------------------------------------------------------------


def test_stage3_not_triggered_when_stage2_can_cover() -> None:
    """Stage 2 (manager 動員) で埋められる限り Stage 3 の拠点跨ぎ救援は起きない."""
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    staff = _make_staff(name="s1")  # 1 名
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve([course_a, course_b], [staff, manager])

    # staff 1 + manager 動員 1 = 2/2 充足. cross_office / スワップは起きない.
    assert len(result.assignments) == 2
    assert not [a for a in result.assignments if a.via == "cross_office"]
    assert result.rescue_swaps == []


def test_stage3_cross_office_fills_when_local_office_exhausted() -> None:
    """自拠点で埋まらない female_only コースを隣接拠点の女性が越境救援する.

    office1 は男性 1 名のみ (= 男性1名拠点). female_only コース A は自拠点では埋まらず、
    Stage 3 で隣接拠点 office2 の女性が越境 (relax_office) して充足する.
    """
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    course_a = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    male_o1 = _make_staff(sex="male", name="male-o1", primary_office_id=o1)
    female_o2 = _make_staff(sex="female", name="female-o2", primary_office_id=o2)

    result = assigner.solve([course_a], [male_o1, female_o2])

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == female_o2.staff_id
    assert result.assignments[0].via == "cross_office"


def test_stage3_swap_frees_local_for_female_only_course() -> None:
    """木曜都賀A 再現: female_only コース × 男性1名拠点 + 隣接拠点女性 → スワップ充足.

    office1: female_only コース A + 無制限コース B. 自拠点は女性 F_o1 1 名. F_o1 は
    (A の患者を直近担当のため) Stage 1 で B に付き、 A が未割当で残る. 隣接 office2 の
    男性 M_o2 は A (female_only) を担当できないが B は担当できる. Stage 3 は当日を全再解し、
    M_o2 を B に越境入れ (cross_office)、 F_o1 を A に戻す (= スワップ) ことで全充足する.
    """
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    p_a = uuid4()
    p_b = uuid4()
    course_a = _make_course(
        code="A",
        weekday=0,
        restrictions=frozenset({"female_only"}),
        office_id=o1,
        patient_ids=[p_a],
    )
    course_b = _make_course(code="B", weekday=0, office_id=o1, patient_ids=[p_b])
    female_o1 = _make_staff(sex="female", name="female-o1", primary_office_id=o1)
    male_o2 = _make_staff(sex="male", name="male-o2", primary_office_id=o2)

    # F_o1 を A の患者の直近担当者にして Stage 1 で B へ寄せる (A を未割当に残す).
    result = assigner.solve(
        [course_a, course_b],
        [female_o1, male_o2],
        patient_recent_staff={p_a: [female_o1.staff_id]},
    )

    # 全 2 コース充足.
    assert len(result.assignments) == 2
    by_course = {a.course_id: a for a in result.assignments}
    # A は自拠点女性 F_o1 が担当 (性別ハードは緩和されない).
    assert by_course[course_a.course_id].staff_id == female_o1.staff_id
    # B は隣接拠点の男性 M_o2 が越境救援 (cross_office).
    assert by_course[course_b.course_id].staff_id == male_o2.staff_id
    assert by_course[course_b.course_id].via == "cross_office"
    # cross_office ≥ 1 かつ rescue_swaps ≥ 1 (B の担当が F_o1 → M_o2 に入れ替わった).
    assert len([a for a in result.assignments if a.via == "cross_office"]) >= 1
    assert len(result.rescue_swaps) >= 1
    swap = next(s for s in result.rescue_swaps if s.course_id == course_b.course_id)
    assert swap.before_staff_id == female_o1.staff_id
    assert swap.after_staff_id == male_o2.staff_id


def test_stage3_does_not_relax_gender_hard_constraint() -> None:
    """Stage 3 拠点跨ぎ救援でも性別ハード制約は INF のまま (= 越境しても未割当)."""
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    # female_only コースに male しか居ない (自拠点も隣接拠点も男性) → 越境しても性別 INF.
    course = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    male_o1 = _make_staff(sex="male", name="male-o1", primary_office_id=o1)
    male_o2 = _make_staff(sex="male", name="male-o2", primary_office_id=o2)

    result = assigner.solve([course], [male_o1, male_o2])

    assert result.assignments == [], "性別ハード制約が Stage 3 で緩和されてしまった"


def test_stage3_does_not_relax_work_day_hard_constraint() -> None:
    """Stage 3 でも勤務曜日ハード制約は INF のまま (= 越境しても未割当)."""
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    course = _make_course(code="A", weekday=0, office_id=o1)
    # 自拠点は該当拠点に居ない構成 (o1 スタッフ無し) + 隣接拠点 o2 の月曜非勤務 staff.
    staff_o2 = _make_staff(
        work_days=frozenset({1, 2, 3, 4, 5, 6}), name="s-o2", primary_office_id=o2
    )

    result = assigner.solve([course], [staff_o2])

    assert result.assignments == [], "勤務曜日ハード制約が Stage 3 で緩和されてしまった"


def test_stage3_does_not_relax_event_hard_constraint() -> None:
    """Stage 3 でも event 時間帯重複ハード制約は INF のまま (= 越境しても未割当)."""
    from app.models.staff import StaffEvent

    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    visit_slots = [VisitTimeSlot(time(9, 0), time(9, 30))]
    course = _make_course(code="A", weekday=0, visits=visit_slots, office_id=o1)
    staff_o2 = _make_staff(name="s-o2", primary_office_id=o2)
    event = StaffEvent(
        staff_id=staff_o2.staff_id,
        starts_at=datetime.combine(TEST_WEEK_MONDAY, time(9, 0)),
        ends_at=datetime.combine(TEST_WEEK_MONDAY, time(12, 0)),
        event_type="leave",
    )

    result = assigner.solve(
        [course],
        [staff_o2],
        events_by_staff={staff_o2.staff_id: [event]},
        week_monday=TEST_WEEK_MONDAY,
    )

    assert result.assignments == [], "event ハード制約が Stage 3 で緩和されてしまった"


def test_stage3_partial_improvement_adopted_when_shortage_remains() -> None:
    """採用ガード: 部分改善 (2件中1件だけ充足) でも「未割当が厳密に減る」ため採用される.

    female_only コース 2 件に対し女性は隣接拠点 1 名のみ. 越境で 1 件を充足すると
    未割当は 2 → 1 に減るため採用ガードを通過する (全充足は要員数的に不可能で、
    残り 1 件は unassigned として残る = 真の人員不足の可視化).
    """
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    course_a = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    course_b = _make_course(
        code="B", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    male_o1 = _make_staff(sex="male", name="male-o1", primary_office_id=o1)
    female_o2 = _make_staff(sex="female", name="female-o2", primary_office_id=o2)

    result = assigner.solve([course_a, course_b], [male_o1, female_o2])

    # 越境救援で 1 件は埋まる (未割当 2 → 1 に減るため採用される).
    assert len(result.assignments) == 1
    assert result.assignments[0].via == "cross_office"


def test_stage3_coverage_regression_guard_prevents_adoption() -> None:
    """退行ガード: 再解が既存カバーコースを未割当化する解しか出せない場合は採用されない.

    採用ガード v2.0: ``new_unassigned < original_unassigned`` だけでなく、
    「元々カバーされていた全コースが引き続きカバーされている」
    (``covered_course_ids <= new_covered``) も必要。

    シナリオ:
    - コース A / B / C (all in O1, female_only). 要員 F1 (O1) + F2 (O2).
    - Stage 1+2 後: A → F1 (hungarian). B / C は未割当 (old_unassigned=2).
    - Stage 3 で ``_solve_matching`` をモック: [F1→B, F2→C] を返す
      (= A を捨てて B+C を充足. new_unassigned=1 < 2 → 旧ガードはパス).
    - 旧コード: A が無報告で未割当化 & rescue_swaps にも載らない (PO 要件違反 BUG).
    - 新ガード: covered_course_ids={A} ⊄ new_covered={B,C} → None を返し元を維持.
    """
    from unittest.mock import patch

    from app.services.scheduling.layer3_assignment import StaffAssignment

    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()

    course_a = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    course_b = _make_course(
        code="B", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    course_c = _make_course(
        code="C", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )

    f1 = _make_staff(name="F1", sex="female", primary_office_id=o1)
    f2 = _make_staff(name="F2", sex="female", primary_office_id=o2)

    # Stage 1+2 後の想定状態: A のみカバー済, B と C は未割当.
    day_assignments_stub = [
        StaffAssignment(
            weekday=0,
            course_code="A",
            course_id=course_a.course_id,
            staff_id=f1.staff_id,
            via="hungarian",
        )
    ]

    # _solve_matching が A を捨てて B+C を充足する「退行解」を返すようにモック.
    # (実ソルバーはコスト構造次第でこれを選択し得る)
    bad_matched = [
        StaffAssignment(
            weekday=0,
            course_code="B",
            course_id=course_b.course_id,
            staff_id=f1.staff_id,
            via="hungarian",
        ),
        StaffAssignment(
            weekday=0,
            course_code="C",
            course_id=course_c.course_id,
            staff_id=f2.staff_id,
            via="hungarian",
        ),
    ]

    with patch.object(assigner, "_solve_matching", return_value=bad_matched):
        result = assigner._solve_stage3_cross_office(
            weekday=0,
            day_courses=[course_a, course_b, course_c],
            day_assignments=day_assignments_stub,
            staff_pool=[f1, f2],
            fixed_staff_by_course={},
            history=[],
            prev_day_pairs=set(),
            events_by_staff={},
            week_monday=None,
            iso_year=None,
            iso_week=None,
            patient_recent_staff={},
        )

    # 退行ガードが機能し None を返すこと (元の割当 [A→F1] を維持).
    assert result is None, (
        "退行ガード: A を失う解(B+Cのみ充足)を採用してはならない。"
        "None を返して Stage 1+2 の結果を維持すること。"
    )


def test_stage3_no_rescue_when_full_shortage_cannot_improve() -> None:
    """要員そのものが居ないコースは越境救援でも埋まらず未割当のまま (採用ガード)."""
    assigner = Layer3Assigner()
    o1 = uuid4()
    course = _make_course(code="A", weekday=0, office_id=o1)
    # 自拠点も隣接拠点も要員ゼロ.
    result = assigner.solve([course], [])
    assert result.assignments == []
    assert result.rescue_swaps == []


# ---------------------------------------------------------------------------
# 観点4: via の正しさ
# ---------------------------------------------------------------------------


def test_via_hungarian_for_normal_stage1_assignment() -> None:
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    staff = _make_staff(name="s1")
    result = assigner.solve([course], [staff])
    assert len(result.assignments) == 1
    assert result.assignments[0].via == "hungarian"


def test_via_fixed_for_fixed_assignment() -> None:
    assigner = Layer3Assigner()
    course_m = _make_course(code="M", weekday=0)
    manager = _make_staff(role="manager", name="mgr")
    result = assigner.solve(
        [course_m], [manager], fixed_staff_by_course={course_m.course_id: manager.staff_id}
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].via == "fixed"


def test_via_covers_all_four_kinds_in_one_solve() -> None:
    """1 回の solve で hungarian / fixed / manager_mobilized / cross_office が揃う."""
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    # office1 の月曜: M(固定 manager), A(female_only=越境), B/C(無制限).
    course_m = _make_course(code="M", weekday=0, office_id=o1)
    course_a = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    course_b = _make_course(code="B", weekday=0, office_id=o1)
    course_c = _make_course(code="C", weekday=0, office_id=o1)
    # office1 の要員: 男性 staff 1 名 + 固定 manager + 動員 manager (いずれも男性).
    s_local = _make_staff(sex="male", name="s_local", primary_office_id=o1)
    mgr_fixed = _make_staff(role="manager", sex="male", name="mgr_fixed", primary_office_id=o1)
    mgr_mob = _make_staff(role="manager", sex="male", name="mgr_mob", primary_office_id=o1)
    # 隣接 office2 の女性 (A の越境救援要員).
    female_o2 = _make_staff(sex="female", name="female_o2", primary_office_id=o2)

    result = assigner.solve(
        [course_m, course_a, course_b, course_c],
        [s_local, mgr_fixed, mgr_mob, female_o2],
        fixed_staff_by_course={course_m.course_id: mgr_fixed.staff_id},
    )

    via_kinds = {a.via for a in result.assignments}
    assert "fixed" in via_kinds
    assert "hungarian" in via_kinds
    assert "manager_mobilized" in via_kinds
    assert "cross_office" in via_kinds
    # 全 4 コース割当.
    assert len(result.assignments) == 4
    # cross_office は隣接拠点女性 (female_o2) が female_only コース A を越境救援.
    cross = [a for a in result.assignments if a.via == "cross_office"]
    assert len(cross) == 1
    assert cross[0].staff_id == female_o2.staff_id
    assert cross[0].course_id == course_a.course_id


# ---------------------------------------------------------------------------
# 観点5: 回帰 (新人除外 / 1日1コース / Gini / βスキップ削除)
# ---------------------------------------------------------------------------


def test_trainee_never_assigned_in_any_stage() -> None:
    """新人 (is_trainee) は Stage 1/2/3 いずれでも割当されない."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    trainee = _make_staff(is_trainee=True, name="trainee")
    result = assigner.solve([course], [trainee], history=[(1, "A", trainee.staff_id)])
    assert result.assignments == []


def test_trainee_manager_never_mobilized_in_stage2() -> None:
    """新人フラグの manager は Stage 2 の動員候補にもならない."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    trainee_mgr = _make_staff(role="manager", is_trainee=True, name="trainee-mgr")
    result = assigner.solve([course], [trainee_mgr])
    assert result.assignments == []


def test_trainee_not_used_in_cross_office_rescue() -> None:
    """新人は Stage 3 拠点跨ぎ救援の候補プールにも入らない (全 Stage 除外)."""
    assigner = Layer3Assigner()
    o1 = uuid4()
    o2 = uuid4()
    course = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), office_id=o1
    )
    male_o1 = _make_staff(sex="male", name="male-o1", primary_office_id=o1)
    trainee_female_o2 = _make_staff(
        sex="female", is_trainee=True, name="trainee-o2", primary_office_id=o2
    )
    result = assigner.solve([course], [male_o1, trainee_female_o2])
    assert result.assignments == [], "新人が越境救援に動員された"


def test_mobilized_manager_respects_one_course_per_day() -> None:
    """Stage 2 で動員された manager は同日 1 コースのみ (1日1コース原則)."""
    assigner = Layer3Assigner()
    courses = [
        _make_course(code="A", weekday=0),
        _make_course(code="B", weekday=0),
        _make_course(code="C", weekday=0),
    ]
    staff = _make_staff(name="s1")
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve(courses, [staff, manager])

    mgr_assigns = [a for a in result.assignments if a.staff_id == manager.staff_id]
    assert len(mgr_assigns) == 1, "manager が同日複数コースに動員された (1日1コース違反)"


def test_gini_excludes_manager_mobilized() -> None:
    """Gini (rotation_score) は manager_mobilized を分子から除外する."""
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    course_c = _make_course(code="C", weekday=0)
    s1 = _make_staff(name="s1")
    s2 = _make_staff(name="s2")
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve([course_a, course_b, course_c], [s1, s2, manager])

    assert len(result.assignments) == 3
    assert any(a.via == "manager_mobilized" for a in result.assignments)
    assert result.rotation_score == 0.0, (
        f"manager_mobilized が Gini から除外されていない: rotation_score={result.rotation_score}"
    )


def test_beta_penalty_applies_to_last_week_weight_one() -> None:
    """βスキップ削除: weeks_ago=1 (前週同コード) が weight 1.0 で β に乗る.

    v2.0: 旧実装は weeks_ago<=1 を β 計算で skip していた (Q3 ハード除外に委ねていた).
    Q3 全廃に伴い skip も撤去したため、 weeks_ago=1 は weight 1.0 (最重) で β に加算される.
    前週同コード担当 s1 と無履歴 s2 では β 差 (5.0) により s2 が選ばれる (= 除外ではなく
    ソフトな交代). skip が残っていれば β 差 0 でタイ (先頭 s1) になるため、 s2 選択は
    weight 1.0 が効いている証左.
    """
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    s1 = _make_staff(name="s1")
    s2 = _make_staff(name="s2")

    # iso_year/week を渡さない = 決定的ジッタなし → β 差のみで決着.
    result = assigner.solve([course], [s1, s2], history=[(1, "A", s1.staff_id)])

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id


# ---------------------------------------------------------------------------
# 観点6: W28 実況再現 (2 staff + 2 manager + 前週全埋め → 4/4・動員2・緩和0)
# ---------------------------------------------------------------------------


def test_w28_scenario_full_week_reproduction() -> None:
    """設計書 §11 の W28 月曜シナリオ再現 (Q3 全廃版).

    コース A,B,C,D (月). 前週 W27 履歴:
        宇田川(U)={A,B,C,D}, 高岡(T)={A,B,C}, 熊澤(K)={A,C,D}, 川名(N)={}.
    期待: 4/4 割当・manager_mobilized 2 件・cross_office 0 件・1 回の solve で完了.
        - Stage1: 宇田川・高岡が各 1 コース (Q3 全廃で宇田川も hungarian で入る)
        - Stage2: 熊澤・川名を動員して残り 2 コースを埋める
        - Stage3: 未割当なし → 発動しない (緩和 0)
    """
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    course_c = _make_course(code="C", weekday=0)
    course_d = _make_course(code="D", weekday=0)

    udagawa = _make_staff(name="udagawa")  # 正規
    takaoka = _make_staff(name="takaoka")  # 正規
    kumazawa = _make_staff(role="manager", name="kumazawa")
    kawana = _make_staff(role="manager", name="kawana")

    history = [
        (1, "A", udagawa.staff_id),
        (1, "B", udagawa.staff_id),
        (1, "C", udagawa.staff_id),
        (1, "D", udagawa.staff_id),
        (1, "A", takaoka.staff_id),
        (1, "B", takaoka.staff_id),
        (1, "C", takaoka.staff_id),
        (1, "A", kumazawa.staff_id),
        (1, "C", kumazawa.staff_id),
        (1, "D", kumazawa.staff_id),
    ]

    result = assigner.solve(
        [course_a, course_b, course_c, course_d],
        [udagawa, takaoka, kumazawa, kawana],
        history=history,
    )

    # 4/4 割当.
    assert len(result.assignments) == 4, f"4/4 割当されていない: {result.assignments}"
    assert len({a.course_id for a in result.assignments}) == 4

    via_counts: dict[str, int] = {}
    for a in result.assignments:
        via_counts[a.via] = via_counts.get(a.via, 0) + 1

    assert via_counts.get("manager_mobilized", 0) == 2, f"動員 2 件でない: {via_counts}"
    # Q3 全廃により全員が自拠点内で埋まるため、 越境 (cross_office) は発生しない.
    assert via_counts.get("cross_office", 0) == 0, f"越境が発生: {via_counts}"
    assert result.rescue_swaps == []

    # 宇田川は Stage 1 の hungarian で入る (前週全担当でも Q3 除外されない).
    udagawa_assign = [a for a in result.assignments if a.staff_id == udagawa.staff_id]
    assert len(udagawa_assign) == 1
    assert udagawa_assign[0].via == "hungarian"

    # 高岡も Stage 1 hungarian (前週未担当の D が最安).
    takaoka_assign = [a for a in result.assignments if a.staff_id == takaoka.staff_id]
    assert len(takaoka_assign) == 1
    assert takaoka_assign[0].course_code == "D"
    assert takaoka_assign[0].via == "hungarian"

    # 動員は 2 名の manager (熊澤・川名).
    mobilized_ids = {a.staff_id for a in result.assignments if a.via == "manager_mobilized"}
    assert mobilized_ids == {kumazawa.staff_id, kawana.staff_id}


def test_w28_scenario_via_distribution_is_stable() -> None:
    """W28 シナリオの via 分布 (course_code, via) が入力 UUID に依らず安定している."""
    assigner = Layer3Assigner()

    def _run() -> list[tuple[str, str]]:
        a = _make_course(code="A", weekday=0)
        b = _make_course(code="B", weekday=0)
        c = _make_course(code="C", weekday=0)
        d = _make_course(code="D", weekday=0)
        u = _make_staff(name="u")
        t = _make_staff(name="t")
        k = _make_staff(role="manager", name="k")
        n = _make_staff(role="manager", name="n")
        history = [
            (1, "A", u.staff_id),
            (1, "B", u.staff_id),
            (1, "C", u.staff_id),
            (1, "D", u.staff_id),
            (1, "A", t.staff_id),
            (1, "B", t.staff_id),
            (1, "C", t.staff_id),
            (1, "A", k.staff_id),
            (1, "C", k.staff_id),
            (1, "D", k.staff_id),
        ]
        res = assigner.solve([a, b, c, d], [u, t, k, n], history=history)
        return sorted((x.course_code, x.via) for x in res.assignments)

    runs = [_run() for _ in range(3)]
    assert runs[0] == runs[1] == runs[2], f"非決定的: {runs}"


# ---------------------------------------------------------------------------
# 観点7: エンドポイントレスポンス (新フィールドのスキーマ・空配列デフォルト)
# ---------------------------------------------------------------------------


def test_response_schema_defaults_new_notice_fields_to_empty() -> None:
    """AssignStaffOnlyResponse の新 2 フィールドは default 空配列 (後方互換)."""
    from app.api.v1.schedule import AssignStaffOnlyResponse

    resp = AssignStaffOnlyResponse(iso_year=2026, iso_week=28, courses_assigned=0, message="ok")
    assert resp.manager_mobilized_notices == []
    assert resp.cross_office_notices == []
    assert resp.rescue_swap_notices == []


def test_stage_assignment_notice_schema_shape() -> None:
    """StageAssignmentNoticeSchema が §4 のフィールドを持つ (Stage 2 動員通知)."""
    from app.api.v1.schedule import StageAssignmentNoticeSchema

    notice = StageAssignmentNoticeSchema(
        course_id=UUID("11111111-1111-1111-1111-111111111111"),
        weekday=0,
        course_code="A",
        staff_id=UUID("22222222-2222-2222-2222-222222222222"),
        staff_name="熊澤　妙子",
    )
    assert notice.course_code == "A"
    assert notice.staff_name == "熊澤　妙子"
    assert notice.weekday == 0


def test_cross_office_notice_schema_shape() -> None:
    """CrossOfficeNoticeSchema が §11.3 のフィールド (course/staff 拠点名含む) を持つ."""
    from app.api.v1.schedule import CrossOfficeNoticeSchema

    notice = CrossOfficeNoticeSchema(
        course_id=UUID("11111111-1111-1111-1111-111111111111"),
        weekday=3,
        course_code="A",
        course_office_name="都賀",
        staff_id=UUID("22222222-2222-2222-2222-222222222222"),
        staff_name="宇田川　優莉",
        staff_office_name="稲毛",
    )
    assert notice.course_office_name == "都賀"
    assert notice.staff_office_name == "稲毛"
    assert notice.weekday == 3


def test_rescue_swap_notice_schema_shape() -> None:
    """RescueSwapNoticeSchema が §11.3 のフィールド (before/after 氏名) を持つ."""
    from app.api.v1.schedule import RescueSwapNoticeSchema

    notice = RescueSwapNoticeSchema(
        course_id=UUID("11111111-1111-1111-1111-111111111111"),
        weekday=3,
        course_code="B",
        before_staff_name="高岡",
        after_staff_name="宇田川　優莉",
    )
    assert notice.before_staff_name == "高岡"
    assert notice.after_staff_name == "宇田川　優莉"


async def test_mobilization_notices_exclude_review_sent_assignments() -> None:
    """Stage 2/3 の割当・入替がレビュー送り（committed_course_ids 外）なら通知に載らない.

    背景: ``_build_stage_mobilization_notices`` は ``l3_result`` 全体から集計するが、
    ``committed_course_ids`` に含まれない course は _persist 対象外（管理者レビュー待ち）.
    manager_mobilized / cross_office / rescue_swaps いずれも committed で絞る必要がある.
    """
    from app.api.v1.schedule import _build_stage_mobilization_notices
    from app.services.scheduling.layer3_assignment import (
        Layer3Result,
        RescueSwap,
        StaffAssignment,
    )

    course_id_review = uuid4()
    staff_id_mgr = uuid4()

    assignments = [
        StaffAssignment(
            weekday=0,
            course_code="A",
            course_id=course_id_review,
            staff_id=staff_id_mgr,
            via="manager_mobilized",
        ),
        StaffAssignment(
            weekday=0,
            course_code="B",
            course_id=uuid4(),
            staff_id=uuid4(),
            via="cross_office",
        ),
    ]
    l3_result = Layer3Result(
        assignments=assignments,
        committed_course_ids=[],  # 全割当がレビュー送り（確定ゼロ）
        rescue_swaps=[
            RescueSwap(
                course_id=uuid4(),
                weekday=0,
                course_code="B",
                before_staff_id=uuid4(),
                after_staff_id=uuid4(),
            )
        ],
    )

    # db=None: committed_set フィルタで全リストが空になり DB アクセスに到達しない.
    mob, cross, swaps = await _build_stage_mobilization_notices(
        db=None,  # type: ignore[arg-type]
        l3_result=l3_result,
    )
    assert mob == [], "レビュー送りの動員は manager_mobilized_notices に含まれてはならない"
    assert cross == [], "レビュー送りの越境は cross_office_notices に含まれてはならない"
    assert swaps == [], "レビュー送りの入替は rescue_swap_notices に含まれてはならない"
