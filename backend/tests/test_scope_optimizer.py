"""Tests for scope_optimizer (範囲最適化 W1 BE-1).

貪欲反復の収束・閾値・D-1 (pinned/locked 除外)・D-2 (確認不要のみ)・D-3 (却下記憶)・
scope 制約 (範囲外へ動かさない)・決定性・state_token・前後メトリクスの整合
(delta 累積 = travel+buffer の減少量) を検証する。

ローカル SQLite のみ (本番 DB 禁止)。

座標メモ (千葉市近辺, test_improvement_engine.py と整合):
    BASE = (35.6000, 140.1000)
    FAR  = (35.6300, 140.1400)  → BASE から ~4.9km
    SAME = (35.60005, 140.10005) → BASE と同住所 (<=100m)
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.improvement_engine import ImprovementCandidateData
from app.services.scheduling.scope_optimizer import (
    SCOPE_STEP_THRESHOLD_MIN,
    OptimizationScope,
    _apply_swap,
    _copy_bucket,
    _CourseBucket,
    _ScopeCandidate,
    _SimPfv,
    _SimState,
    compute_state_token,
    simulate_scope_optimization,
)

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)
SAME = (35.60005, 140.10005)

CONFIG = DEFAULT_SCHEDULING_CONFIG


# ---------------------------------------------------------------------------
# fixtures (test_improvement_engine.py と同型の seed 群)
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


async def _seed_patient(db, *, office: Office, code: str, lat: float, lng: float, **kw) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        **kw,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office: Office, staff: Staff | None, weekday: int, code: str
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
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
    movability: str = "unknown",
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


async def _seed_shift(db, *, staff: Staff, weekday: int) -> None:
    db.add(StaffShift(staff_id=staff.id, weekday=weekday, is_on=True))
    await db.flush()


async def _sandwich_scenario(
    db,
    *,
    target_movability: str,
    is_pinned: bool = False,
    alt_weekday: int | None = None,
):
    """P(BASE) を Monday course A で FAR — P — FAR に挟む (限界コスト高).

    course A 内の末尾へ動かすだけで delta = 1 辺分 (~23 分) 浮く時刻変更が存在する。
    ``alt_weekday`` を指定すると、その曜日に SAME 隣人つき course B (低 marginal 枠)
    も用意する (曜日跨ぎ候補)。
    """
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    await _seed_shift(db, staff=s1, weekday=0)

    p = await _seed_patient(db, office=office, code="TGT", lat=BASE[0], lng=BASE[1])
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")
    fa1 = await _seed_patient(db, office=office, code="FA1", lat=FAR[0], lng=FAR[1])
    fa2 = await _seed_patient(db, office=office, code="FA2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=fa1, course=ca, weekday=0, start=time(9, 30), end=time(10, 0))
    await _seed_visit(db, patient=p, course=ca, weekday=0, start=time(10, 30), end=time(11, 0))
    await _seed_visit(db, patient=fa2, course=ca, weekday=0, start=time(11, 15), end=time(11, 45))
    await _seed_pfv(
        db,
        patient=p,
        weekday=0,
        start=time(10, 30),
        movability=target_movability,
        is_pinned=is_pinned,
    )

    if alt_weekday is not None:
        s2 = await _seed_staff(db, office=office, name="S2")
        await _seed_shift(db, staff=s2, weekday=alt_weekday)
        cb = await _seed_course(db, office=office, staff=s2, weekday=alt_weekday, code="B")
        pb1 = await _seed_patient(db, office=office, code="PB1", lat=SAME[0], lng=SAME[1])
        await _seed_visit(
            db, patient=pb1, course=cb, weekday=alt_weekday, start=time(9, 30), end=time(10, 0)
        )

    await db.commit()
    return office, p


def _scope(office, *, weekdays=None, course_codes=None) -> OptimizationScope:
    return OptimizationScope(
        office_id=office.id,
        weekdays=frozenset(weekdays) if weekdays is not None else None,
        course_codes=frozenset(course_codes) if course_codes is not None else None,
    )


# ---------------------------------------------------------------------------
# 収束・効果整合
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_course_move_converges_and_metrics_match(db) -> None:
    """①コース内スコープ: 挟まれた P を動かす time_change が積まれ、収束する.

    整合の核: steps の最終累積 delta (分) は before/after の
    (travel_minutes + buffer_minutes) の減少量と厳密一致する (限界コスト =
    コース合計の増減そのものなので)。
    """
    office, p = await _sandwich_scenario(db, target_movability="time_flexible")
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    assert result.steps, result.excluded
    first = result.steps[0]
    assert first.patient_id == p.id
    assert first.candidate.kind == "time_change"
    assert first.candidate.delta_minutes >= SCOPE_STEP_THRESHOLD_MIN
    # seq は 1 始まり連番.
    assert [s.seq for s in result.steps] == list(range(1, len(result.steps) + 1))
    # 累積 delta = travel+buffer の減少量 (厳密一致).
    reduction = (result.before.travel_minutes + result.before.buffer_minutes) - (
        result.after.travel_minutes + result.after.buffer_minutes
    )
    assert reduction == result.steps[-1].cumulative_delta_minutes
    assert result.after.travel_minutes < result.before.travel_minutes
    # visit 総数は不変 (move は移動であって増減しない).
    assert result.after.visit_count == result.before.visit_count
    assert result.excluded.truncated is False


@pytest.mark.asyncio
async def test_empty_scope_returns_zero_steps(db) -> None:
    office = await _seed_office(db)
    await db.commit()
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office),
        config=CONFIG,
    )
    assert result.steps == []
    assert result.before.visit_count == 0
    assert result.state_token == compute_state_token([])


# ---------------------------------------------------------------------------
# D-1: pinned / locked 除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_excluded(db) -> None:
    office, _p = await _sandwich_scenario(db, target_movability="locked", is_pinned=True)
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    assert result.steps == []
    assert result.excluded.pinned == 1
    assert result.excluded.locked == 0
    # 前後は不変.
    assert result.before == result.after


@pytest.mark.asyncio
async def test_locked_excluded(db) -> None:
    office, _p = await _sandwich_scenario(db, target_movability="locked")
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    assert result.steps == []
    assert result.excluded.locked == 1
    assert result.excluded.pinned == 0


# ---------------------------------------------------------------------------
# D-2: 確認不要の手のみ (unknown の希望外は生成しない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_without_preference_excluded(db) -> None:
    """unknown + 希望未登録: 改善枠 (delta>=閾値) があっても要確認のため積まない."""
    office, _p = await _sandwich_scenario(db, target_movability="unknown")
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    assert result.steps == []
    assert result.excluded.confirmation_required_excluded == 1


@pytest.mark.asyncio
async def test_unknown_within_preference_allowed_cross_weekday(db) -> None:
    """unknown でも希望内 (層 2) の曜日跨ぎは確認不要で積まれる (P4-A と同じ規則)."""
    office, p = await _sandwich_scenario(db, target_movability="unknown", alt_weekday=2)
    p.weekly_pattern = {"entries": [{"weekday": 2, "time_type": None}]}
    await db.commit()

    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office),
        config=CONFIG,
    )
    day2 = [s for s in result.steps if s.candidate.cand_weekday == 2]
    assert day2, (result.steps, result.excluded)
    assert all(s.candidate.within_preference for s in day2)
    assert all(not s.candidate.requires_patient_confirmation for s in day2)


# ---------------------------------------------------------------------------
# D-3: 却下記憶
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismissed_fingerprint_respected(db) -> None:
    office, p = await _sandwich_scenario(db, target_movability="time_flexible")
    db.add(
        SuggestionDismissal(
            patient_id=p.id,
            kind="time_change",
            target_weekday=0,
            reason="other",
        )
    )
    await db.commit()

    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    assert result.steps == []
    assert result.excluded.dismissed == 1


# ---------------------------------------------------------------------------
# scope 制約 (範囲外へ動かさない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_never_moves_outside_selected_weekdays(db) -> None:
    """wd2 に圧倒的な改善枠 (同住所隣人) があっても scope=[月] なら wd2 へ動かさない."""
    office, _p = await _sandwich_scenario(db, target_movability="day_flexible", alt_weekday=2)
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0]),
        config=CONFIG,
    )
    assert all(s.candidate.cand_weekday == 0 for s in result.steps)

    # 対照: 全曜日スコープなら wd2 への day_change が最良手として積まれる.
    result_all = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office),
        config=CONFIG,
    )
    assert any(
        s.candidate.kind == "day_change" and s.candidate.cand_weekday == 2 for s in result_all.steps
    ), (result_all.steps, result_all.excluded)


# ---------------------------------------------------------------------------
# 決定性・state_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulate_is_deterministic(db) -> None:
    office, _p = await _sandwich_scenario(db, target_movability="day_flexible", alt_weekday=2)
    r1 = await simulate_scope_optimization(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, scope=_scope(office), config=CONFIG
    )
    r2 = await simulate_scope_optimization(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, scope=_scope(office), config=CONFIG
    )
    assert r1.state_token == r2.state_token
    assert [
        (s.patient_id, s.candidate.kind, s.candidate.cand_weekday, s.candidate.cand_start)
        for s in r1.steps
    ] == [
        (s.patient_id, s.candidate.kind, s.candidate.cand_weekday, s.candidate.cand_start)
        for s in r2.steps
    ]


@pytest.mark.asyncio
async def test_state_token_changes_when_pfv_changes(db) -> None:
    office, p = await _sandwich_scenario(db, target_movability="time_flexible")
    r1 = await simulate_scope_optimization(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, scope=_scope(office), config=CONFIG
    )
    # PFV の時刻を動かす → 指紋が変わる (apply の 409 判定根拠).
    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    pfv.start_time = time(9, 0)
    await db.commit()
    r2 = await simulate_scope_optimization(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, scope=_scope(office), config=CONFIG
    )
    assert r1.state_token != r2.state_token


# ---------------------------------------------------------------------------
# no_current_visit 会計 (PFV 対応が取れない visit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visits_without_pfv_counted_not_moved(db) -> None:
    """FA1/FA2 (PFV 無し) は動かさず no_current_visit で会計する."""
    office, p = await _sandwich_scenario(db, target_movability="time_flexible")
    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    # FA1 / FA2 の 2 件 (TGT は PFV あり).
    assert result.excluded.no_current_visit == 2
    moved = {s.patient_id for s in result.steps}
    assert moved <= {p.id}


# ---------------------------------------------------------------------------
# _apply_swap (純関数レベル: 模擬状態の相互更新)
# ---------------------------------------------------------------------------


def _v2visit(pid, name, wd, start_h, start_m, dur, lat, lng, office_id, code):
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    start = time(start_h, start_m)
    end_total = start_h * 60 + start_m + dur
    return V2Visit(
        patient_id=pid,
        patient_name=name,
        patient_code=None,
        weekday=wd,
        start_time=start,
        end_time=time(end_total // 60, end_total % 60),
        service_minutes=dur,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am",
        source_kind="fixed",
        course_code=code,
    )


def test_copy_bucket_preserves_ng_patient_ids() -> None:
    """シミュレーション用の複製でも NG スタッフ集合を引き継ぐ (§6 の警告判定に必要)."""
    office_id = uuid4()
    ng_pid = uuid4()
    src = _CourseBucket(
        office_id=office_id,
        weekday=0,
        course_code="A",
        assigned_staff_id=uuid4(),
        ng_patient_ids=frozenset({ng_pid}),
    )
    assert _copy_bucket(src).ng_patient_ids == frozenset({ng_pid})


def test_apply_swap_updates_sim_state() -> None:
    """swap 適用で 2 visit の位置・2 PFV の (weekday, start) が相互に入れ替わる."""
    office_id = uuid4()
    x_pid, y_pid = uuid4(), uuid4()
    bucket_a = _CourseBucket(
        office_id=office_id,
        weekday=0,
        course_code="A",
        visits=[_v2visit(x_pid, "X", 0, 10, 0, 30, *BASE, office_id, "A")],
    )
    bucket_b = _CourseBucket(
        office_id=office_id,
        weekday=2,
        course_code="B",
        visits=[_v2visit(y_pid, "Y", 2, 14, 0, 30, *FAR, office_id, "B")],
    )
    sim = _SimState(
        buckets={(office_id, 0, "A"): bucket_a, (office_id, 2, "B"): bucket_b},
        pfv_by_pw={
            (x_pid, 0): _SimPfv(x_pid, 0, time(10, 0), 30, False, "day_flexible"),
            (y_pid, 2): _SimPfv(y_pid, 2, time(14, 0), 30, False, "day_flexible"),
        },
        occupied_weekdays={x_pid: {0}, y_pid: {2}},
    )
    cand = ImprovementCandidateData(
        kind="swap",
        target_weekday=0,
        current_office_id=office_id,
        current_weekday=0,
        current_start=time(10, 0),
        current_end=time(10, 30),
        current_course_label="A",
        current_staff_name=None,
        cand_office_id=office_id,
        cand_office_name=None,
        cand_weekday=2,
        cand_start=time(14, 0),
        cand_end=time(14, 30),
        cand_course_code="B",
        cand_course_label="B",
        cand_staff_name=None,
        delta_minutes=20,
        delta_km=1.0,
        staff_warnings=[],
        requires_patient_confirmation=False,
        changes=[],
        unchanged=[],
        swap_counterpart_patient_id=y_pid,
        swap_counterpart_name="Y",
        swap_counterpart_current_weekday=2,
        swap_counterpart_current_start=time(14, 0),
        swap_counterpart_new_weekday=0,
        swap_counterpart_new_start=time(10, 0),
    )
    ok = _apply_swap(sim, _ScopeCandidate(patient_id=x_pid, patient_name="X", data=cand))
    assert ok
    # X が bucket_b (wd2 14:00) へ、Y が bucket_a (wd0 10:00) へ.
    assert [v.patient_id for v in bucket_b.visits] == [x_pid]
    assert bucket_b.visits[0].start_time == time(14, 0)
    assert [v.patient_id for v in bucket_a.visits] == [y_pid]
    assert bucket_a.visits[0].start_time == time(10, 0)
    # PFV 模擬コピーと占有曜日も入れ替わる.
    assert (x_pid, 2) in sim.pfv_by_pw and (y_pid, 0) in sim.pfv_by_pw
    assert sim.occupied_weekdays[x_pid] == {2}
    assert sim.occupied_weekdays[y_pid] == {0}


# ---------------------------------------------------------------------------
# W3 回帰: 同住所・同時刻ペアの見かけ倒し提案 (実データ由来のバグ修正)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_address_same_time_pair_yields_no_phantom_step(db) -> None:
    """同住所・同時刻ペアの片割れの時刻ずらしは効果ゼロ → 提案しない.

    実データ再現: 遠方の患者 (FAR) の後に、同住所ペア (BASE×2) が同時刻 15:30 に
    入っているコース。旧・近似式は「ペアの片割れを 16:05 へずらすと FAR→BASE の
    移動が浮く」と誤計上した (実際は相方が同住所に残るため 1 分も浮かない)。
    厳密計算 (コース合計の差) では delta=0 になり、手順は生成されない。
    """
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    await _seed_shift(db, staff=s1, weekday=0)
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")

    far = await _seed_patient(db, office=office, code="FARP", lat=FAR[0], lng=FAR[1])
    pair1 = await _seed_patient(db, office=office, code="PA1", lat=BASE[0], lng=BASE[1])
    pair2 = await _seed_patient(db, office=office, code="PA2", lat=SAME[0], lng=SAME[1])

    await _seed_visit(db, patient=far, course=ca, weekday=0, start=time(14, 0), end=time(14, 35))
    # 同住所ペア: 同時刻 15:30 開始 (本番データと同型).
    await _seed_visit(db, patient=pair1, course=ca, weekday=0, start=time(15, 30), end=time(16, 5))
    await _seed_visit(db, patient=pair2, course=ca, weekday=0, start=time(15, 30), end=time(16, 5))
    # 動かせるのはペアの片割れ (pair2) のみ.
    await _seed_pfv(
        db,
        patient=pair2,
        weekday=0,
        start=time(15, 30),
        duration_min=35,
        movability="time_flexible",
    )
    await db.commit()

    result = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=_scope(office, weekdays=[0], course_codes=["A"]),
        config=CONFIG,
    )
    # 同一コース内で時刻をずらしても相方が同住所に残る → 効果ゼロ → 手順なし.
    assert result.steps == [], [
        (s.candidate.kind, s.candidate.delta_minutes, s.candidate.cand_start) for s in result.steps
    ]
    # 前後メトリクスも不変.
    assert result.before == result.after


# ---------------------------------------------------------------------------
# §10 フォーカス最適化: 問題範囲 (focus) と探索範囲 (search) の分離
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focus_search_separation_moves_out_of_focus(db) -> None:
    """フォーカス=コースA・探索=拠点全体のとき、A の患者を B の好位置へ移せる.

    - 従来 (探索=フォーカス) では B へ動けない (コース内の並べ替えのみ)。
    - 分離後は移動先がフォーカス外でも可。ただし動かす対象はフォーカス内のみ
      (B に movable な患者がいても動かさない)。
    - フォーカスの負担が実際に減り、探索範囲全体も悪化しない。
    """
    office, p = await _sandwich_scenario(db, target_movability="time_flexible", alt_weekday=0)
    # course B 側にも movable な PFV を与える (フォーカス外なので動かないことの検証用).
    pb1 = (await db.scalars(select(Patient).where(Patient.code == "PB1"))).one()
    await _seed_pfv(db, patient=pb1, weekday=0, start=time(9, 30), movability="time_flexible")
    await db.commit()

    focus = _scope(office, weekdays=[0], course_codes=["A"])

    # 従来挙動 (search 省略 = フォーカスと同じ): B へは動けない.
    r_same = await simulate_scope_optimization(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, scope=focus, config=CONFIG
    )
    assert all(s.candidate.cand_course_code == "A" for s in r_same.steps)

    # §10: 探索=拠点全体 → B の同住所隣 (低コスト枠) へ移せる.
    r = await simulate_scope_optimization(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        scope=focus,
        config=CONFIG,
        search=_scope(office),
    )
    assert r.steps, r.excluded
    first = r.steps[0]
    assert first.patient_id == p.id
    assert first.candidate.cand_course_code == "B"  # 移動先はフォーカス外
    # 動かす対象はフォーカス内の枠のみ (B の PB1 は対象外).
    assert {s.patient_id for s in r.steps} <= {p.id}
    # フォーカスの負担が減り、探索範囲全体も悪化しない.
    assert r.focus_before is not None and r.focus_after is not None
    assert r.focus_after.travel_minutes < r.focus_before.travel_minutes
    assert (r.before.travel_minutes + r.before.buffer_minutes) >= (
        r.after.travel_minutes + r.after.buffer_minutes
    )
    # 従来挙動 (search=focus) では focus 合計 = 全体合計.
    assert r_same.focus_before == r_same.before
