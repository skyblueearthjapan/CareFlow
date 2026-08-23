"""「担当なし」への投入提案 — POST /api/v1/schedule/v2/assign-candidates (Phase 2-A)

正典: docs/plans/unassigned-suggestions-design.md §2 (契約)。

検証観点:
  1. course_id 指定 = その日のそのコースの planned 訪問すべてが対象
  2. visit_ids 指定 = 指定訪問のみが対象 (存在しない id は 404 / この日の planned で
     ないものは 422)
  3. course_id / course_ids / visit_ids は 1 つだけ (複数指定 / 全部なしは 422)
  4. whole_ok_staff_ids = 全対象訪問 (= 全 group) で ◎ の交差 — 片方だけ ◎ の人は入らない。
     対象訪問どうしがぶつかる (1 人では回れない) 場合も空になる
  4b. whole_ok_by_course = コースごとの同じ交差 (course_ids で 1 回にまとめても正しい)
  5. 対象訪問の現担当は候補から除外される (担当なし以外からも呼べる)
  6. 担当なしの訪問 (primary_staff_id NULL かつ course.assigned_staff_id NULL) を拾える
  7. read-only (INSERT / UPDATE / DELETE を 1 本も出さない) / RBAC: staff は 403
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import event, func, select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.course import Course
from app.models.office import Office
from app.models.staff import StaffShift
from app.services.scheduling.substitute_candidates import build_assign_candidates

_URL = "/api/v1/schedule/v2/assign-candidates"

# 2026-09-04 = 金曜 = ISO 2026-W36 weekday4 (test_substitute_candidates.py と同じ日)
_ISO_YEAR, _ISO_WEEK, _WEEKDAY = 2026, 36, 4
_DATE = date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, _WEEKDAY + 1)

_LAT_A, _LNG_A = 35.6300, 140.1000
_LAT_B, _LNG_B = 35.7000, 140.2000  # 別住所 (同住所免除が効かない距離)


# ---------------------------------------------------------------------------
# fixtures (test_substitute_candidates.py の作法に倣う)
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_staff(
    db,
    *,
    name: str,
    office: Office | None = None,
    sex: str | None = None,
    is_trainee: bool = False,
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5),
) -> Staff:
    s = Staff(
        name=name,
        sex=sex,
        status="active",
        role="staff",
        is_trainee=is_trainee,
        primary_office_id=office.id if office is not None else None,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    for wd in work_days:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.commit()
    return s


async def _make_patient(
    db,
    *,
    code: str,
    lat: float | None = _LAT_A,
    lng: float | None = _LNG_A,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        special_week_active=[],
        lat=lat,
        lng=lng,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(
    db,
    *,
    office: Office,
    code: str = "A",
    assigned_staff: Staff | None = None,
) -> Course:
    c = Course(
        iso_year=_ISO_YEAR,
        iso_week=_ISO_WEEK,
        weekday=_WEEKDAY,
        code=code,
        course_status="staff_assigned" if assigned_staff is not None else "course_fixed",
        office_id=office.id,
        assigned_staff_id=assigned_staff.id if assigned_staff is not None else None,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    *,
    patient: Patient,
    course: Course | None = None,
    staff: Staff | None = None,
    start: time = time(10, 0),
    end: time = time(11, 0),
    status_value: str = "planned",
    visit_date: date | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        course_id=course.id if course is not None else None,
        primary_staff_id=staff.id if staff is not None else None,
        visit_date=visit_date or _DATE,
        start_time=start,
        end_time=end,
        type="regular",
        status=status_value,
        source="auto",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _candidate(body: dict, staff: Staff, *, group_index: int = 0) -> dict:
    group = body["groups"][group_index]
    return next(c for c in group["candidates"] if c["staff_id"] == str(staff.id))


def _staff_ids(body: dict, *, group_index: int = 0) -> set[str]:
    return {c["staff_id"] for c in body["groups"][group_index]["candidates"]}


async def _post(client, user: User, **payload):
    body: dict = {"date": _DATE.isoformat(), **payload}
    return await client.post(_URL, headers=_bearer(user), json=body)


# ---------------------------------------------------------------------------
# 1. 対象集合の作り方 (course_id / visit_ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_id_targets_all_planned_visits_of_that_course(client, db) -> None:
    """course_id = その日のそのコースの planned 訪問すべて (cancelled / 他コースは対象外)."""
    admin = await _make_user(db, email="asg-1@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG1")
    await _make_staff(db, name="空いてる人", office=office)
    course = await _make_course(db, office=office, code="A")
    other_course = await _make_course(db, office=office, code="B")

    p1 = await _make_patient(db, code="ASG-1a")
    p2 = await _make_patient(db, code="ASG-1b")
    p_cancelled = await _make_patient(db, code="ASG-1c")
    p_other = await _make_patient(db, code="ASG-1d")

    v1 = await _make_visit(db, patient=p1, course=course)
    v2 = await _make_visit(db, patient=p2, course=course, start=time(13, 0), end=time(14, 0))
    await _make_visit(
        db,
        patient=p_cancelled,
        course=course,
        start=time(15, 0),
        end=time(16, 0),
        status_value="cancelled",
    )
    await _make_visit(db, patient=p_other, course=other_course)

    res = await _post(client, admin, course_id=str(course.id))
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["absent_staff"] is None
    assert body["date"] == _DATE.isoformat()
    assert body["weekday"] == _WEEKDAY
    assert len(body["groups"]) == 1
    assert body["groups"][0]["course_id"] == str(course.id)
    assert [v["visit_id"] for v in body["groups"][0]["visits"]] == [str(v1.id), str(v2.id)]


@pytest.mark.asyncio
async def test_visit_ids_target_only_the_given_visits(client, db) -> None:
    admin = await _make_user(db, email="asg-2@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG2")
    await _make_staff(db, name="空いてる人", office=office)
    course = await _make_course(db, office=office, code="A")

    p1 = await _make_patient(db, code="ASG-2a")
    p2 = await _make_patient(db, code="ASG-2b")
    v1 = await _make_visit(db, patient=p1, course=course)
    await _make_visit(db, patient=p2, course=course, start=time(13, 0), end=time(14, 0))

    res = await _post(client, admin, visit_ids=[str(v1.id)])
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["groups"]) == 1
    assert [v["visit_id"] for v in body["groups"][0]["visits"]] == [str(v1.id)]


@pytest.mark.asyncio
async def test_unassigned_visit_is_picked_up(client, db) -> None:
    """担当なし (primary_staff_id NULL かつ course.assigned_staff_id NULL) を拾える."""
    admin = await _make_user(db, email="asg-3@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG3")
    free = await _make_staff(db, name="空いてる人", office=office)
    course = await _make_course(db, office=office, code="A", assigned_staff=None)
    patient = await _make_patient(db, code="ASG-3")
    visit = await _make_visit(db, patient=patient, course=course, staff=None)

    res = await _post(client, admin, course_id=str(course.id))
    assert res.status_code == 200, res.text
    body = res.json()

    assert [v["visit_id"] for v in body["groups"][0]["visits"]] == [str(visit.id)]
    cand = _candidate(body, free)
    assert cand["status"] == "ok", cand
    assert cand["load_today"] == 0
    assert body["whole_ok_staff_ids"] == [str(free.id)]
    assert body["whole_ok_by_course"] == {str(course.id): [str(free.id)]}


# ---------------------------------------------------------------------------
# 2. 入力検証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_course_id_and_visit_ids_is_422(client, db) -> None:
    admin = await _make_user(db, email="asg-4@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG4")
    course = await _make_course(db, office=office, code="A")
    patient = await _make_patient(db, code="ASG-4")
    visit = await _make_visit(db, patient=patient, course=course)

    res = await _post(client, admin, course_id=str(course.id), visit_ids=[str(visit.id)])
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_neither_course_id_nor_visit_ids_is_422(client, db) -> None:
    admin = await _make_user(db, email="asg-5@example.com", role="admin")
    res = await _post(client, admin)
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_unknown_visit_id_is_404(client, db) -> None:
    admin = await _make_user(db, email="asg-6@example.com", role="admin")
    res = await _post(client, admin, visit_ids=["00000000-0000-0000-0000-0000000000ff"])
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_non_planned_visit_id_is_422(client, db) -> None:
    """生きてはいるが「この日の planned」でない訪問は黙って捨てず 422."""
    admin = await _make_user(db, email="asg-7@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG7")
    course = await _make_course(db, office=office, code="A")
    patient = await _make_patient(db, code="ASG-7")
    done = await _make_visit(db, patient=patient, course=course, status_value="completed")

    res = await _post(client, admin, visit_ids=[str(done.id)])
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_unknown_course_id_is_404(client, db) -> None:
    admin = await _make_user(db, email="asg-8@example.com", role="admin")
    res = await _post(client, admin, course_id="00000000-0000-0000-0000-0000000000ff")
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 3. whole_ok_staff_ids (全対象訪問で ◎ の交差)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whole_ok_excludes_staff_ok_on_only_one_visit(client, db) -> None:
    """2 訪問 (2 束) のうち片方でしか ◎ でない人は whole_ok に入らない."""
    admin = await _make_user(db, email="asg-9@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG9")
    partial = await _make_staff(db, name="午後ふさがりの人", office=office)
    free = await _make_staff(db, name="空いてる人", office=office)

    course_a = await _make_course(db, office=office, code="A")
    course_b = await _make_course(db, office=office, code="B")
    p_a = await _make_patient(db, code="ASG-9a")
    p_b = await _make_patient(db, code="ASG-9b")
    v_a = await _make_visit(db, patient=p_a, course=course_a, start=time(10, 0), end=time(11, 0))
    v_b = await _make_visit(db, patient=p_b, course=course_b, start=time(13, 0), end=time(14, 0))

    # partial の当日既存訪問 (別住所) が v_b と重なる → group B では △
    busy_course = await _make_course(db, office=office, code="D", assigned_staff=partial)
    p_busy = await _make_patient(db, code="ASG-9z", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(
        db,
        patient=p_busy,
        course=busy_course,
        staff=partial,
        start=time(13, 30),
        end=time(14, 30),
    )

    res = await _post(client, admin, visit_ids=[str(v_a.id), str(v_b.id)])
    assert res.status_code == 200, res.text
    body = res.json()

    assert [g["course_id"] for g in body["groups"]] == [str(course_a.id), str(course_b.id)]
    assert _candidate(body, partial, group_index=0)["status"] == "ok"
    cand_b = _candidate(body, partial, group_index=1)
    assert cand_b["status"] == "warn"
    assert {r["code"] for r in cand_b["reasons"]} == {"time_overlap"}
    assert _candidate(body, free, group_index=0)["status"] == "ok"
    assert _candidate(body, free, group_index=1)["status"] == "ok"

    assert body["whole_ok_staff_ids"] == [str(free.id)]


@pytest.mark.asyncio
async def test_substitute_candidates_also_returns_whole_ok(client, db) -> None:
    """既存 substitute-candidates にも whole_ok_staff_ids が載る (FE 交差判定の BE 寄せ)."""
    admin = await _make_user(db, email="asg-10@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG10")
    absent = await _make_staff(db, name="休む人", office=office)
    free = await _make_staff(db, name="代わりの人", office=office)
    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="ASG-10")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    res = await client.post(
        "/api/v1/schedule/v2/substitute-candidates",
        headers=_bearer(admin),
        json={"staff_id": str(absent.id), "date": _DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["absent_staff"]["id"] == str(absent.id)
    assert body["whole_ok_staff_ids"] == [str(free.id)]


# ---------------------------------------------------------------------------
# 4. 現担当の除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_owner_is_excluded_from_candidates(client, db) -> None:
    """担当なし以外からも呼べる — 対象訪問の現担当は候補に出さない."""
    admin = await _make_user(db, email="asg-11@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG11")
    owner = await _make_staff(db, name="いまの担当", office=office)
    other = await _make_staff(db, name="別の人", office=office)
    # コース担当のみ (primary_staff_id は NULL) = 盤面の owner フォールバック
    course = await _make_course(db, office=office, code="A", assigned_staff=owner)
    patient = await _make_patient(db, code="ASG-11")
    await _make_visit(db, patient=patient, course=course, staff=None)

    res = await _post(client, admin, course_id=str(course.id))
    assert res.status_code == 200, res.text
    body = res.json()

    ids = _staff_ids(body)
    assert str(owner.id) not in ids
    assert str(other.id) in ids
    assert body["whole_ok_staff_ids"] == [str(other.id)]


# ---------------------------------------------------------------------------
# 5. read-only / RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_issues_no_write_statements(db) -> None:
    """read-only の証明: 実行中に INSERT / UPDATE / DELETE を 1 本も出さない.

    HTTP 経由だと監査ログ middleware が audit_logs へ 1 行書くため、サービス層を
    直接叩いて DBAPI レベルで観測する (test_substitute_candidates.py と同方針)。
    """
    from app.db import session as db_session

    office = await _make_office(db, name="稲毛ASG12")
    free = await _make_staff(db, name="空いてる人", office=office)
    course = await _make_course(db, office=office, code="A")
    patient = await _make_patient(db, code="ASG-12")
    await _make_visit(db, patient=patient, course=course, staff=None)
    visits_before = await db.scalar(select(func.count()).select_from(Visit))

    captured: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append(statement)

    engine = db_session.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        res = await build_assign_candidates(db, target_date=_DATE, course_id=course.id)
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert _candidate(res.model_dump(mode="json"), free)["status"] == "ok"
    # listener が本当に効いていたことの保証 (captured が空なら「書込 0」は無意味)
    assert captured, "before_cursor_execute を 1 本も観測できていない"
    writes = [s for s in captured if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == [], writes
    db.expire_all()
    assert await db.scalar(select(func.count()).select_from(Visit)) == visits_before


@pytest.mark.asyncio
async def test_staff_role_is_forbidden(client, db) -> None:
    office = await _make_office(db, name="稲毛ASG13")
    member = await _make_staff(db, name="一般スタッフ", office=office)
    staff_user = await _make_user(db, email="asg-13@example.com", role="staff", staff_id=member.id)
    course = await _make_course(db, office=office, code="A")

    res = await _post(client, staff_user, course_id=str(course.id))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 6. course_ids — 複数コースを 1 回の呼び出しで (M-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_ids_returns_every_course_in_one_call(client, db) -> None:
    """course_ids = 複数コースを 1 回で評価し、whole_ok_by_course がコースごとに正しい."""
    admin = await _make_user(db, email="asg-14@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG14")
    partial = await _make_staff(db, name="午後ふさがりの人", office=office)
    free = await _make_staff(db, name="空いてる人", office=office)

    course_a = await _make_course(db, office=office, code="A")
    course_b = await _make_course(db, office=office, code="B")
    p_a = await _make_patient(db, code="ASG-14a")
    p_b = await _make_patient(db, code="ASG-14b")
    # 束どうしはぶつからない (10:00-11:00 / 14:00-15:00)
    await _make_visit(db, patient=p_a, course=course_a, start=time(10, 0), end=time(11, 0))
    await _make_visit(db, patient=p_b, course=course_b, start=time(14, 0), end=time(15, 0))

    # partial は 14:30 に別住所の既存訪問 → course_b でだけ △
    busy_course = await _make_course(db, office=office, code="D", assigned_staff=partial)
    p_busy = await _make_patient(db, code="ASG-14z", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(
        db,
        patient=p_busy,
        course=busy_course,
        staff=partial,
        start=time(14, 30),
        end=time(15, 30),
    )

    res = await _post(client, admin, course_ids=[str(course_a.id), str(course_b.id)])
    assert res.status_code == 200, res.text
    body = res.json()

    assert [g["course_id"] for g in body["groups"]] == [str(course_a.id), str(course_b.id)]
    assert _candidate(body, partial, group_index=0)["status"] == "ok"
    assert _candidate(body, partial, group_index=1)["status"] == "warn"

    by_course = body["whole_ok_by_course"]
    assert set(by_course) == {str(course_a.id), str(course_b.id)}
    assert set(by_course[str(course_a.id)]) == {str(partial.id), str(free.id)}
    assert by_course[str(course_b.id)] == [str(free.id)]
    # 全対象訪問の交差 (両コースを 1 人で) は free だけ
    assert body["whole_ok_staff_ids"] == [str(free.id)]


@pytest.mark.asyncio
async def test_course_ids_runs_heavy_preprocessing_once(db) -> None:
    """コースを 1 → 2 に増やしても発行 SQL 本数は変わらない (前処理はコース数に比例しない)."""
    from app.db import session as db_session

    office = await _make_office(db, name="稲毛ASG15")
    await _make_staff(db, name="空いてる人", office=office)
    course_a = await _make_course(db, office=office, code="A")
    course_b = await _make_course(db, office=office, code="B")
    p_a = await _make_patient(db, code="ASG-15a")
    p_b = await _make_patient(db, code="ASG-15b")
    await _make_visit(db, patient=p_a, course=course_a, start=time(10, 0), end=time(11, 0))
    await _make_visit(db, patient=p_b, course=course_b, start=time(14, 0), end=time(15, 0))

    async def _count(course_ids: list) -> int:
        captured: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            captured.append(statement)

        engine = db_session.get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", _listener)
        try:
            await build_assign_candidates(db, target_date=_DATE, course_ids=course_ids)
        finally:
            event.remove(engine, "before_cursor_execute", _listener)
        assert captured
        return len(captured)

    one = await _count([course_a.id])
    two = await _count([course_a.id, course_b.id])
    assert one == two, (one, two)


@pytest.mark.asyncio
async def test_course_id_and_course_ids_together_is_422(client, db) -> None:
    admin = await _make_user(db, email="asg-16@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG16")
    course = await _make_course(db, office=office, code="A")

    res = await _post(client, admin, course_id=str(course.id), course_ids=[str(course.id)])
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_course_ids_over_the_cap_is_422(client, db) -> None:
    admin = await _make_user(db, email="asg-17@example.com", role="admin")
    too_many = [f"00000000-0000-0000-0000-{i:012d}" for i in range(51)]
    res = await _post(client, admin, course_ids=too_many)
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 7. 対象訪問どうしの衝突 (M-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutually_conflicting_targets_empty_whole_ok_but_keep_per_course(client, db) -> None:
    """束をまたいで時間がぶつかるなら「全部まとめて 1 人」は不可 (whole_ok 空).

    ただしコース単独なら引き受けられるので whole_ok_by_course には残る。
    """
    admin = await _make_user(db, email="asg-18@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG18")
    free1 = await _make_staff(db, name="空いてる人1", office=office)
    free2 = await _make_staff(db, name="空いてる人2", office=office)

    course_a = await _make_course(db, office=office, code="A")
    course_b = await _make_course(db, office=office, code="B")
    # 別住所 × 時間が重なる → 1 人では両方回れない
    p_a = await _make_patient(db, code="ASG-18a", lat=_LAT_A, lng=_LNG_A)
    p_b = await _make_patient(db, code="ASG-18b", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(db, patient=p_a, course=course_a, start=time(10, 0), end=time(11, 0))
    await _make_visit(db, patient=p_b, course=course_b, start=time(10, 30), end=time(11, 30))

    res = await _post(client, admin, course_ids=[str(course_a.id), str(course_b.id)])
    assert res.status_code == 200, res.text
    body = res.json()

    # 各コース単独では両名とも ◎ のまま
    both = {str(free1.id), str(free2.id)}
    assert _candidate(body, free1, group_index=0)["status"] == "ok"
    assert _candidate(body, free2, group_index=1)["status"] == "ok"
    by_course = body["whole_ok_by_course"]
    assert set(by_course[str(course_a.id)]) == both
    assert set(by_course[str(course_b.id)]) == both

    # まとめて 1 人では回れない
    assert body["whole_ok_staff_ids"] == []


@pytest.mark.asyncio
async def test_conflicting_visits_inside_one_course_empty_that_course(client, db) -> None:
    """コース内の訪問どうしがぶつかるなら、そのコースの whole_ok_by_course は空."""
    admin = await _make_user(db, email="asg-19@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG19")
    free = await _make_staff(db, name="空いてる人", office=office)

    course = await _make_course(db, office=office, code="A")
    p1 = await _make_patient(db, code="ASG-19a", lat=_LAT_A, lng=_LNG_A)
    p2 = await _make_patient(db, code="ASG-19b", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(db, patient=p1, course=course, start=time(10, 0), end=time(11, 0))
    await _make_visit(db, patient=p2, course=course, start=time(10, 30), end=time(11, 30))

    res = await _post(client, admin, course_id=str(course.id))
    assert res.status_code == 200, res.text
    body = res.json()

    assert _candidate(body, free)["status"] == "ok"
    assert body["whole_ok_by_course"] == {str(course.id): []}
    assert body["whole_ok_staff_ids"] == []


@pytest.mark.asyncio
async def test_same_address_pair_is_not_a_conflict(client, db) -> None:
    """同住所ペアは免除 — 同じ時間帯でも「1 人で回れる」扱い (既存規則と同じ)."""
    admin = await _make_user(db, email="asg-20@example.com", role="admin")
    office = await _make_office(db, name="稲毛ASG20")
    free = await _make_staff(db, name="空いてる人", office=office)

    course = await _make_course(db, office=office, code="A")
    p1 = await _make_patient(db, code="ASG-20a", lat=_LAT_A, lng=_LNG_A)
    p2 = await _make_patient(db, code="ASG-20b", lat=_LAT_A, lng=_LNG_A)
    await _make_visit(db, patient=p1, course=course, start=time(10, 0), end=time(11, 0))
    await _make_visit(db, patient=p2, course=course, start=time(10, 0), end=time(11, 0))

    res = await _post(client, admin, course_id=str(course.id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["whole_ok_by_course"] == {str(course.id): [str(free.id)]}
    assert body["whole_ok_staff_ids"] == [str(free.id)]
