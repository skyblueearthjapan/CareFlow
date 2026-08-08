"""Tests for swap (2 患者入れ替え) 提案 + apply-swap (P3-②).

engine (find_improvement_candidates) の swap 生成:
    - 双方向 feasible + delta 閾値超 → 成立
    - 片方向のみ feasible → 不成立
    - Y が pinned / locked → 不成立
    - 曜日跨ぎで Y が unknown → 不成立 (day_flexible 必須)
    - 同曜日で双方 unknown → 成立 + 両方 要確認
    - 却下指紋 kind='swap' 除外

endpoint (POST /v2/improvement-suggestions/apply-swap):
    - 成立 (両患者の PFV が入れ替わる)
    - pinned は 422 + 全 rollback (原子性)
    - 新位置同士の相互衝突は warnings に載る (ブロックしない)

ローカル SQLite のみ (本番 DB 禁止). 座標は test_improvement_engine.py と整合.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.improvement_engine import find_improvement_candidates

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)

_APPLY_URL = "/api/v1/schedule/v2/improvement-suggestions/apply-swap"


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


async def _seed_office(db, *, code: str = "INAGE", name: str = "稲") -> Office:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    return office


async def _seed_staff(db, *, office: Office, name: str) -> Staff:
    staff = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return staff


async def _seed_shift(db, *, staff: Staff, weekday: int) -> None:
    db.add(StaffShift(staff_id=staff.id, weekday=weekday, is_on=True))
    await db.flush()


async def _seed_patient(db, *, office: Office, code: str, lat: float, lng: float) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(db, *, office: Office, staff: Staff, weekday: int, code: str) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(
    db, *, patient: Patient, course: Course, weekday: int, start: time, end: time
) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=date.fromordinal(WEEK_MONDAY.toordinal() + weekday),
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(visit)
    await db.flush()
    return visit


async def _seed_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start: time,
    duration_min: int = 30,
    movability: str = "time_flexible",
    is_pinned: bool = False,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        slot_index=0,
        start_time=start,
        duration_min=duration_min,
        movability=movability,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _swap_scenario(
    db,
    *,
    x_movability: str = "time_flexible",
    y_movability: str = "time_flexible",
    y_is_pinned: bool = False,
    y_weekday: int = 0,
):
    """成立形状: 同一 office に course A (FAR 群) と course B (BASE 群).

    X(BASE) は course A で FAR に挟まれ marginal 高.
    Y(FAR) は course B で BASE に挟まれ marginal 高.
    → 入れ替えると双方 ~0 (同住所グループへ収まる) で combined delta 大.

    ``y_weekday`` を変えると曜日跨ぎスワップ (X=月, Y=別曜日) を作れる.
    """
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    s2 = await _seed_staff(db, office=office, name="S2")
    await _seed_shift(db, staff=s1, weekday=0)
    await _seed_shift(db, staff=s2, weekday=y_weekday)

    x = await _seed_patient(db, office=office, code="X", lat=BASE[0], lng=BASE[1])
    y = await _seed_patient(db, office=office, code="Y", lat=FAR[0], lng=FAR[1])

    # Course A (Mon): FAR — X(BASE) — FAR.
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")
    fa1 = await _seed_patient(db, office=office, code="FA1", lat=FAR[0], lng=FAR[1])
    fa2 = await _seed_patient(db, office=office, code="FA2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=fa1, course=ca, weekday=0, start=time(9, 30), end=time(10, 0))
    await _seed_visit(db, patient=x, course=ca, weekday=0, start=time(10, 30), end=time(11, 0))
    await _seed_visit(db, patient=fa2, course=ca, weekday=0, start=time(11, 15), end=time(11, 45))

    # Course B (y_weekday): BASE — Y(FAR) — BASE.
    cb = await _seed_course(db, office=office, staff=s2, weekday=y_weekday, code="B")
    bb1 = await _seed_patient(db, office=office, code="BB1", lat=BASE[0], lng=BASE[1])
    bb2 = await _seed_patient(db, office=office, code="BB2", lat=BASE[0], lng=BASE[1])
    await _seed_visit(
        db, patient=bb1, course=cb, weekday=y_weekday, start=time(9, 30), end=time(10, 0)
    )
    await _seed_visit(
        db, patient=y, course=cb, weekday=y_weekday, start=time(10, 30), end=time(11, 0)
    )
    await _seed_visit(
        db, patient=bb2, course=cb, weekday=y_weekday, start=time(11, 15), end=time(11, 45)
    )

    await _seed_pfv(db, patient=x, weekday=0, start=time(10, 30), movability=x_movability)
    await _seed_pfv(
        db,
        patient=y,
        weekday=y_weekday,
        start=time(10, 30),
        movability=y_movability,
        is_pinned=y_is_pinned,
    )
    await db.commit()
    return office, x, y


# ---------------------------------------------------------------------------
# engine: swap 生成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swap_feasible_both_directions(db) -> None:
    _office, x, y = await _swap_scenario(db)
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    swaps = [s for s in suggestions if s.kind == "swap"]
    assert swaps, suggestions
    s = swaps[0]
    assert s.swap_counterpart_patient_id == y.id
    assert s.delta_minutes >= 10
    # time_flexible 同士 → 要確認は付かない.
    assert not s.requires_patient_confirmation
    assert not s.swap_counterpart_requires_confirmation
    # target_weekday は X の現在枠 (Monday=0).
    assert s.target_weekday == 0


@pytest.mark.asyncio
async def test_swap_rejected_when_one_direction_infeasible(db) -> None:
    """Y が X の位置に置けない (異住所隣人と移動時間衝突) → 不成立."""
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    s2 = await _seed_staff(db, office=office, name="S2")
    await _seed_shift(db, staff=s1, weekday=0)
    await _seed_shift(db, staff=s2, weekday=0)

    x = await _seed_patient(db, office=office, code="X", lat=BASE[0], lng=BASE[1])
    y = await _seed_patient(db, office=office, code="Y", lat=FAR[0], lng=FAR[1])

    # Course A: NEAR 隣人(異住所)が X 直前に密着 → Y(FAR) が X の枠に来ると移動衝突.
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")
    n1 = await _seed_patient(db, office=office, code="N1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=n1, course=ca, weekday=0, start=time(10, 15), end=time(10, 45))
    await _seed_visit(db, patient=x, course=ca, weekday=0, start=time(10, 50), end=time(11, 20))

    # Course B: Y 単独 → X(BASE) は容易に置ける (片方向のみ feasible).
    cb = await _seed_course(db, office=office, staff=s2, weekday=0, code="B")
    await _seed_visit(db, patient=y, course=cb, weekday=0, start=time(10, 30), end=time(11, 0))

    # N1 には PFV を与えない (swap 候補にしない). X / Y は time_flexible.
    await _seed_pfv(db, patient=x, weekday=0, start=time(10, 50))
    await _seed_pfv(db, patient=y, weekday=0, start=time(10, 30))
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_rejected_when_counterpart_pinned(db) -> None:
    _office, x, _y = await _swap_scenario(db, y_is_pinned=True, y_movability="locked")
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_rejected_when_counterpart_locked(db) -> None:
    _office, x, _y = await _swap_scenario(db, y_movability="locked")
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_cross_weekday_requires_day_flexible_on_both(db) -> None:
    """X=day_flexible でも Y=unknown なら曜日跨ぎスワップは不成立."""
    _office, x, _y = await _swap_scenario(
        db, x_movability="day_flexible", y_movability="unknown", y_weekday=2
    )
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_cross_weekday_ok_when_both_day_flexible(db) -> None:
    """双方 day_flexible なら曜日跨ぎスワップは成立する."""
    _office, x, y = await _swap_scenario(
        db, x_movability="day_flexible", y_movability="day_flexible", y_weekday=2
    )
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    swaps = [s for s in suggestions if s.kind == "swap"]
    assert swaps, suggestions
    assert swaps[0].swap_counterpart_patient_id == y.id
    assert swaps[0].cand_weekday == 2  # X は Y の曜日 (水) へ.


@pytest.mark.asyncio
async def test_swap_same_weekday_both_unknown_requires_confirmation(db) -> None:
    _office, x, _y = await _swap_scenario(db, x_movability="unknown", y_movability="unknown")
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    swaps = [s for s in suggestions if s.kind == "swap"]
    assert swaps, suggestions
    assert swaps[0].requires_patient_confirmation  # X 側 要確認.
    assert swaps[0].swap_counterpart_requires_confirmation  # Y 側 要確認.


@pytest.mark.asyncio
async def test_swap_dismissed_fingerprint_excluded(db) -> None:
    _office, x, _y = await _swap_scenario(db)
    db.add(SuggestionDismissal(patient_id=x.id, kind="swap", target_weekday=0, reason="other"))
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_y_dismissed_fingerprint_excluded(db) -> None:
    """Y 側の却下指紋 (y_pid, 'swap', wy) で該当スワップが除外される (テスト空隙 a)."""
    _office, x, y = await _swap_scenario(db)
    # Y の現在曜日 (wy=0) に対して Y 側で swap 却下.
    db.add(SuggestionDismissal(patient_id=y.id, kind="swap", target_weekday=0, reason="other"))
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    # Y が却下しているので X 視点でも swap は出ない.
    assert all(s.kind != "swap" for s in suggestions), suggestions


@pytest.mark.asyncio
async def test_swap_skipped_when_x_already_has_pfv_on_target_weekday(db) -> None:
    """X が月(0)と水(2)に PFV を持つとき、水の Y との月→水スワップは提案されない (MEDIUM-1)."""
    _office, x, _y = await _swap_scenario(
        db, x_movability="day_flexible", y_movability="day_flexible", y_weekday=2
    )
    # X に水曜(2)の追加 PFV を追加する (既存 月曜 PFV はシナリオ内で作成済み).
    await _seed_pfv(db, patient=x, weekday=2, start=time(14, 0), movability="day_flexible")
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    # X は水曜に既に PFV を持つため月→水 swap は候補に上がらない.
    assert all(s.kind != "swap" for s in suggestions), suggestions


# ---------------------------------------------------------------------------
# engine: swap × 希望訪問スケジュール (weekly_pattern) — P4-A 層 2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swap_within_preference_one_side_only_that_side_confirmed(db) -> None:
    """同曜日・双方 unknown で X だけ希望内 → X は確認不要、Y だけ要確認 (層 2 片側適用)."""
    _office, x, _y = await _swap_scenario(db, x_movability="unknown", y_movability="unknown")
    # X の希望に月(0) 終日を登録 → X の新位置 (月, 10:30) が希望内になる.
    x.weekly_pattern = {"entries": [{"weekday": 0, "time_type": None}]}
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    swaps = [s for s in suggestions if s.kind == "swap"]
    assert swaps, suggestions
    s = swaps[0]
    assert s.within_preference is True
    assert not s.requires_patient_confirmation  # X 希望内 → 確認不要
    assert s.swap_counterpart_within_preference is False
    assert s.swap_counterpart_requires_confirmation  # Y 希望外 unknown → 要確認


@pytest.mark.asyncio
async def test_swap_cross_weekday_within_preference_authorizes_unknown(db) -> None:
    """X=unknown でも X の新位置が希望内なら Y=day_flexible との曜日跨ぎスワップが成立 (層 2)."""
    _office, x, y = await _swap_scenario(
        db, x_movability="unknown", y_movability="day_flexible", y_weekday=2
    )
    # X の希望に水(2) 終日を登録 → X の新位置 (水, 10:30) が希望内 → unknown でも曜日跨ぎ許可.
    x.weekly_pattern = {"entries": [{"weekday": 2, "time_type": None}]}
    await db.commit()

    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    swaps = [s for s in suggestions if s.kind == "swap"]
    assert swaps, suggestions
    assert swaps[0].cand_weekday == 2
    assert swaps[0].within_preference is True
    assert not swaps[0].requires_patient_confirmation
    assert swaps[0].swap_counterpart_patient_id == y.id


@pytest.mark.asyncio
async def test_swap_cross_weekday_unknown_x_rejected_without_preference(db) -> None:
    """対照: X=unknown・希望登録なし → 曜日跨ぎスワップは不成立 (層 3 のまま)."""
    _office, x, _y = await _swap_scenario(
        db, x_movability="unknown", y_movability="day_flexible", y_weekday=2
    )
    suggestions, _summary = await find_improvement_candidates(
        db, patient=x, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "swap" for s in suggestions), suggestions


# ---------------------------------------------------------------------------
# endpoint: apply-swap
# ---------------------------------------------------------------------------


async def _make_admin(db, *, email: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _apply_pfv_pair(
    db,
    *,
    a_weekday: int,
    a_start: time,
    b_weekday: int,
    b_start: time,
    a_pinned: bool = False,
):
    """A / B 患者と 1 枚ずつの PFV を用意して返す (apply-swap 用の最小構成)."""
    office = await _seed_office(db)
    a = await _seed_patient(db, office=office, code="AA", lat=BASE[0], lng=BASE[1])
    b = await _seed_patient(db, office=office, code="BB", lat=FAR[0], lng=FAR[1])
    await _seed_pfv(
        db,
        patient=a,
        weekday=a_weekday,
        start=a_start,
        movability="day_flexible",
        is_pinned=a_pinned,
    )
    await _seed_pfv(db, patient=b, weekday=b_weekday, start=b_start, movability="day_flexible")
    await db.commit()
    return a, b


@pytest.mark.asyncio
async def test_apply_swap_moves_both_patients(client, db) -> None:
    """cross-weekday swap: A(月) と B(水) の固定枠が入れ替わる."""
    admin = await _make_admin(db, email="sw-a1@example.com")
    a, b = await _apply_pfv_pair(
        db, a_weekday=0, a_start=time(10, 30), b_weekday=2, b_start=time(9, 0)
    )

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    a_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == a.id))
    ).all()
    b_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == b.id))
    ).all()
    assert len(a_rows) == 1 and a_rows[0].weekday == 2 and a_rows[0].start_time == time(9, 0)
    assert len(b_rows) == 1 and b_rows[0].weekday == 0 and b_rows[0].start_time == time(10, 30)
    # movability は保持.
    assert a_rows[0].movability == "day_flexible"
    assert b_rows[0].movability == "day_flexible"


@pytest.mark.asyncio
async def test_apply_swap_pinned_is_422_and_rolls_back(client, db) -> None:
    """片方が pinned なら 422 で、両患者の PFV は一切変わらない (全 rollback)."""
    admin = await _make_admin(db, email="sw-a2@example.com")
    a, b = await _apply_pfv_pair(
        db,
        a_weekday=0,
        a_start=time(10, 30),
        b_weekday=2,
        b_start=time(9, 0),
        a_pinned=True,
    )

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 422, res.text

    a_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == a.id))
    ).all()
    b_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == b.id))
    ).all()
    # 変化なし.
    assert a_rows[0].weekday == 0 and a_rows[0].start_time == time(10, 30)
    assert b_rows[0].weekday == 2 and b_rows[0].start_time == time(9, 0)


@pytest.mark.asyncio
async def test_apply_swap_mutual_conflict_returns_warning(client, db) -> None:
    """同曜日・同コース (None) で新位置同士が近接 → applied=True + warnings 非空."""
    admin = await _make_admin(db, email="sw-a3@example.com")
    # A(BASE)@09:00, B(FAR)@09:40 (同曜日). 入れ替えると A→09:40, B→09:00 で移動衝突.
    a, b = await _apply_pfv_pair(
        db, a_weekday=0, a_start=time(9, 0), b_weekday=0, b_start=time(9, 40)
    )

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 0, "start_time": "09:40"},
            "b_new": {"weekday": 0, "start_time": "09:00"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    assert body["warnings"], "新位置衝突の警告が載るはず"


@pytest.mark.asyncio
async def test_apply_swap_same_weekday_success(client, db) -> None:
    """同曜日スワップ: A(月09:00) と B(月10:30) の時刻が入れ替わる (テスト空隙 b).

    movability / is_pinned は変化しない.
    """
    admin = await _make_admin(db, email="sw-same-wd@example.com")
    a, b = await _apply_pfv_pair(
        db, a_weekday=0, a_start=time(9, 0), b_weekday=0, b_start=time(10, 30)
    )

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 0, "start_time": "10:30"},
            "b_new": {"weekday": 0, "start_time": "09:00"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    a_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == a.id))
    ).all()
    b_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == b.id))
    ).all()
    assert len(a_rows) == 1 and a_rows[0].weekday == 0 and a_rows[0].start_time == time(10, 30)
    assert len(b_rows) == 1 and b_rows[0].weekday == 0 and b_rows[0].start_time == time(9, 0)
    # movability / is_pinned は保持.
    assert a_rows[0].movability == "day_flexible"
    assert b_rows[0].movability == "day_flexible"
    assert a_rows[0].is_pinned is False
    assert b_rows[0].is_pinned is False


@pytest.mark.asyncio
async def test_apply_swap_same_patient_id_is_422(client, db) -> None:
    """patient_a_id == patient_b_id は 422 早期ガード (MEDIUM-2)."""
    admin = await _make_admin(db, email="sw-selfswap@example.com")
    a, _b = await _apply_pfv_pair(
        db, a_weekday=0, a_start=time(10, 30), b_weekday=2, b_start=time(9, 0)
    )

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(a.id),  # 同一患者
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# _apply_pfv_move: course_template_id 保持 (MAJOR)
# ---------------------------------------------------------------------------


async def _seed_course_template(db, *, office: Office, label: str = "TA") -> CourseTemplate:
    ct = CourseTemplate(office_id=office.id, label=label)
    db.add(ct)
    await db.flush()
    return ct


@pytest.mark.asyncio
async def test_apply_swap_omitted_b_course_retains_existing(client, db) -> None:
    """b_new.course_template_id 省略時 → Y の course_template_id が保持される (MAJOR fix).

    FE は counterpart 側の移動先コースを解決できず省略してくる. 省略を
    「変更なし」と扱い、既存の course_template_id を null 化しないことを確認.
    """
    admin = await _make_admin(db, email="sw-course-a@example.com")
    office = await _seed_office(db, code="CT01", name="CT拠点")
    ct = await _seed_course_template(db, office=office)

    # A: 月, B: 水. B には course_template_id を設定しておく.
    a = await _seed_patient(db, office=office, code="CTA", lat=BASE[0], lng=BASE[1])
    b = await _seed_patient(db, office=office, code="CTB", lat=FAR[0], lng=FAR[1])
    pfv_a = PatientFixedVisit(
        patient_id=a.id,
        mode="normal",
        weekday=0,
        slot_index=0,
        start_time=time(10, 30),
        duration_min=30,
        movability="day_flexible",
        is_pinned=False,
    )
    pfv_b = PatientFixedVisit(
        patient_id=b.id,
        mode="normal",
        weekday=2,
        slot_index=0,
        start_time=time(9, 0),
        duration_min=30,
        movability="day_flexible",
        is_pinned=False,
        course_template_id=ct.id,  # B は既存コースを持つ
    )
    db.add_all([pfv_a, pfv_b])
    await db.commit()

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            # b_new.course_template_id を省略 (FE 契約 — counterpart 解決不能)
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    # populate_existing=True: session が expire_on_commit=False のため、identity map の
    # stale コピーを強制的に DB から再ロードする (endpoint の別セッション commit を反映).
    # B は月(0)・10:30 に移動しており、course_template_id は保持されているはず.
    b_rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == b.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(b_rows) == 1
    assert b_rows[0].weekday == 0
    assert b_rows[0].start_time == time(10, 30)
    assert b_rows[0].course_template_id == ct.id, (
        "省略された b_new.course_template_id は null 化せず保持されるべき"
    )


@pytest.mark.asyncio
async def test_apply_swap_explicit_b_course_overwrites(client, db) -> None:
    """b_new.course_template_id を明示指定すると、Y の course_template_id が上書きされる."""
    admin = await _make_admin(db, email="sw-course-b@example.com")
    office = await _seed_office(db, code="CT02", name="CT拠点2")
    ct_old = await _seed_course_template(db, office=office, label="TA")
    ct_new = await _seed_course_template(db, office=office, label="TB")

    a = await _seed_patient(db, office=office, code="CTA2", lat=BASE[0], lng=BASE[1])
    b = await _seed_patient(db, office=office, code="CTB2", lat=FAR[0], lng=FAR[1])
    pfv_a = PatientFixedVisit(
        patient_id=a.id,
        mode="normal",
        weekday=0,
        slot_index=0,
        start_time=time(10, 30),
        duration_min=30,
        movability="day_flexible",
        is_pinned=False,
    )
    pfv_b = PatientFixedVisit(
        patient_id=b.id,
        mode="normal",
        weekday=2,
        slot_index=0,
        start_time=time(9, 0),
        duration_min=30,
        movability="day_flexible",
        is_pinned=False,
        course_template_id=ct_old.id,
    )
    db.add_all([pfv_a, pfv_b])
    await db.commit()

    res = await client.post(
        _APPLY_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            # b_new に新しいコースを明示指定 → 上書きされるはず
            "b_new": {"weekday": 0, "start_time": "10:30", "course_template_id": str(ct_new.id)},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    # populate_existing=True: session が expire_on_commit=False のため、identity map の
    # stale コピーを強制的に DB から再ロードする (endpoint の別セッション commit を反映).
    b_rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == b.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(b_rows) == 1
    assert b_rows[0].course_template_id == ct_new.id, (
        "明示指定した course_template_id は上書きされるべき"
    )


@pytest.mark.asyncio
async def test_apply_swap_staff_forbidden(client, db) -> None:
    staff_user = User(email="sw-staff@example.com", password_hash=hash_password("x"), role="staff")
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)
    a, b = await _apply_pfv_pair(
        db, a_weekday=0, a_start=time(10, 30), b_weekday=2, b_start=time(9, 0)
    )
    res = await client.post(
        _APPLY_URL,
        headers=_bearer(staff_user),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 403, res.text
