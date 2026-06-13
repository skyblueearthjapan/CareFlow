"""Phase G-90: 拠点ハード制約 + 距離撤去 の純粋関数 (solve) テスト.

設計 (Phase G-90):
    - スタッフは住所で拠点が決まり、 自拠点のコースにのみ割り当てられる
      (= 距離 km は割付に無関係). ``CourseAssignmentTarget.office_id`` と
      ``StaffInfo.effective_office_for_weekday(weekday)`` が一致しない組は
      ``_cost_single_cell`` で ``HUNGARIAN_INFINITY`` (= 割当不可).
    - 距離 (α) は cost から撤去. ``_distance_km`` は total_distance_km レポート用
      にのみ残す.

検証観点:
    1. 他拠点排除: office B の staff は office A のコースに絶対割り当たらない
       (短スタッフでも). office A が短スタッフなら当該コースは未割当.
    2. 応援: secondary office を設定し primary を当該曜日休業にした staff が
       secondary のコースに割当可能 (effective_office 経由).
    3. 距離無関係: 同拠点・同条件の 2 staff で距離差があっても距離では決まらない.
       cost に距離項が無いこと.
    4. 後方互換: office_id を持たない合成 fixture は制約 skip で従来どおり.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from app.services.scheduling.layer3_assignment import (
    HUNGARIAN_INFINITY,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
)

# 拠点 ID (= office A / office B).
OFFICE_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
OFFICE_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")

MONDAY = 0


def _staff(
    *,
    name: str,
    primary_office_id: UUID,
    work_days: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
    primary_office_lat: float | None = 35.6383,
    primary_office_lng: float | None = 140.1041,
    secondary_office_ids: tuple[UUID, ...] = (),
    office_operating_weekdays: dict[UUID, frozenset[int]] | None = None,
    role: str = "staff",
    sex: str | None = None,
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid.uuid4(),
        name=name,
        sex=sex,
        role=role,
        primary_office_lat=primary_office_lat,
        primary_office_lng=primary_office_lng,
        work_days=work_days,
        primary_office_id=primary_office_id,
        secondary_office_ids=secondary_office_ids,
        office_operating_weekdays=office_operating_weekdays or {},
    )


def _course(
    *,
    code: str,
    office_id: UUID | None,
    weekday: int = MONDAY,
    centroid_lat: float | None = 35.7,
    centroid_lng: float | None = 140.2,
    patient_ids: list[UUID] | None = None,
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid.uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=centroid_lat,
        centroid_lng=centroid_lng,
        gender_restrictions=frozenset(),
        patient_ids=patient_ids if patient_ids is not None else [uuid.uuid4()],
        office_id=office_id,
    )


# ---------------------------------------------------------------------------
# 1) 他拠点排除
# ---------------------------------------------------------------------------


def test_other_office_staff_never_assigned() -> None:
    """office B の staff は office A のコースに絶対割り当たらない."""
    course_a = _course(code="A", office_id=OFFICE_A)
    staff_b = _staff(name="B-staff", primary_office_id=OFFICE_B)

    assigner = Layer3Assigner()
    result = assigner.solve([course_a], [staff_b])

    # office B の staff のみ → office A のコースは割当不能 (未割当).
    assert result.assignments == []


def test_short_office_a_leaves_course_unassigned() -> None:
    """office A が短スタッフ (1 名) で 2 コースなら 1 コースが未割当.

    office B の staff が余っていても office A のコースには入れない (= 漏れない).
    """
    course_a1 = _course(code="A", office_id=OFFICE_A)
    course_a2 = _course(code="B", office_id=OFFICE_A)
    staff_a = _staff(name="A-staff", primary_office_id=OFFICE_A)
    # office B の staff は余っているが office A コースには使えない.
    staff_b1 = _staff(name="B-staff1", primary_office_id=OFFICE_B)
    staff_b2 = _staff(name="B-staff2", primary_office_id=OFFICE_B)

    assigner = Layer3Assigner()
    result = assigner.solve([course_a1, course_a2], [staff_a, staff_b1, staff_b2])

    # office A staff は 1 名 → A コース 2 つのうち 1 つしか埋まらない.
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == staff_a.staff_id
    # 割り当てられた staff は必ず office A.
    assigned_ids = {a.staff_id for a in result.assignments}
    assert staff_b1.staff_id not in assigned_ids
    assert staff_b2.staff_id not in assigned_ids


def test_each_office_staff_serves_own_office() -> None:
    """office A staff -> A コース, office B staff -> B コース に正しく振り分く."""
    course_a = _course(code="A", office_id=OFFICE_A)
    course_b = _course(code="B", office_id=OFFICE_B)
    staff_a = _staff(name="A-staff", primary_office_id=OFFICE_A)
    staff_b = _staff(name="B-staff", primary_office_id=OFFICE_B)

    assigner = Layer3Assigner()
    result = assigner.solve([course_a, course_b], [staff_a, staff_b])

    by_course = {a.course_id: a.staff_id for a in result.assignments}
    assert by_course[course_a.course_id] == staff_a.staff_id
    assert by_course[course_b.course_id] == staff_b.staff_id


# ---------------------------------------------------------------------------
# 2) 応援 (secondary office)
# ---------------------------------------------------------------------------


def test_support_staff_assignable_to_secondary_when_primary_closed() -> None:
    """primary 休業曜日に secondary office のコースへ割当可能 (effective_office 経由).

    staff primary=OFFICE_A (Mon 休業), secondary=OFFICE_B (Mon 稼働) のとき、
    Mon の effective_office は OFFICE_B → OFFICE_B のコースに割り当たる.
    """
    # primary OFFICE_A は Mon (0) 休業、 secondary OFFICE_B は Mon 稼働.
    op_map = {
        OFFICE_A: frozenset({1, 2, 3, 4, 5}),  # Mon 休業
        OFFICE_B: frozenset({0, 1, 2, 3, 4, 5}),  # Mon 稼働
    }
    support = _staff(
        name="support",
        primary_office_id=OFFICE_A,
        secondary_office_ids=(OFFICE_B,),
        office_operating_weekdays=op_map,
        work_days=frozenset({0, 1, 2, 3, 4, 5}),
    )
    course_b = _course(code="B", office_id=OFFICE_B, weekday=MONDAY)

    assigner = Layer3Assigner()
    result = assigner.solve([course_b], [support])

    # effective_office(Mon) = OFFICE_B なので OFFICE_B コースに割当.
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == support.staff_id


def test_support_staff_not_assignable_to_primary_when_primary_closed() -> None:
    """primary 休業曜日には primary office のコースには入れない (effective = secondary).

    効果検証の対称ケース: 同 staff は Mon に OFFICE_A コースへは入れない.
    """
    op_map = {
        OFFICE_A: frozenset({1, 2, 3, 4, 5}),  # Mon 休業
        OFFICE_B: frozenset({0, 1, 2, 3, 4, 5}),
    }
    support = _staff(
        name="support",
        primary_office_id=OFFICE_A,
        secondary_office_ids=(OFFICE_B,),
        office_operating_weekdays=op_map,
        work_days=frozenset({0, 1, 2, 3, 4, 5}),
    )
    course_a = _course(code="A", office_id=OFFICE_A, weekday=MONDAY)

    assigner = Layer3Assigner()
    result = assigner.solve([course_a], [support])

    # Mon は effective=OFFICE_B なので OFFICE_A コースは割当不能.
    assert result.assignments == []


# ---------------------------------------------------------------------------
# 3) 距離無関係 (cost に距離項なし)
# ---------------------------------------------------------------------------


def test_distance_not_in_cost_function() -> None:
    """_cost_single_cell の戻り値に距離項が含まれない.

    同拠点・同条件・履歴なし・iso 指定なし (= rotation_random も 0) の 2 staff は、
    座標 (= 距離) が大きく違っても cost が一致する (= 距離が cost に効かない).
    """
    assigner = Layer3Assigner()
    course = _course(code="A", office_id=OFFICE_A, centroid_lat=35.0, centroid_lng=140.0)

    near = _staff(
        name="near", primary_office_id=OFFICE_A, primary_office_lat=35.01, primary_office_lng=140.01
    )
    far = _staff(
        name="far", primary_office_id=OFFICE_A, primary_office_lat=36.5, primary_office_lng=141.5
    )

    cost_near = assigner._cost_single_cell(weekday=MONDAY, course=course, staff=near, history=[])
    cost_far = assigner._cost_single_cell(weekday=MONDAY, course=course, staff=far, history=[])
    # 距離差があっても cost は同一 (= 距離項が無い).
    assert cost_near == cost_far
    # ハード制約に当たらず有限 (= 割当可能).
    assert cost_near < HUNGARIAN_INFINITY


def test_assignment_determined_by_tiebreak_not_distance() -> None:
    """同拠点・同条件の 2 staff で、 距離差があっても距離では決まらない.

    1 コース・2 staff (両者 office A). 近距離 staff が距離で有利になる旧挙動なら
    near が選ばれるが、 距離撤去後は決定的タイブレーク (Hungarian 安定性) で決まり、
    距離は影響しない. ここでは「両方 cost 同点 → どちらか 1 名のみ割当 (= 距離で
    増減しない)」 を確認する.
    """
    assigner = Layer3Assigner()
    course = _course(code="A", office_id=OFFICE_A, centroid_lat=35.0, centroid_lng=140.0)
    near = _staff(
        name="near", primary_office_id=OFFICE_A, primary_office_lat=35.01, primary_office_lng=140.01
    )
    far = _staff(
        name="far", primary_office_id=OFFICE_A, primary_office_lat=36.5, primary_office_lng=141.5
    )

    result = assigner.solve([course], [near, far])

    # 1 コースなので 1 名のみ割当. どちらでも良いが office A 内であること.
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id in {near.staff_id, far.staff_id}


# ---------------------------------------------------------------------------
# 4) 後方互換 (office_id=None は制約 skip)
# ---------------------------------------------------------------------------


def test_office_none_skips_hard_constraint() -> None:
    """course.office_id=None (= 合成 fixture) は拠点制約を skip し従来どおり割当.

    staff の primary_office_id が何であれ、 office_id を持たないコースには割り
    当たる (= 後方互換).
    """
    assigner = Layer3Assigner()
    course = _course(code="A", office_id=None)
    staff_a = _staff(name="A-staff", primary_office_id=OFFICE_A)
    staff_b = _staff(name="B-staff", primary_office_id=OFFICE_B)

    result = assigner.solve([course], [staff_a, staff_b])

    # office_id None → 拠点制約 skip → どちらかが割り当たる.
    assert len(result.assignments) == 1


def test_office_none_cost_finite_for_any_office_staff() -> None:
    """office_id=None のコストはどの拠点 staff でも有限 (= 制約 skip)."""
    assigner = Layer3Assigner()
    course = _course(code="A", office_id=None)
    staff_b = _staff(name="B-staff", primary_office_id=OFFICE_B)

    cost = assigner._cost_single_cell(weekday=MONDAY, course=course, staff=staff_b, history=[])
    assert cost < HUNGARIAN_INFINITY


# ---------------------------------------------------------------------------
# 5) cost セルレベルでの拠点ハード制約
# ---------------------------------------------------------------------------


def test_cost_cell_other_office_is_infinity() -> None:
    """実効拠点 != course 拠点 のセルは HUNGARIAN_INFINITY."""
    assigner = Layer3Assigner()
    course_a = _course(code="A", office_id=OFFICE_A)
    staff_b = _staff(name="B-staff", primary_office_id=OFFICE_B)

    cost = assigner._cost_single_cell(weekday=MONDAY, course=course_a, staff=staff_b, history=[])
    assert cost >= HUNGARIAN_INFINITY


def test_cost_cell_same_office_is_finite() -> None:
    """実効拠点 == course 拠点 のセルは有限."""
    assigner = Layer3Assigner()
    course_a = _course(code="A", office_id=OFFICE_A)
    staff_a = _staff(name="A-staff", primary_office_id=OFFICE_A)

    cost = assigner._cost_single_cell(weekday=MONDAY, course=course_a, staff=staff_a, history=[])
    assert cost < HUNGARIAN_INFINITY


# ---------------------------------------------------------------------------
# 6) manager fallback (2nd pass) の拠点ハード制約
# ---------------------------------------------------------------------------


def test_manager_fallback_respects_office() -> None:
    """2nd pass manager fallback も自拠点コースにのみ配置する.

    1st pass で割当できない office A コースに対し、 office B の manager は
    fallback でも配置されない (= 他拠点漏れ防止). 通常 staff は居ないので
    office A コースは未割当のまま残る.
    """
    assigner = Layer3Assigner()
    course_a = _course(code="A", office_id=OFFICE_A)
    # office B の manager のみ (= office A コースには使えない).
    mgr_b = _staff(name="mgr-B", primary_office_id=OFFICE_B, role="manager")

    result = assigner.solve([course_a], [mgr_b])

    # office B manager は office A コースに fallback 配置できない → 未割当.
    assert result.assignments == []


def test_manager_fallback_same_office_assigned() -> None:
    """同拠点 manager は 1st pass で埋まらない NULL コースに fallback 配置される."""
    assigner = Layer3Assigner()
    course_a = _course(code="A", office_id=OFFICE_A)
    # 通常 staff 無し、 office A manager のみ → fallback で manager が入る.
    mgr_a = _staff(name="mgr-A", primary_office_id=OFFICE_A, role="manager")

    result = assigner.solve([course_a], [mgr_a])

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == mgr_a.staff_id


def test_manager_fallback_tiebreak_is_deterministic_by_staff_id() -> None:
    """同拠点 manager が複数いる場合、 staff_id 昇順の決定的タイブレークで選ぶ.

    距離は選定基準から外れているため、 座標差に依らず staff_id 最小の manager が
    選ばれる (= 距離撤去 + 決定的タイブレークの確認).
    """
    assigner = Layer3Assigner()
    course_a = _course(code="A", office_id=OFFICE_A)
    # 2 manager (同拠点). 座標は far の方が近いが、 距離は選定に効かない.
    mgr1 = _staff(
        name="mgr1",
        primary_office_id=OFFICE_A,
        role="manager",
        primary_office_lat=36.5,
        primary_office_lng=141.5,
    )
    mgr2 = _staff(
        name="mgr2",
        primary_office_id=OFFICE_A,
        role="manager",
        primary_office_lat=35.01,
        primary_office_lng=140.01,
    )
    # staff_id 昇順で最小の方を期待値にする.
    expected = min([mgr1, mgr2], key=lambda m: str(m.staff_id)).staff_id

    result = assigner.solve([course_a], [mgr1, mgr2])

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == expected
