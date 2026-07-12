"""4段ソルバ (マネージャー動員 Stage 2 + ローテ緩和 Stage 3) テスト.

設計書 ``docs/plans/layer3-staged-mobilization-design.md`` v1.1 §7 のテスト観点を実装する:

    観点1: Stage 2 発動条件 (staff >= courses の日は manager が動員されない / 不足日のみ)
    観点2: マネージャー同等ローテ (履歴コストで交代する = UUID 昇順固定にならない)
    観点3: Stage 3 発動条件 (Stage 2 で候補が残る限り緩和されない / 全滅時のみ Q3 緩和 /
            性別・シフト・イベント・拠点は緩和後も INF)
    観点4: via の正しさ (hungarian / fixed / manager_mobilized / rotation_relaxed)
    観点5: 回帰 (新人除外 全Stage / 1日1コース / Gini は manager_mobilized 除外)
    観点6: W28 実況再現 (2 staff + 2 manager + 前週全埋め → 4/4割当・動員2・緩和1)
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
    )


def _make_staff(
    *,
    role: str = "staff",
    sex: str | None = "female",
    work_days: frozenset[int] = frozenset(range(7)),
    is_trainee: bool = False,
    name: str = "staff",
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
# 観点2: マネージャー同等ローテ (履歴コストで交代・UUID 固定にならない)
# ---------------------------------------------------------------------------


def test_stage2_manager_rotation_is_history_driven_not_uuid_fixed() -> None:
    """2 名の manager + 履歴で、 動員が履歴コストにより交代する.

    旧 greedy は UUID 昇順固定で常に同じ manager を選んだ (川名/熊澤の偏り). Stage 2 の
    ハンガリアンは履歴 (前週同コード Q3 除外) を反映するため、 前週その manager が
    担当したコードは避けられ、 もう一方の manager が選ばれる (= 同等ローテ).
    """
    assigner = Layer3Assigner()
    m1 = _make_staff(role="manager", name="mgr1")
    m2 = _make_staff(role="manager", name="mgr2")

    # 前週 m1 が A を担当 → 今週 A の動員では m1 が Q3 除外され m2 が選ばれる.
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
# 観点3: Stage 3 発動条件 + ハード制約は緩和しない
# ---------------------------------------------------------------------------


def test_stage3_not_triggered_while_stage2_can_cover() -> None:
    """Stage 2 (manager 動員) で埋められる限り Stage 3 の緩和は起きない.

    前週 A を担当した staff は Q3 除外だが、 未履歴の manager が Stage 2 で埋めるため
    Stage 3 緩和 (rotation_relaxed) は発生しない.
    """
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    staff = _make_staff(name="s1")  # 前週 A 担当 → Q3 除外
    manager = _make_staff(role="manager", name="mgr")  # 未履歴

    result = assigner.solve(
        [course_a], [staff, manager], history=[(1, "A", staff.staff_id)]
    )

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == manager.staff_id
    assert result.assignments[0].via == "manager_mobilized"
    assert not [a for a in result.assignments if a.via == "rotation_relaxed"]


def test_stage3_relaxes_q3_only_when_all_candidates_exhausted() -> None:
    """候補が Q3 除外で全滅したときのみ前週同コード除外を緩和して埋める."""
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    # 唯一の候補 staff が前週 A を担当 → Stage1/2 では INF、 Stage3 で緩和して埋める.
    staff = _make_staff(name="s1")

    result = assigner.solve([course_a], [staff], history=[(1, "A", staff.staff_id)])

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == staff.staff_id
    assert result.assignments[0].via == "rotation_relaxed"


def test_stage3_does_not_relax_gender_hard_constraint() -> None:
    """Stage 3 でも性別ハード制約は INF のまま (= 緩和されず未割当)."""
    assigner = Layer3Assigner()
    # female_only コースに male staff のみ (かつ前週同コード) → 緩和後も性別 INF.
    course = _make_course(code="A", weekday=0, restrictions=frozenset({"female_only"}))
    male_staff = _make_staff(sex="male", name="male-s")

    result = assigner.solve(
        [course], [male_staff], history=[(1, "A", male_staff.staff_id)]
    )

    assert result.assignments == [], "性別ハード制約が Stage 3 で緩和されてしまった"


def test_stage3_does_not_relax_work_day_hard_constraint() -> None:
    """Stage 3 でも勤務曜日ハード制約は INF のまま (= 緩和されず未割当)."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    # 月曜非勤務の staff (前週同コード) → 緩和後もシフト INF.
    staff = _make_staff(work_days=frozenset({1, 2, 3, 4, 5, 6}), name="s1")

    result = assigner.solve([course], [staff], history=[(1, "A", staff.staff_id)])

    assert result.assignments == [], "勤務曜日ハード制約が Stage 3 で緩和されてしまった"


def test_stage3_does_not_relax_event_hard_constraint() -> None:
    """Stage 3 でも event 時間帯重複ハード制約は INF のまま (= 緩和されず未割当)."""
    from app.models.staff import StaffEvent

    assigner = Layer3Assigner()
    visit_slots = [VisitTimeSlot(time(9, 0), time(9, 30))]
    course = _make_course(code="A", weekday=0, visits=visit_slots)
    staff = _make_staff(name="s1")
    event = StaffEvent(
        staff_id=staff.staff_id,
        starts_at=datetime.combine(TEST_WEEK_MONDAY, time(9, 0)),
        ends_at=datetime.combine(TEST_WEEK_MONDAY, time(12, 0)),
        event_type="leave",
    )

    result = assigner.solve(
        [course],
        [staff],
        history=[(1, "A", staff.staff_id)],
        events_by_staff={staff.staff_id: [event]},
        week_monday=TEST_WEEK_MONDAY,
    )

    assert result.assignments == [], "event ハード制約が Stage 3 で緩和されてしまった"


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
    """1 回の solve で hungarian / fixed / manager_mobilized / rotation_relaxed が揃う."""
    assigner = Layer3Assigner()
    # 月曜: M(固定 manager), A/B/C の 4 コース.
    course_m = _make_course(code="M", weekday=0)
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    course_c = _make_course(code="C", weekday=0)
    # staff 2 名 (s_free は無履歴で 1 件を hungarian、 s_relax は前週 B 担当).
    s_free = _make_staff(name="s_free")
    s_relax = _make_staff(name="s_relax")
    mgr_fixed = _make_staff(role="manager", name="mgr_fixed")
    mgr_mob = _make_staff(role="manager", name="mgr_mob")  # 無履歴で動員

    # s_relax は前週 A,B,C を全部担当 → Stage1 で全 INF. s_free は無履歴.
    history = [
        (1, "A", s_relax.staff_id),
        (1, "B", s_relax.staff_id),
        (1, "C", s_relax.staff_id),
    ]
    result = assigner.solve(
        [course_m, course_a, course_b, course_c],
        [s_free, s_relax, mgr_fixed, mgr_mob],
        history=history,
        fixed_staff_by_course={course_m.course_id: mgr_fixed.staff_id},
    )

    via_kinds = {a.via for a in result.assignments}
    assert "fixed" in via_kinds
    assert "hungarian" in via_kinds
    assert "manager_mobilized" in via_kinds
    assert "rotation_relaxed" in via_kinds
    # 全 4 コース割当 (1日1コース: s_relax は Stage3 緩和で 1 件のみ).
    assert len(result.assignments) == 4
    # rotation_relaxed は正規スタッフ (s_relax) 由来.
    relaxed = [a for a in result.assignments if a.via == "rotation_relaxed"]
    assert len(relaxed) == 1
    assert relaxed[0].staff_id == s_relax.staff_id


# ---------------------------------------------------------------------------
# 観点5: 回帰 (新人除外 / 1日1コース / Gini)
# ---------------------------------------------------------------------------


def test_trainee_never_assigned_in_any_stage() -> None:
    """新人 (is_trainee) は Stage 1/2/3 いずれでも割当されない.

    唯一のフリー要員が新人のみのとき、 緩和しても新人は動員されず未割当のまま.
    """
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    trainee = _make_staff(is_trainee=True, name="trainee")
    # 前週同コードも仕込むが、 新人は Stage 3 プールにも入らない.
    result = assigner.solve([course], [trainee], history=[(1, "A", trainee.staff_id)])
    assert result.assignments == []


def test_trainee_manager_never_mobilized_in_stage2() -> None:
    """新人フラグの manager は Stage 2 の動員候補にもならない."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    trainee_mgr = _make_staff(role="manager", is_trainee=True, name="trainee-mgr")
    result = assigner.solve([course], [trainee_mgr])
    assert result.assignments == []


def test_mobilized_manager_respects_one_course_per_day() -> None:
    """Stage 2 で動員された manager は同日 1 コースのみ (1日1コース原則)."""
    assigner = Layer3Assigner()
    # 月: 3 コース vs staff 1 名 + manager 1 名 → 埋まるのは 2 件、 manager は 1 件のみ.
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
    """Gini (rotation_score) は manager_mobilized を分子から除外する.

    正規スタッフ 2 名が各 1 件 (均等) + manager 動員 1 件のとき、 動員を除外すれば
    分散は完全均等 = rotation_score 0.0. 除外していなければ manager 分が分子に載り
    (分母は eligible の 2 名のまま) 0 にならない.
    """
    assigner = Layer3Assigner()
    # 月: A,B は s1,s2 で均等に埋まる. C は不足 → manager 動員.
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    course_c = _make_course(code="C", weekday=0)
    s1 = _make_staff(name="s1")
    s2 = _make_staff(name="s2")
    manager = _make_staff(role="manager", name="mgr")

    result = assigner.solve([course_a, course_b, course_c], [s1, s2, manager])

    # 3 コース全て割当 (s1,s2 で 2 件 + manager 動員 1 件).
    assert len(result.assignments) == 3
    assert any(a.via == "manager_mobilized" for a in result.assignments)
    # 正規スタッフ 2 名が各 1 件で均等 → 動員除外なら Gini 0.0.
    assert result.rotation_score == 0.0, (
        f"manager_mobilized が Gini から除外されていない: rotation_score={result.rotation_score}"
    )


# ---------------------------------------------------------------------------
# 観点6: W28 実況再現 (2 staff + 2 manager + 前週全埋め → 4/4・動員2・緩和1)
# ---------------------------------------------------------------------------


def test_w28_scenario_full_week_reproduction() -> None:
    """設計書 §2 の W28 月曜シナリオ再現.

    コース A,B,C,D (月). 前週 W27 履歴:
        宇田川(U)={A,B,C,D}, 高岡(T)={A,B,C}, 熊澤(K)={A,C,D}, 川名(N)={}.
    期待: 4/4 割当・manager_mobilized 2 件・rotation_relaxed 1 件・1 回の solve で完了.
        - Stage1: 高岡→D (宇田川は全コード Q3 除外)
        - Stage2: 熊澤→B・川名→A or C (コスト最小側)
        - Stage3: 宇田川→残り 1 コース (前週同コード緩和・rotation_relaxed)
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
    assert via_counts.get("rotation_relaxed", 0) == 1, f"緩和 1 件でない: {via_counts}"

    # 高岡は D を担当 (前週未担当) = hungarian.
    takaoka_assign = [a for a in result.assignments if a.staff_id == takaoka.staff_id]
    assert len(takaoka_assign) == 1
    assert takaoka_assign[0].course_code == "D"
    assert takaoka_assign[0].via == "hungarian"

    # 緩和で埋まったのは宇田川 (全コード前週担当).
    relaxed = [a for a in result.assignments if a.via == "rotation_relaxed"]
    assert relaxed[0].staff_id == udagawa.staff_id

    # 動員は 2 名の manager (熊澤・川名).
    mobilized_ids = {a.staff_id for a in result.assignments if a.via == "manager_mobilized"}
    assert mobilized_ids == {kumazawa.staff_id, kawana.staff_id}


def test_w28_scenario_via_distribution_is_stable() -> None:
    """W28 シナリオの via 分布 (course_code, via) が入力 UUID に依らず安定している.

    注: _run() は毎回新しい UUID を生成するため、本テストが検証するのは
    「経路分布の安定性」であって特定 staff-course 割当の決定性ではない
    (決定性そのものは Wave 5 ジッタが (iso_year, iso_week, patient_id, staff_id)
    seed の決定的ハッシュであることによる。code-review MINOR-2 で命名を精密化).
    """
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
        # staff_id は毎回異なるが、 via の分布 (経路の種類ごとの件数) は不変であるべき.
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
    # via 分布 (course_code, via) の集合が 3 回とも一致.
    assert runs[0] == runs[1] == runs[2], f"非決定的: {runs}"


# ---------------------------------------------------------------------------
# 観点7: エンドポイントレスポンス (新フィールドのスキーマ・空配列デフォルト)
# ---------------------------------------------------------------------------


def test_response_schema_defaults_new_notice_fields_to_empty() -> None:
    """AssignStaffOnlyResponse の新 2 フィールドは default 空配列 (後方互換)."""
    from app.api.v1.schedule import AssignStaffOnlyResponse

    resp = AssignStaffOnlyResponse(
        iso_year=2026, iso_week=28, courses_assigned=0, message="ok"
    )
    assert resp.manager_mobilized_notices == []
    assert resp.rotation_relaxed_notices == []


def test_stage_assignment_notice_schema_shape() -> None:
    """StageAssignmentNoticeSchema が §4 のフィールド (course_id/weekday/course_code/
    staff_id/staff_name) を持つ."""
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


async def test_mobilization_notices_exclude_review_sent_assignments() -> None:
    """Stage 2 割当がレビュー送り（committed_course_ids に含まれない）の場合、
    manager_mobilized_notices に載らないことを検証する.

    背景: ``_build_stage_mobilization_notices`` は ``l3_result.assignments`` 全体から
    集計するが、``committed_course_ids`` に含まれない course は _persist 対象外
    （= 管理者レビュー待ち）。管理者がレビューを却下した場合に通知と実状態が矛盾
    するため、確定済み course_id に限定してフィルタする必要がある。
    """

    from app.api.v1.schedule import _build_stage_mobilization_notices
    from app.services.scheduling.layer3_assignment import Layer3Result, StaffAssignment

    course_id_review = uuid4()   # レビュー送り（未確定）
    staff_id_mgr = uuid4()

    assignments = [
        StaffAssignment(
            weekday=0,
            course_code="A",
            course_id=course_id_review,
            staff_id=staff_id_mgr,
            via="manager_mobilized",
        ),
    ]
    l3_result = Layer3Result(
        assignments=assignments,
        committed_course_ids=[],  # 全割当がレビュー送り（確定ゼロ）
    )

    # db=None: committed_set フィルタで mobilized/relaxed が空になるため
    # DB アクセスに到達しない（early-return パス）。
    mob, relaxed = await _build_stage_mobilization_notices(db=None, l3_result=l3_result)  # type: ignore[arg-type]
    assert mob == [], "レビュー送り割当は manager_mobilized_notices に含まれてはならない"
    assert relaxed == []
