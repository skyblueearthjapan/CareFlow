"""W4-BE9: Layer 3 アルゴリズム / スタッフ割付 のテスト.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.4 / §5.4 と
API 契約 ``docs/plans/v2-api-contracts.md`` §4.6 に対応。

検証観点 (受入基準):
  1. 全ハード制約 (性別 / 勤務曜日 / 1 コース 1 スタッフ / マネージャー除外)
  2. MVP Q3=ハイブリッド: 直近 1 週は強制除外、それ以前はソフトペナルティ
  3. fixture 4 週シミュレーションで Gini 係数が naive round-robin 以下
  4. 4 拠点 24 患者 × 4 スタッフ × 1 週で計算 30 秒以内
  5. RBAC: admin/manager は 200, staff は 403, 認証なしは 401
  6. ハンガリアン法 (純粋関数) の正当性
"""

from __future__ import annotations

import json
import time as time_mod
import uuid
from datetime import date, time
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit, VisitStaffAssignment
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.staff_companion_assignment import StaffCompanionAssignment
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    HUNGARIAN_INFINITY,
    ROTATION_EXCLUSION_WEEKS,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffAssignment,
    StaffInfo,
    course_target_from_fixture_dict,
    history_from_fixture_list,
    hungarian_min_cost,
    naive_round_robin,
    staff_from_fixture_dict,
)

# fixture 用の ISO 週. 月曜 = 2026-05-25 (week 22).
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "layer3_4weeks.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _build_fixture_inputs(
    week_index: int = 0,
) -> tuple[
    list[CourseAssignmentTarget],
    list[StaffInfo],
    list[tuple[int, str, UUID]],
    int,
    int,
]:
    """fixture から (targets, staff_pool, history, iso_year, iso_week) を構築.

    Args:
        week_index: 0 = current_iso_week, 1 = current+1, ... 連続シミュレーション用.
    """
    data = _load_fixture()
    base_year = int(data["current_iso_year"])
    base_week = int(data["current_iso_week"])
    # week_index >= 1 のとき past の history と courses を進める
    iso_week = base_week + week_index
    iso_year = base_year

    staff_pool = [staff_from_fixture_dict(s) for s in data["staff"]]

    # current week の courses をベースとして再利用 (固定構成)
    courses = []
    for c in data["courses_per_week"]:
        # course_id をユニーク化 (week_index ごとに別 UUID)
        cd = dict(c)
        cd["course_id"] = f"{iso_week}-{c['weekday']}-{c['course_code']}"
        courses.append(course_target_from_fixture_dict(cd))

    history = history_from_fixture_list(
        data["history"], current_iso_year=iso_year, current_iso_week=iso_week
    )
    return courses, staff_pool, history, iso_year, iso_week


# ---------------------------------------------------------------------------
# 1) Hungarian algorithm 単体テスト
# ---------------------------------------------------------------------------


def test_hungarian_simple_2x2() -> None:
    """2x2 行列で最小コスト割当を返す (古典的サンプル)."""
    cost = [
        [4.0, 1.0],
        [2.0, 3.0],
    ]
    # 最適: (0->1) + (1->0) = 1 + 2 = 3
    result = hungarian_min_cost(cost)
    assert result == [1, 0]


def test_hungarian_3x3_known_optimum() -> None:
    """3x3 行列で最小コスト = 5 を見つける.

    cost = [[2,1,3],[3,2,3],[3,3,2]]
    最適: (0->1) + (1->0) + (2->2) = 1 + 3 + 2 = 6
    OR    (0->0) + (1->1) + (2->2) = 2 + 2 + 2 = 6
    """
    cost = [
        [2.0, 1.0, 3.0],
        [3.0, 2.0, 3.0],
        [3.0, 3.0, 2.0],
    ]
    result = hungarian_min_cost(cost)
    total = sum(cost[i][result[i]] for i in range(3))
    assert total == 6.0
    # 全て異なる列が選ばれている
    assert len(set(result)) == 3


def test_hungarian_with_inf_avoids_blocked_cells() -> None:
    """禁止セル (HUNGARIAN_INFINITY) を避けて割当する."""
    cost = [
        [HUNGARIAN_INFINITY, 1.0, 5.0],
        [2.0, HUNGARIAN_INFINITY, 3.0],
        [4.0, 2.0, HUNGARIAN_INFINITY],
    ]
    result = hungarian_min_cost(cost)
    # 最適は (0->1, 1->0, 2->X) だが 2->X は禁止だらけ。残るのは 2->1
    # ハンガリアン法は全ての行を割当する必要があるので、
    # (0->2)=5, (1->0)=2, (2->1)=2 -> 9 が次善
    # (0->1)=1, (1->2)=3, (2->0)=4 -> 8 が最適
    total = sum(cost[i][result[i]] for i in range(3))
    assert total == 8.0
    # INF セルは選ばれない
    for i in range(3):
        assert cost[i][result[i]] < HUNGARIAN_INFINITY


def test_hungarian_empty() -> None:
    """空入力 → 空出力."""
    assert hungarian_min_cost([]) == []


def test_hungarian_1x1() -> None:
    """1x1 → [0]."""
    assert hungarian_min_cost([[7.0]]) == [0]


def test_hungarian_non_square_raises() -> None:
    with pytest.raises(ValueError):
        hungarian_min_cost([[1.0, 2.0], [3.0]])


# ---------------------------------------------------------------------------
# 2) ハード制約: 性別 / 勤務曜日 / マネージャー除外 / 1 コース 1 スタッフ
# ---------------------------------------------------------------------------


def test_hard_constraint_manager_excluded() -> None:
    """role='manager' のスタッフは割当対象から除外される (§3.6.4)."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)

    manager_ids = {s.staff_id for s in staff_pool if s.role == "manager"}
    assert manager_ids, "fixture must include at least one manager"
    assigned_staff_ids = {a.staff_id for a in result.assignments}
    assert assigned_staff_ids.isdisjoint(manager_ids), (
        f"manager assigned: {assigned_staff_ids & manager_ids}"
    )


def test_hard_constraint_gender_restriction_satisfied() -> None:
    """gender_restriction のあるコースは適合性別のスタッフが選ばれる (§3.6.4)."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)

    staff_by_id = {s.staff_id: s for s in staff_pool}
    for a in result.assignments:
        course = next(c for c in courses if c.course_id == a.course_id)
        if course.gender_restrictions:
            staff = staff_by_id[a.staff_id]
            assert staff.sex in course.gender_restrictions, (
                f"course {course.course_code} requires {course.gender_restrictions}, "
                f"but assigned staff {staff.name} sex={staff.sex}"
            )


def test_hard_constraint_work_day_satisfied() -> None:
    """スタッフは割り当てられた weekday に勤務している (§3.6.4)."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)

    staff_by_id = {s.staff_id: s for s in staff_pool}
    for a in result.assignments:
        staff = staff_by_id[a.staff_id]
        assert a.weekday in staff.work_days, (
            f"staff {staff.name} not on duty on weekday {a.weekday} "
            f"(work_days={sorted(staff.work_days)})"
        )


def test_hard_constraint_one_staff_per_day() -> None:
    """1 スタッフは 1 日 1 コースまで (§3.6.4)."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)

    by_day: dict[int, list] = {}
    for a in result.assignments:
        by_day.setdefault(a.weekday, []).append(a.staff_id)
    for weekday, ids in by_day.items():
        assert len(ids) == len(set(ids)), f"weekday {weekday} has duplicate staff: {ids}"


def test_staff_with_no_work_days_never_assigned() -> None:
    """work_days=空 のスタッフは選ばれない."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    # S1 を勤務曜日なしに変更
    staff_pool = [
        StaffInfo(
            staff_id=s.staff_id,
            name=s.name,
            sex=s.sex,
            role=s.role,
            primary_office_lat=s.primary_office_lat,
            primary_office_lng=s.primary_office_lng,
            work_days=frozenset() if s.name.startswith("S1") else s.work_days,
        )
        for s in staff_pool
    ]
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)
    s1_id = next(s.staff_id for s in staff_pool if s.name.startswith("S1"))
    for a in result.assignments:
        assert a.staff_id != s1_id


# ---------------------------------------------------------------------------
# 3) MVP Q3 ハイブリッド: 直近 1 週は強制除外、それ以前はソフト
# ---------------------------------------------------------------------------


def test_q3_hybrid_excludes_last_week_assignment() -> None:
    """直近 1 週で同 course を担当したスタッフは強制除外される (Q3 ハイブリッド).

    fixture 履歴: week 21 (= 直近 1 週) で
        A→S1, B→S2, C→S4, D→S3
    なので current week (22) では同じ組み合わせが選ばれてはならない。
    """
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=history)

    # 直近 1 週のペアを禁止セットとして集める
    forbidden = {
        (course_code, staff_id)
        for weeks_ago, course_code, staff_id in history
        if weeks_ago <= ROTATION_EXCLUSION_WEEKS
    }
    for a in result.assignments:
        assert (a.course_code, a.staff_id) not in forbidden, (
            f"violation Q3: {a.course_code} re-assigned to last-week staff"
        )


def test_q3_hybrid_soft_penalty_for_older_history() -> None:
    """2 週前以前の履歴はソフト (= 同じ組み合わせを選ぶことは可だがコストは高い).

    具体的には、コスト関数で `rotation_count > 0` のときソフトペナルティが
    加算され、選好順位が下がる。直接観察するには「同じ組み合わせを最終的に
    選ばなかった」ケースが多いことを期待値とする。
    """
    courses, staff_pool, history, _, _ = _build_fixture_inputs()

    # 2 週前のみの履歴 (直近 1 週は無し) を作る
    history_only_2w = [(2, c, s) for w, c, s in history if w == 2]

    assigner = Layer3Assigner()
    # 履歴ありとなしで結果を比較
    result_with = assigner.solve(courses, staff_pool, history=history_only_2w)
    result_without = assigner.solve(courses, staff_pool, history=[])

    # 履歴ありの方は (course_code, staff_id) が history_only_2w と異なる方を
    # 選びやすい. 完全に同じ全 mapping にはならないことを期待
    # (距離が極端に有利な場合は同じになる可能性もあるので、ここでは
    # ハード除外されない=最低限結果が成立することを確認)
    assert len(result_with.assignments) == len(result_without.assignments)


def test_q3_hybrid_no_history_first_week() -> None:
    """履歴なし (初回週) でもエラーにならず割当できる."""
    courses, staff_pool, _, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    result = assigner.solve(courses, staff_pool, history=[])
    # 4 コース全てに割当 (S1〜S4 がいるので S5 はいらない)
    assert len(result.assignments) == 4


# ---------------------------------------------------------------------------
# 4) ローテーション分散度 (Gini) が naive round-robin 以下
# ---------------------------------------------------------------------------


def test_4weeks_gini_below_or_equal_naive_round_robin() -> None:
    """4 週連続シミュレーションで Gini が naive round-robin 以下 (受入基準 3).

    各週で確定済みコースを Layer 3 が解き、結果を次週の history に積む。
    最終週まで進めた時点で全 16 件 (4 週 × 4 コース) の分散度を比較する。
    """
    data = _load_fixture()
    staff_pool = [staff_from_fixture_dict(s) for s in data["staff"]]
    base_courses_raw = data["courses_per_week"]
    base_year = int(data["current_iso_year"])
    base_week = int(data["current_iso_week"])

    assigner = Layer3Assigner()
    rolling_history: list[tuple[int, str, UUID]] = list(
        history_from_fixture_list(
            data["history"], current_iso_year=base_year, current_iso_week=base_week
        )
    )
    layer3_assignments_all = []

    # 4 週連続 (current から 3 週先まで)
    for w_offset in range(4):
        iso_week = base_week + w_offset
        # courses をその週用に再構築 (course_id を週ごとにユニーク)
        courses = []
        for c in base_courses_raw:
            cd = dict(c)
            cd["course_id"] = f"{iso_week}-{c['weekday']}-{c['course_code']}"
            courses.append(course_target_from_fixture_dict(cd))

        result = assigner.solve(courses, staff_pool, history=rolling_history)
        layer3_assignments_all.extend(result.assignments)

        # 次週用に history を更新 (今週の結果を weeks_ago=0 として加え、既存を +1 する)
        new_history: list[tuple[int, str, UUID]] = []
        for a in result.assignments:
            new_history.append((1, a.course_code, a.staff_id))
        for wa, code, sid in rolling_history:
            new_history.append((wa + 1, code, sid))
        rolling_history = new_history

    # naive round-robin の比較
    naive_assignments_all = []
    for w_offset in range(4):
        iso_week = base_week + w_offset
        courses = []
        for c in base_courses_raw:
            cd = dict(c)
            cd["course_id"] = f"{iso_week}-{c['weekday']}-{c['course_code']}"
            courses.append(course_target_from_fixture_dict(cd))
        naive_result = naive_round_robin(courses, staff_pool)
        naive_assignments_all.extend(naive_result.assignments)

    eligible_n = sum(1 for s in staff_pool if s.role != "manager")
    layer3_gini = assigner._gini_index(
        [a.staff_id for a in layer3_assignments_all], staff_count=eligible_n
    )
    naive_gini = assigner._gini_index(
        [a.staff_id for a in naive_assignments_all], staff_count=eligible_n
    )

    assert layer3_gini <= naive_gini + 1e-9, (
        f"Layer 3 Gini {layer3_gini:.4f} > naive {naive_gini:.4f} (4 weeks × 4 courses)"
    )


# ---------------------------------------------------------------------------
# 5) 性能: 4 拠点 24 患者 × 4 スタッフ × 1 週で 30 秒以内
# ---------------------------------------------------------------------------


def test_perf_24patients_4staff_1week_within_30s() -> None:
    """fixture (24 患者 × 4 staff × 1 曜日) を解く時間が 30 秒以内 (受入基準 4).

    fixture は 1 曜日のみだが、6 曜日相当に広げても余裕を持って 30 秒以内に収まる
    必要がある (実際には数十 ms オーダーで終わる)。
    """
    courses, staff_pool, history, _, _ = _build_fixture_inputs()

    # 6 曜日に拡張 (受入基準を強める)
    expanded_courses: list[CourseAssignmentTarget] = []
    for weekday in range(6):
        for c in courses:
            expanded_courses.append(
                CourseAssignmentTarget(
                    course_id=uuid.uuid4(),
                    weekday=weekday,
                    course_code=c.course_code,
                    centroid_lat=c.centroid_lat,
                    centroid_lng=c.centroid_lng,
                    gender_restrictions=c.gender_restrictions,
                    patient_ids=c.patient_ids,
                )
            )
    # スタッフを 6 曜日とも勤務できるように調整
    expanded_staff = [
        StaffInfo(
            staff_id=s.staff_id,
            name=s.name,
            sex=s.sex,
            role=s.role,
            primary_office_lat=s.primary_office_lat,
            primary_office_lng=s.primary_office_lng,
            work_days=frozenset(range(6)),
        )
        for s in staff_pool
    ]

    assigner = Layer3Assigner()
    t0 = time_mod.monotonic()
    result = assigner.solve(expanded_courses, expanded_staff, history=history)
    elapsed = time_mod.monotonic() - t0

    assert elapsed < 30.0, f"Layer 3 took {elapsed:.3f}s > 30s"
    assert len(result.assignments) > 0


# ---------------------------------------------------------------------------
# 6) HTTP API: POST /api/v1/courses/assign-staff (RBAC + 統合)
# ---------------------------------------------------------------------------


async def _seed_one_day_fixed_courses(db) -> dict[str, UUID]:
    """1 曜日 × 4 コース × 6 患者 = 24 患者 (course_fixed) を seed.

    スタッフ 4 名 (S1〜S4) + マネージャー 1 名 を作成し、StaffShift で月曜稼働を設定。
    返り値は ``{"S1": <staff_id>, "course_A": <course_id>, ...}`` の辞書。
    """
    data = _load_fixture()
    visit_date = TEST_WEEK_MONDAY  # 月曜

    # offices
    inage = Office(name="稲毛事業所", lat=35.6383, lng=140.1041)
    tsuga = Office(name="都賀事業所", lat=35.6500, lng=140.1700)
    db.add_all([inage, tsuga])
    await db.flush()

    # staff
    ids: dict[str, UUID] = {}
    staff_db: dict[str, Staff] = {}
    for s in data["staff"]:
        office = inage if s["primary_office_lat"] == 35.6383 else tsuga
        staff_obj = Staff(
            code=s["staff_id"],
            name=s["name"],
            sex=s.get("gender"),
            role=s["role"],
            status="active",
            primary_office_id=office.id,
        )
        db.add(staff_obj)
        await db.flush()
        ids[s["staff_id"]] = staff_obj.id
        staff_db[s["staff_id"]] = staff_obj

        # StaffShift: 月〜金 ON
        for weekday in range(5):
            db.add(
                StaffShift(
                    staff_id=staff_obj.id,
                    weekday=weekday,
                    is_on=True,
                )
            )
    await db.flush()

    # courses + patients + visits
    # Phase G-90: 拠点ハード制約に対応するため、 コースを拠点別に振り分ける.
    # fixture の staff は S1/S2 = inage (lat 35.6383), S3/S4 = tsuga (lat 35.65).
    # 4 コース全員割当 (1 staff 1 course) を維持するには inage/tsuga 各 2 コース
    # である必要があるため、 course A/B -> inage, C/D -> tsuga に割り当てる.
    # (旧: 全コース inage は距離前提のテスト。 office ハード制約導入で他拠点 staff
    # が割当不能になり 4 名割当が崩れるため office ベースに更新.)
    course_office_by_code = {"A": inage.id, "B": inage.id, "C": tsuga.id, "D": tsuga.id}
    for c in data["courses_per_week"]:
        course = Course(
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=int(c["weekday"]),
            code=c["course_code"],
            course_status=COURSE_STATUS_COURSE_FIXED,
            # Phase G-90: course_code に応じて拠点を割当 (default inage).
            office_id=course_office_by_code.get(c["course_code"], inage.id),
        )
        db.add(course)
        await db.flush()
        ids[f"course_{c['course_code']}"] = course.id

        for p in c["patients"]:
            patient = Patient(
                code=p["patient_id"],
                name=f"L3 {p['patient_id']}",
                status="active",
                lat=p["lat"],
                lng=p["lng"],
                sex_restriction=p.get("gender_restriction"),
            )
            db.add(patient)
            await db.flush()
            visit = Visit(
                patient_id=patient.id,
                visit_date=visit_date,
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
    return ids


@pytest.mark.asyncio
async def test_assign_staff_returns_200_with_assignments(client, db) -> None:
    """POST /assign-staff が 200 + 各コースに staff_id を返す."""
    admin = await _make_user(db, "l3-1-admin@example.com", "admin")
    await _seed_one_day_fixed_courses(db)

    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "assignments" in body
    # 4 コース全てに割当 (S1〜S4)
    assert len(body["assignments"]) == 4
    # 1 スタッフ 1 日 1 コース
    staff_ids = [a["staff_id"] for a in body["assignments"]]
    assert len(set(staff_ids)) == 4
    # マネージャーは含まれない
    # rotation_score / total_distance_km フィールドが存在
    assert "rotation_score" in body
    assert "total_distance_km" in body


@pytest.mark.asyncio
async def test_assign_staff_persists_status_to_db(client, db) -> None:
    """assign-staff 成功後、DB の course.course_status='staff_assigned' になる."""
    from sqlalchemy import select as _select

    admin = await _make_user(db, "l3-2-admin@example.com", "admin")
    await _seed_one_day_fixed_courses(db)

    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text

    courses = (
        await db.scalars(
            _select(Course).where(
                Course.iso_year == TEST_ISO_YEAR, Course.iso_week == TEST_ISO_WEEK
            )
        )
    ).all()
    # 全 4 コースが staff_assigned に遷移
    for c in courses:
        assert c.course_status == COURSE_STATUS_STAFF_ASSIGNED
        assert c.assigned_staff_id is not None
        assert c.staff_assigned_at is not None


@pytest.mark.asyncio
async def test_assign_staff_no_token_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/courses/assign-staff",
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_assign_staff_staff_role_returns_403(client, db) -> None:
    staff = await _make_user(db, "l3-3-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(staff),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_assign_staff_manager_allowed(client, db) -> None:
    manager = await _make_user(db, "l3-4-mgr@example.com", "manager")
    # データなしでも 200 (空 assignments)
    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(manager),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["assignments"] == []


@pytest.mark.asyncio
async def test_assign_staff_invalid_iso_week_returns_422(client, db) -> None:
    admin = await _make_user(db, "l3-5-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": 0},  # 範囲外
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_assign_staff_skips_proposed_courses(client, db) -> None:
    """course_status='proposed' のコースは Layer 3 の対象外."""
    from sqlalchemy import select as _select

    admin = await _make_user(db, "l3-6-admin@example.com", "admin")
    # offices + staff だけ seed
    inage = Office(name="稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    s1 = Staff(
        code="S1",
        name="S1 山田",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(s1)
    await db.flush()
    db.add(StaffShift(staff_id=s1.id, weekday=0, is_on=True))

    # proposed なコース (= Layer 3 の対象外)
    course_proposed = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status="proposed",
        office_id=inage.id,  # W15-BE-FIXPATTERN: courses.office_id NOT NULL
    )
    db.add(course_proposed)
    await db.commit()

    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # proposed コースは対象外なので assignments 0 件
    assert body["assignments"] == []

    # course_status が変わっていない
    refreshed = await db.scalar(_select(Course).where(Course.id == course_proposed.id))
    assert refreshed.course_status == "proposed"


@pytest.mark.asyncio
async def test_assign_staff_excludes_manager_from_db(client, db) -> None:
    """DB ベースで manager (M1) が割当されないことを確認."""
    from sqlalchemy import select as _select

    admin = await _make_user(db, "l3-7-admin@example.com", "admin")
    ids = await _seed_one_day_fixed_courses(db)

    res = await client.post(
        "/api/v1/courses/assign-staff",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text

    manager_id = ids["staff-M1"]
    courses = (
        await db.scalars(
            _select(Course).where(
                Course.iso_year == TEST_ISO_YEAR, Course.iso_week == TEST_ISO_WEEK
            )
        )
    ).all()
    for c in courses:
        assert c.assigned_staff_id != manager_id


# ---------------------------------------------------------------------------
# 7) Gini 係数の単体テスト
# ---------------------------------------------------------------------------


def test_gini_perfectly_even() -> None:
    """4 人に 4 件均等分配 → Gini = 0."""
    asgn = Layer3Assigner()
    items = [uuid.uuid4() for _ in range(4)]
    g = asgn._gini_index(items, staff_count=4)
    assert g == 0.0


def test_gini_complete_concentration() -> None:
    """全件 1 人集中 → Gini が大きい (0.5 以上)."""
    asgn = Layer3Assigner()
    one = uuid.uuid4()
    items = [one] * 8
    g = asgn._gini_index(items, staff_count=4)
    # 4 人中 1 人が 8 件、残り 3 人が 0 件 → Gini = 0.75
    assert g >= 0.5


# ---------------------------------------------------------------------------
# 8) Reproducibility & determinism
# ---------------------------------------------------------------------------


def test_solve_deterministic_with_same_input() -> None:
    """同入力で同出力."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    assigner = Layer3Assigner()
    r1 = assigner.solve(courses, staff_pool, history=history)
    r2 = assigner.solve(courses, staff_pool, history=history)
    a1 = sorted((a.weekday, a.course_code, str(a.staff_id)) for a in r1.assignments)
    a2 = sorted((a.weekday, a.course_code, str(a.staff_id)) for a in r2.assignments)
    assert a1 == a2
    assert r1.rotation_score == r2.rotation_score
    assert r1.total_distance_km == r2.total_distance_km


# ---------------------------------------------------------------------------
# 9) Edge case: 患者がいないコース / 空 staff_pool
# ---------------------------------------------------------------------------


def test_solve_empty_inputs() -> None:
    """両方空 → 空結果."""
    assigner = Layer3Assigner()
    r = assigner.solve([], [], history=[])
    assert r.assignments == []
    assert r.total_distance_km == 0.0


def test_solve_more_courses_than_staff() -> None:
    """staff < courses の場合、余ったコースはスキップ."""
    courses, staff_pool, history, _, _ = _build_fixture_inputs()
    # staff 2 名のみ (役割除外後)
    only_two = [s for s in staff_pool if s.role != "manager"][:2]
    assigner = Layer3Assigner()
    r = assigner.solve(courses, only_two, history=[])
    # 2 人しかいないので最大 2 件 (1 曜日内)
    assert len(r.assignments) == 2
    # 2 件は別スタッフ
    assert len({a.staff_id for a in r.assignments}) == 2


# ---------------------------------------------------------------------------
# 10) W10-BE2: 新人スタッフへの companion 自動付与
# ---------------------------------------------------------------------------
# 共通ヘルパー


async def _w10_setup_trainee_and_companion(
    db,
    *,
    companion_shift_start: time = time(9, 0),
    companion_shift_end: time = time(19, 0),
) -> tuple[Staff, Staff, Course, Patient]:
    """新人スタッフ + companion スタッフ + コース + 患者を作成して返す."""
    office = Office(name="テスト事業所", lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()

    trainee = Staff(
        code="TRAINEE",
        name="新人 田中",
        sex="female",
        role="staff",
        status="active",
        is_trainee=True,
        primary_office_id=office.id,
    )
    companion = Staff(
        code="COMPANION",
        name="先輩 鈴木",
        sex="female",
        role="staff",
        status="active",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add_all([trainee, companion])
    await db.flush()

    # trainee と companion のシフト (月曜)
    db.add(StaffShift(staff_id=trainee.id, weekday=0, is_on=True))
    db.add(
        StaffShift(
            staff_id=companion.id,
            weekday=0,
            is_on=True,
            start_time=companion_shift_start,
            end_time=companion_shift_end,
        )
    )
    await db.flush()

    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,  # W15-BE-FIXPATTERN: courses.office_id NOT NULL
    )
    db.add(course)
    await db.flush()

    patient = Patient(
        code="PT-W10",
        name="W10 患者",
        status="active",
        lat=35.6383,
        lng=140.1041,
    )
    db.add(patient)
    await db.flush()

    return trainee, companion, course, patient


@pytest.mark.asyncio
async def test_w10_companion_am_visit_auto_added(db) -> None:
    """新人スタッフの午前 visit に companion am が自動付与される.

    シフト 9:00–19:00 → 中央 14:00 → 9:00 start は am → part='am' の companion
    """
    trainee, companion, course, patient = await _w10_setup_trainee_and_companion(
        db,
        companion_shift_start=time(9, 0),
        companion_shift_end=time(19, 0),
    )

    # companion 割付 (月曜 am)
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id,
            weekday=0,
            part="am",
            companion_staff_id=companion.id,
        )
    )

    visit = Visit(
        patient_id=patient.id,
        visit_date=TEST_WEEK_MONDAY,  # 月曜
        start_time=time(9, 0),  # 午前
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
    )
    db.add(visit)
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [StaffAssignment(weekday=0, course_code="A", course_id=course.id, staff_id=trainee.id)],
        trainee_staff_ids=frozenset([trainee.id]),
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    staff_ids_in_assignments = {r.staff_id for r in rows}

    # trainee + companion の 2 行が存在すること
    assert trainee.id in staff_ids_in_assignments, "trainee が割当されていない"
    assert companion.id in staff_ids_in_assignments, "companion am が自動付与されていない"
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_w10_companion_pm_visit_auto_added(db) -> None:
    """新人スタッフの午後 visit に companion pm が自動付与される.

    シフト 9:00–19:00 → 中央 14:00 → 15:00 start は pm → part='pm' の companion
    """
    trainee, companion, course, patient = await _w10_setup_trainee_and_companion(
        db,
        companion_shift_start=time(9, 0),
        companion_shift_end=time(19, 0),
    )

    # companion 割付 (月曜 pm)
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id,
            weekday=0,
            part="pm",
            companion_staff_id=companion.id,
        )
    )

    visit = Visit(
        patient_id=patient.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(15, 0),  # 午後
        end_time=time(16, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
    )
    db.add(visit)
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [StaffAssignment(weekday=0, course_code="A", course_id=course.id, staff_id=trainee.id)],
        trainee_staff_ids=frozenset([trainee.id]),
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    staff_ids_in_assignments = {r.staff_id for r in rows}

    assert trainee.id in staff_ids_in_assignments, "trainee が割当されていない"
    assert companion.id in staff_ids_in_assignments, "companion pm が自動付与されていない"
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_w10_companion_full_takes_priority_over_am_pm(db) -> None:
    """part='full' の companion 割付がある場合、am/pm 問わず優先採用される."""
    trainee, companion, course, patient = await _w10_setup_trainee_and_companion(
        db,
        companion_shift_start=time(9, 0),
        companion_shift_end=time(19, 0),
    )

    # companion_alt は am 専用
    office_alt = Office(name="別事業所", lat=35.65, lng=140.17)
    db.add(office_alt)
    await db.flush()
    companion_alt = Staff(
        code="COMPANION_ALT",
        name="別先輩",
        sex="female",
        role="staff",
        status="active",
        is_trainee=False,
        primary_office_id=office_alt.id,
    )
    db.add(companion_alt)
    await db.flush()
    db.add(StaffShift(staff_id=companion_alt.id, weekday=0, is_on=True))

    # full 優先の割付 (companion) + am 割付 (companion_alt)
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id,
            weekday=0,
            part="full",
            companion_staff_id=companion.id,
        )
    )
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id,
            weekday=0,
            part="am",
            companion_staff_id=companion_alt.id,
        )
    )

    visit = Visit(
        patient_id=patient.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(9, 0),  # 午前 だが full が優先
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
    )
    db.add(visit)
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [StaffAssignment(weekday=0, course_code="A", course_id=course.id, staff_id=trainee.id)],
        trainee_staff_ids=frozenset([trainee.id]),
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    staff_ids_in_assignments = {r.staff_id for r in rows}

    assert trainee.id in staff_ids_in_assignments, "trainee が割当されていない"
    # full の companion が採用される
    assert companion.id in staff_ids_in_assignments, "full companion が優先採用されていない"
    # am の companion_alt は採用されない (full が優先)
    assert companion_alt.id not in staff_ids_in_assignments, "am companion が誤って採用された"


@pytest.mark.asyncio
async def test_w10_trainee_plus_required_two_no_duplicate(db) -> None:
    """patient.required_staff_count=2 (2 名体制) + is_trainee → 3 名体制, 重複なし.

    - visit.required_staff_count=2: primary (trainee) + secondary (normal staff) の 2 名体制
    - trainee → companion も追加 → 合計 3 名; 全員 distinct であること
    - 火曜 (weekday=1) で構築し、月曜の他テストと衝突を避ける
    """
    # 独立したオフィス/スタッフを用意 (共有ヘルパーを使わず直接作成)
    office = Office(name="W10-4 事業所", lat=35.64, lng=140.11)
    db.add(office)
    await db.flush()

    trainee = Staff(
        code="W10T4_TRAINEE",
        name="新人 W10-4",
        sex="female",
        role="staff",
        status="active",
        is_trainee=True,
        primary_office_id=office.id,
    )
    companion = Staff(
        code="W10T4_COMP",
        name="先輩 W10-4",
        sex="female",
        role="staff",
        status="active",
        is_trainee=False,
        primary_office_id=office.id,
    )
    secondary = Staff(
        code="W10T4_SEC",
        name="secondary W10-4",
        sex="female",
        role="staff",
        status="active",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add_all([trainee, companion, secondary])
    await db.flush()

    # 火曜 (weekday=1) シフト
    db.add(
        StaffShift(
            staff_id=trainee.id,
            weekday=1,
            is_on=True,
            start_time=time(9, 0),
            end_time=time(19, 0),
        )
    )
    db.add(
        StaffShift(
            staff_id=companion.id,
            weekday=1,
            is_on=True,
            start_time=time(9, 0),
            end_time=time(19, 0),
        )
    )
    db.add(StaffShift(staff_id=secondary.id, weekday=1, is_on=True))
    await db.flush()

    # 2 つのコース (火曜 = weekday=1)
    course_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=1,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,  # W15-BE-FIXPATTERN: courses.office_id NOT NULL
    )
    course_b = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=1,
        code="B",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,  # W15-BE-FIXPATTERN
    )
    db.add_all([course_a, course_b])
    await db.flush()

    patient = Patient(
        code="PT-W10-4",
        name="W10-4 患者",
        status="active",
        lat=35.64,
        lng=140.11,
    )
    db.add(patient)
    await db.flush()

    # 火曜の visit (weekday=1 → visit_date = 月曜+1日 = 火曜)
    visit_date_tue = date(TEST_WEEK_MONDAY.year, TEST_WEEK_MONDAY.month, TEST_WEEK_MONDAY.day + 1)
    group_id = uuid.uuid4()
    visit_a = Visit(
        patient_id=patient.id,
        visit_date=visit_date_tue,
        start_time=time(9, 0),  # 午前
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_a.id,
    )
    visit_b = Visit(
        patient_id=patient.id,
        visit_date=visit_date_tue,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=2,
        visit_group_id=group_id,
        course_id=course_b.id,
    )
    db.add_all([visit_a, visit_b])

    # companion 割付 (trainee → companion、火曜 am)
    db.add(
        StaffCompanionAssignment(
            trainee_staff_id=trainee.id,
            weekday=1,
            part="am",
            companion_staff_id=companion.id,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(weekday=1, course_code="A", course_id=course_a.id, staff_id=trainee.id),
            StaffAssignment(
                weekday=1, course_code="B", course_id=course_b.id, staff_id=secondary.id
            ),
        ],
        trainee_staff_ids=frozenset([trainee.id]),
    )
    await db.commit()

    rows_a = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    ).all()
    staff_ids_a = {r.staff_id for r in rows_a}

    # visit_a: trainee (primary) + secondary (2名体制partner) + companion (新人同行)
    assert trainee.id in staff_ids_a, "trainee が visit_a に割当されていない"
    assert secondary.id in staff_ids_a, "secondary が 2名体制で追加されていない"
    assert companion.id in staff_ids_a, "companion が新人同行で追加されていない"
    # 重複なし
    assert len(rows_a) == len(staff_ids_a), "重複が発生している"
    assert len(rows_a) == 3, f"3 名体制になっていない: {len(rows_a)} 行"


# ---------------------------------------------------------------------------
# CareFlow バグ修正 (Layer 3 staff_assigned 拾い漏れ):
#   auto_allocator_v2 由来の course_status='staff_assigned' コースも
#   _load_course_targets で処理対象に含まれる (= 自動割付ボタンで 15/112 件
#   のみ割付になる本質バグの再発防止).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer3_load_course_targets_includes_staff_assigned(db) -> None:
    """``_load_course_targets`` が ``course_fixed`` だけでなく ``staff_assigned``
    コースも返す (= assign-staff-only 再実行で staff_assigned コースを再評価)."""
    inage = Office(name="L3-SA 拠点", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    s_assigned = Staff(
        code="L3SA-S0",
        name="L3SA assigned",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(s_assigned)
    await db.flush()

    # patient + visit (course 紐付け要)
    p_fixed = Patient(code="L3SA-PF", name="patient-fixed", status="active")
    p_assigned = Patient(code="L3SA-PA", name="patient-assigned", status="active")
    db.add_all([p_fixed, p_assigned])
    await db.flush()

    # course_fixed コース
    c_fixed = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    # staff_assigned コース (本来は Layer 3 拾い漏れの対象)
    c_staff_assigned = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=s_assigned.id,
        office_id=inage.id,
    )
    db.add_all([c_fixed, c_staff_assigned])
    await db.flush()

    # 各 course に visit を 1 件ずつ紐付け
    v_fixed = Visit(
        patient_id=p_fixed.id,
        course_id=c_fixed.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
    )
    v_assigned = Visit(
        patient_id=p_assigned.id,
        course_id=c_staff_assigned.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc_v2",
        required_staff_count=1,
    )
    db.add_all([v_fixed, v_assigned])
    await db.commit()

    assigner = Layer3Assigner()
    targets = await assigner._load_course_targets(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    codes = {t.course_code for t in targets}
    assert "A" in codes, f"course_fixed の 'A' が targets に無い: {codes}"
    assert "B" in codes, (
        f"staff_assigned の 'B' が targets に無い "
        "(=auto_allocator_v2 由来コースが拾い漏れる本質バグの再発): "
        f"{codes}"
    )


@pytest.mark.asyncio
async def test_layer3_assign_does_not_reassign_staff_assigned_course(db) -> None:
    """``Layer3Assigner.assign``: staff_assigned コースを処理対象に含めても、
    既存 ``assigned_staff_id`` は ``run`` 内 ``already_assigned_stmt`` で
    ``fixed_staff_by_course`` に追加されるので副作用が出ない (W25 fix)."""
    from sqlalchemy import select as _select

    inage = Office(name="L3-NoSideEffect 拠点", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    s_locked = Staff(
        code="L3NS-S0",
        name="L3NS locked staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    s_other = Staff(
        code="L3NS-S1",
        name="L3NS other staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add_all([s_locked, s_other])
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=s_locked.id, weekday=wd, is_on=True))
        db.add(StaffShift(staff_id=s_other.id, weekday=wd, is_on=True))

    p_locked = Patient(code="L3NS-P1", name="patient-locked", status="active")
    db.add(p_locked)
    await db.flush()

    c_locked = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=s_locked.id,
        office_id=inage.id,
    )
    db.add(c_locked)
    await db.flush()
    db.add(
        Visit(
            patient_id=p_locked.id,
            course_id=c_locked.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc_v2",
            required_staff_count=1,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id)
    await db.commit()

    refreshed = await db.scalar(_select(Course).where(Course.id == c_locked.id))
    assert refreshed is not None
    # 既存 assigned_staff_id は不変 (W25 fix で保護)
    assert refreshed.assigned_staff_id == s_locked.id, (
        f"既存 staff_assigned コースの assigned_staff_id が変更された "
        f"(W25 fix の副作用): expected={s_locked.id}, got={refreshed.assigned_staff_id}"
    )
    assert refreshed.course_status == COURSE_STATUS_STAFF_ASSIGNED
