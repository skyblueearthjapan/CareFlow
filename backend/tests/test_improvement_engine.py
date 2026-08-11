"""Tests for improvement_engine (P2-B).

限界コスト方式 (marginal_cost) の基本 / 同住所 / 先頭末尾 の手計算一致と、
find_improvement_candidates の movability 尊重規則・閾値・却下指紋除外・
filtered_summary の各カウントを検証する.

ローカル SQLite のみ (本番 DB 禁止).

座標メモ (千葉市近辺, test_propose_slots_api.py と整合):
    BASE = (35.6000, 140.1000)
    NEAR = (35.6010, 140.1010)  → BASE から ~0.14km
    FAR  = (35.6300, 140.1400)  → BASE から ~4.9km
    SAME = (35.60005, 140.10005) → BASE と同住所 (<=100m)
"""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffShift
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator_v2 import (
    VISIT_BUFFER_MINUTES,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.improvement_engine import (
    IMPROVEMENT_THRESHOLD_MIN,
    _slot_within_preference,
    compute_marginal_cost,
    find_improvement_candidates,
)

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)
SAME = (35.60005, 140.10005)


# ---------------------------------------------------------------------------
# 限界コスト (純ロジック・手計算一致)
# ---------------------------------------------------------------------------


def _tb(a: tuple[float, float], b: tuple[float, float]) -> int:
    """異住所 2 点間の移動 + バッファー (分). module 定数 (speed=20, buffer=8) 基準."""
    return haversine_minutes(haversine_km(a[0], a[1], b[0], b[1])) + VISIT_BUFFER_MINUTES


def test_marginal_cost_middle() -> None:
    """中央 (prev/next 両方あり). target=FAR を BASE と BASE の間に挟む.

    手計算: marginal = travel(BASE→FAR) + travel(FAR→BASE) − travel(BASE→BASE).
    BASE→BASE は同住所 = 0. よって marginal = 2 × travel(BASE→FAR).
    km も同様に 2 × haversine_km(BASE, FAR).
    """
    minutes, km = compute_marginal_cost(FAR, BASE, BASE)
    assert minutes == 2 * _tb(BASE, FAR)
    assert km == pytest.approx(2 * haversine_km(*BASE, *FAR))


def test_marginal_cost_same_address_zero() -> None:
    """同住所: target=SAME を BASE / BASE の間に挟むと全辺 0 → marginal 0."""
    minutes, km = compute_marginal_cost(SAME, BASE, BASE)
    assert minutes == 0
    assert km == pytest.approx(0.0)


def test_marginal_cost_head_and_tail_single_edge() -> None:
    """先頭 (prev=None) / 末尾 (next=None) は該当辺のみ.

    先頭 target=FAR, next=BASE → travel(FAR→BASE) のみ.
    末尾 target=FAR, prev=BASE → travel(BASE→FAR) のみ.
    どちらも 1 辺分 = _tb(BASE, FAR).
    """
    head_min, head_km = compute_marginal_cost(FAR, None, BASE)
    tail_min, tail_km = compute_marginal_cost(FAR, BASE, None)
    assert head_min == _tb(BASE, FAR)
    assert tail_min == _tb(BASE, FAR)
    assert head_km == pytest.approx(haversine_km(*BASE, *FAR))
    assert tail_km == pytest.approx(haversine_km(*BASE, *FAR))


# ---------------------------------------------------------------------------
# find_improvement_candidates fixtures
# ---------------------------------------------------------------------------


async def _seed_office(db, *, code: str = "INAGE", name: str = "稲") -> Office:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    return office


async def _seed_staff(db, *, office: Office, name: str, sex: str | None = None) -> Staff:
    staff = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id, sex=sex)
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


async def _two_course_improvement_scenario(
    db, *, target_movability: str, target_weekday: int = 0, alt_weekday: int = 0
):
    """target 患者 P(BASE) を Monday course A (FAR 群に挟まれ marginal 高) に配置し、
    別コースに同住所 (SAME) 相手のいる低 marginal スロットを用意する.

    ``alt_weekday`` を変えると「別曜日にだけ改善枠がある」状況を作れる.
    """
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    s2 = await _seed_staff(db, office=office, name="S2")
    await _seed_shift(db, staff=s1, weekday=target_weekday)
    await _seed_shift(db, staff=s2, weekday=alt_weekday)

    p = await _seed_patient(db, office=office, code="TGT", lat=BASE[0], lng=BASE[1])

    # Course A (target_weekday): FAR — P(BASE) — FAR. P の限界コストは高い.
    ca = await _seed_course(db, office=office, staff=s1, weekday=target_weekday, code="A")
    fa1 = await _seed_patient(db, office=office, code="FA1", lat=FAR[0], lng=FAR[1])
    fa2 = await _seed_patient(db, office=office, code="FA2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(
        db, patient=fa1, course=ca, weekday=target_weekday, start=time(9, 30), end=time(10, 0)
    )
    await _seed_visit(
        db, patient=p, course=ca, weekday=target_weekday, start=time(10, 30), end=time(11, 0)
    )
    await _seed_visit(
        db, patient=fa2, course=ca, weekday=target_weekday, start=time(11, 15), end=time(11, 45)
    )

    # Course B (alt_weekday): SAME 相手 1 名のみ. P(BASE) は同住所で marginal ~0.
    cb = await _seed_course(db, office=office, staff=s2, weekday=alt_weekday, code="B")
    pb1 = await _seed_patient(db, office=office, code="PB1", lat=SAME[0], lng=SAME[1])
    await _seed_visit(
        db, patient=pb1, course=cb, weekday=alt_weekday, start=time(9, 30), end=time(10, 0)
    )

    await _seed_pfv(
        db, patient=p, weekday=target_weekday, start=time(10, 30), movability=target_movability
    )
    await db.commit()
    return office, p


# ---------------------------------------------------------------------------
# movability 尊重規則
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_pfv_excluded(db) -> None:
    _office, p = await _two_course_improvement_scenario(db, target_movability="locked")
    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions == []
    assert summary.locked == 1
    assert summary.pinned == 0


@pytest.mark.asyncio
async def test_pinned_pfv_excluded(db) -> None:
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    await _seed_shift(db, staff=s1, weekday=0)
    p = await _seed_patient(db, office=office, code="TGT", lat=BASE[0], lng=BASE[1])
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")
    await _seed_visit(db, patient=p, course=ca, weekday=0, start=time(10, 30), end=time(11, 0))
    # is_pinned=True (movability は locked backfill 相当) → pinned でカウントし除外.
    await _seed_pfv(
        db, patient=p, weekday=0, start=time(10, 30), movability="locked", is_pinned=True
    )
    await db.commit()

    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions == []
    assert summary.pinned == 1
    assert summary.locked == 0


@pytest.mark.asyncio
async def test_unknown_same_weekday_time_change_requires_confirmation(db) -> None:
    """unknown: 同曜日の時刻/コース変更のみ提案し requires_patient_confirmation=True.

    別曜日 (alt_weekday=2) にだけ低 marginal 枠を置き、それが day_change として
    抑制され day_restricted にカウントされることも確認する.
    """
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="unknown", target_weekday=0, alt_weekday=2
    )
    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    # 提案はすべて time_change (同曜日) で要確認ラベル付き.
    assert suggestions, summary
    assert all(s.kind == "time_change" for s in suggestions)
    assert all(s.requires_patient_confirmation for s in suggestions)
    assert all(s.cand_weekday == 0 for s in suggestions)
    # 別曜日の改善枠は day_restricted として抑制 (0 件ではない).
    assert summary.day_restricted >= 1


@pytest.mark.asyncio
async def test_day_flexible_allows_day_change(db) -> None:
    """day_flexible: 別曜日への day_change 提案が出る (target_weekday は現在曜日)."""
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="day_flexible", target_weekday=0, alt_weekday=2
    )
    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions, summary
    day_changes = [s for s in suggestions if s.kind == "day_change"]
    assert day_changes, suggestions
    # day_change でも要確認は付かない (unknown 専用).
    assert all(not s.requires_patient_confirmation for s in day_changes)
    # 却下指紋の対象曜日は現在枠 (Monday=0).
    assert all(s.target_weekday == 0 for s in suggestions)
    assert summary.day_restricted == 0


@pytest.mark.asyncio
async def test_time_flexible_same_weekday_no_confirmation(db) -> None:
    """time_flexible: 同曜日の時刻変更を出すが要確認は付かない."""
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="time_flexible", target_weekday=0, alt_weekday=0
    )
    suggestions, _summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions
    assert all(s.kind == "time_change" for s in suggestions)
    assert all(not s.requires_patient_confirmation for s in suggestions)


@pytest.mark.asyncio
async def test_improvement_staff_ng_destination_excluded(db) -> None:
    """移動先コース (B) の担当が NG スタッフなら候補が出ない (PO確定 2026-08-11 の除外・§6).

    NG 設定前は B への候補が出ることを同一シナリオで確認してから NG を張り、B の候補が
    消えること (= 警告方式からハード除外への格上げ) を確認する.
    """
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="time_flexible", target_weekday=0, alt_weekday=0
    )
    before, _summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    b_before = [s for s in before if s.cand_course_code == "B"]
    assert b_before, before
    assert all("staff_ng_mismatch" not in s.staff_warnings for s in b_before)

    # Course B の担当 (S2) を NG 指定する.
    s2 = (await db.scalars(select(Staff).where(Staff.name == "S2"))).one()
    db.add(PatientNgStaff(patient_id=p.id, staff_id=s2.id))
    await db.commit()

    after, _summary2 = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    b_after = [s for s in after if s.cand_course_code == "B"]
    assert not b_after, b_after  # NG コースへの提案は一切出さない.
    assert all("staff_ng_mismatch" not in s.staff_warnings for s in after)


# ---------------------------------------------------------------------------
# 閾値 / 却下指紋 / 上限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_filtered(db) -> None:
    """効果が閾値未満なら提案せず below_threshold にカウント.

    単一コースで FAR — P(BASE) — FAR. P を除いた再走査でも neighbors は FAR のままな
    ので、どの空きに入れても marginal は現在とほぼ同じ (delta < 10) → below_threshold.
    """
    office = await _seed_office(db)
    s1 = await _seed_staff(db, office=office, name="S1")
    await _seed_shift(db, staff=s1, weekday=0)
    p = await _seed_patient(db, office=office, code="TGT", lat=FAR[0], lng=FAR[1])
    ca = await _seed_course(db, office=office, staff=s1, weekday=0, code="A")
    # 隣人も FAR (P と同住所) にして、どの位置でも marginal 差が出ないようにする.
    o1 = await _seed_patient(db, office=office, code="O1", lat=FAR[0], lng=FAR[1])
    o2 = await _seed_patient(db, office=office, code="O2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=o1, course=ca, weekday=0, start=time(9, 30), end=time(10, 0))
    await _seed_visit(db, patient=p, course=ca, weekday=0, start=time(10, 30), end=time(11, 0))
    await _seed_visit(db, patient=o2, course=ca, weekday=0, start=time(11, 15), end=time(11, 45))
    await _seed_pfv(db, patient=p, weekday=0, start=time(10, 30), movability="time_flexible")
    await db.commit()

    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions == []
    assert summary.below_threshold >= 1


@pytest.mark.asyncio
async def test_dismissed_fingerprint_excluded(db) -> None:
    """却下指紋 (time_change, weekday=0) 一致で time_change 提案を抑制しカウント."""
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="time_flexible", target_weekday=0, alt_weekday=0
    )
    db.add(
        SuggestionDismissal(
            patient_id=p.id,
            kind="time_change",
            target_weekday=0,
            reason="time_immovable",
        )
    )
    await db.commit()

    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(s.kind != "time_change" for s in suggestions)
    assert summary.dismissed >= 1


@pytest.mark.asyncio
async def test_threshold_constant_and_delta_sign(db) -> None:
    """提案は delta >= 閾値 のみ. delta 降順で最大 5 件."""
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="time_flexible", target_weekday=0, alt_weekday=0
    )
    suggestions, _summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions
    assert len(suggestions) <= 5
    deltas = [s.delta_minutes for s in suggestions]
    assert deltas == sorted(deltas, reverse=True)
    assert all(d >= IMPROVEMENT_THRESHOLD_MIN for d in deltas)


# ---------------------------------------------------------------------------
# P4-A: 希望訪問スケジュール (weekly_pattern) 範囲内判定 — 純関数
# ---------------------------------------------------------------------------


def _pat(pattern: object) -> SimpleNamespace:
    """_extract_weekly_entries は patient.weekly_pattern だけを読むため軽量 stub で足りる."""
    return SimpleNamespace(weekly_pattern=pattern)


def test_within_preference_empty_pattern_is_false() -> None:
    """weekly_pattern 未登録 / 空 → 常に False (層 3 フォールバック = 挙動後退なし)."""
    assert _slot_within_preference(_pat(None), 0, time(10, 0), time(10, 30)) is False
    assert _slot_within_preference(_pat({}), 0, time(10, 0), time(10, 30)) is False
    assert _slot_within_preference(_pat({"entries": []}), 0, time(10, 0), time(10, 30)) is False


def test_within_preference_time_band() -> None:
    """時間帯: [preferred_start, preferred_end] 内に開始なら True. 別曜日は False."""
    p = _pat(
        {
            "entries": [
                {
                    "weekday": 2,
                    "time_type": "時間帯",
                    "preferred_start": "13:00",
                    "preferred_end": "17:00",
                },
            ]
        }
    )
    assert _slot_within_preference(p, 2, time(14, 0), time(14, 30)) is True
    assert _slot_within_preference(p, 2, time(12, 0), time(12, 30)) is False
    assert _slot_within_preference(p, 0, time(14, 0), time(14, 30)) is False


def test_within_preference_fixed_requires_exact_start() -> None:
    """固定: start が preferred_start に厳密一致のときのみ True."""
    p = _pat({"entries": [{"weekday": 0, "time_type": "固定", "preferred_start": "10:00"}]})
    assert _slot_within_preference(p, 0, time(10, 0), time(10, 30)) is True
    assert _slot_within_preference(p, 0, time(10, 30), time(11, 0)) is False


def test_within_preference_am_pm_boundaries() -> None:
    """午前: end<=12:00 / 午後: start>=13:00 の境界 (ちょうど値は True)."""
    am = _pat({"entries": [{"weekday": 0, "time_type": "午前"}]})
    assert _slot_within_preference(am, 0, time(11, 0), time(11, 30)) is True
    # 境界: end=ちょうど 12:00 は True (end <= AM_BLOCK_END = 12:00).
    assert _slot_within_preference(am, 0, time(11, 30), time(12, 0)) is True
    assert _slot_within_preference(am, 0, time(11, 45), time(12, 15)) is False
    pm = _pat({"entries": [{"weekday": 0, "time_type": "午後"}]})
    # 境界: start=ちょうど 13:00 は True (start >= PM_BLOCK_START = 13:00).
    assert _slot_within_preference(pm, 0, time(13, 0), time(13, 30)) is True
    assert _slot_within_preference(pm, 0, time(12, 30), time(13, 0)) is False


def test_within_preference_allday_covers_any_time() -> None:
    """終日 / None: その曜日ならいつでも True, 別曜日は False."""
    p = _pat({"entries": [{"weekday": 0, "time_type": None}]})
    assert _slot_within_preference(p, 0, time(9, 45), time(10, 15)) is True
    assert _slot_within_preference(p, 1, time(9, 45), time(10, 15)) is False


# ---------------------------------------------------------------------------
# P4-A: 希望範囲内の move 候補 (層 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_within_preference_cross_weekday_unknown_no_confirmation(db) -> None:
    """希望内 (終日) の曜日跨ぎは movability=unknown でも確認不要で提案される (層 2).

    対照: 同シナリオで weekly_pattern 未登録なら別曜日枠は day_restricted になる
    (test_unknown_same_weekday_time_change_requires_confirmation).
    """
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="unknown", target_weekday=0, alt_weekday=2
    )
    # 患者の希望訪問スケジュールに水(2) を登録 → alt_weekday の改善枠が希望内になる.
    p.weekly_pattern = {"entries": [{"weekday": 2, "time_type": None}]}
    await db.commit()

    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    day2 = [s for s in suggestions if s.cand_weekday == 2]
    assert day2, suggestions
    assert all(s.kind == "day_change" for s in day2)
    assert all(s.within_preference for s in day2)
    assert all(not s.requires_patient_confirmation for s in day2)
    # 希望内で通ったので day_restricted には計上されない (意味変化: 希望外のみ計上).
    assert summary.day_restricted == 0


@pytest.mark.asyncio
async def test_empty_weekly_pattern_falls_back_to_layer3(db) -> None:
    """weekly_pattern 空 → 従来ルール (unknown の曜日跨ぎは day_restricted) が完全維持."""
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="unknown", target_weekday=0, alt_weekday=2
    )
    p.weekly_pattern = {}
    await db.commit()

    suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert all(not s.within_preference for s in suggestions)
    assert all(s.cand_weekday == 0 for s in suggestions)
    assert summary.day_restricted >= 1


async def _three_weekday_preference_scenario(db):
    """月(現在, 高 marginal) / 火(希望外, delta 最大) / 水(希望内, delta 中) の 3 コース.

    希望内 (水) は delta が火より小さいが、並び順で火より上位に来ることを検証するための形.
    """
    office = await _seed_office(db)
    s0 = await _seed_staff(db, office=office, name="S0")
    s1 = await _seed_staff(db, office=office, name="S1")
    s2 = await _seed_staff(db, office=office, name="S2")
    await _seed_shift(db, staff=s0, weekday=0)
    await _seed_shift(db, staff=s1, weekday=1)
    await _seed_shift(db, staff=s2, weekday=2)

    p = await _seed_patient(db, office=office, code="TGT", lat=BASE[0], lng=BASE[1])

    # Course A (月): FAR — p(BASE) — FAR → current marginal 高.
    ca = await _seed_course(db, office=office, staff=s0, weekday=0, code="A")
    fa1 = await _seed_patient(db, office=office, code="FA1", lat=FAR[0], lng=FAR[1])
    fa2 = await _seed_patient(db, office=office, code="FA2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=fa1, course=ca, weekday=0, start=time(9, 30), end=time(10, 0))
    await _seed_visit(db, patient=p, course=ca, weekday=0, start=time(10, 30), end=time(11, 0))
    await _seed_visit(db, patient=fa2, course=ca, weekday=0, start=time(11, 15), end=time(11, 45))

    # Course B (火): SAME 相手のみ → p は同住所で marginal ~0 → delta 最大 (希望外).
    cb = await _seed_course(db, office=office, staff=s1, weekday=1, code="B")
    pb1 = await _seed_patient(db, office=office, code="PB1", lat=SAME[0], lng=SAME[1])
    await _seed_visit(db, patient=pb1, course=cb, weekday=1, start=time(9, 30), end=time(10, 0))

    # Course C (水): NEAR 相手のみ → p の marginal は小だが 0 ではない → delta 中 (希望内).
    cc = await _seed_course(db, office=office, staff=s2, weekday=2, code="C")
    pc1 = await _seed_patient(db, office=office, code="PC1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pc1, course=cc, weekday=2, start=time(9, 30), end=time(10, 0))

    await _seed_pfv(db, patient=p, weekday=0, start=time(10, 30), movability="day_flexible")
    p.weekly_pattern = {"entries": [{"weekday": 2, "time_type": None}]}
    await db.commit()
    return office, p


@pytest.mark.asyncio
async def test_within_preference_sorts_before_larger_delta(db) -> None:
    """希望内 (水) は delta が小さくても、希望外 (火) の大 delta より上位に並ぶ.

    並び順キー (not within_preference, -delta) の第 1 要素で希望内が優先されることを確認.
    """
    _office, p = await _three_weekday_preference_scenario(db)
    suggestions, _summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions
    # 先頭は希望内 (水=2).
    assert suggestions[0].cand_weekday == 2
    assert suggestions[0].within_preference is True
    # 火 (=1) の希望外候補は存在し、delta は水以上だが後ろに並ぶ (delta より希望内が優先).
    tue = [s for s in suggestions if s.cand_weekday == 1]
    assert tue
    assert not tue[0].within_preference
    assert tue[0].delta_minutes >= suggestions[0].delta_minutes
    assert suggestions.index(tue[0]) > 0


@pytest.mark.asyncio
async def test_within_preference_day_change_dismissed_counted(db) -> None:
    """P4-A MEDIUM: unknown + 希望内 day_change が dismissed 指紋で阻止されたとき
    summary.dismissed >= 1 (旧の事前カウントでは未計上だったバグの回帰防止).

    シナリオ:
      - movability=unknown → 通常は alt_weekday(水) への day_change は day_restricted になる.
      - weekly_pattern に水(2) を登録 → within_pref=True → movability ゲートを通過する.
      - day_change 却下指紋 (kind='day_change', target_weekday=0) があると dismissed で阻止.
      - この阻止が summary.dismissed に計上されることを確認.
    """
    _office, p = await _two_course_improvement_scenario(
        db, target_movability="unknown", target_weekday=0, alt_weekday=2
    )
    # 希望内登録 → P4-A 層 2 で movability ゲート通過 → dismissed チェックへ到達.
    p.weekly_pattern = {"entries": [{"weekday": 2, "time_type": None}]}
    db.add(
        SuggestionDismissal(
            patient_id=p.id,
            kind="day_change",
            target_weekday=0,
            reason="day_immovable",
        )
    )
    await db.commit()

    _suggestions, summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    # 水曜の day_change 候補は dismissed で抑制され、summary.dismissed に計上される.
    assert summary.dismissed >= 1
    # day_restricted には計上されない (希望内は層 2 = movability ゲートを通過したため).
    assert summary.day_restricted == 0


# ---------------------------------------------------------------------------
# UI 統一: コーススナップショット (タイムライン表示用)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_carry_course_snapshots(db) -> None:
    """改善提案には移動元 (と別コースなら移動先) のスナップショットが付く.

    - source_course: 必ず付き、対象患者を含むコース全員の訪問列 (start 昇順)。
    - destination_course: 候補が別コース (office×weekday×code が異なる) のときのみ。
      同一コース内の候補は None (FE は 1 枚のタイムラインで移動を描く)。
    """
    _office, p = await _two_course_improvement_scenario(db, target_movability="time_flexible")
    suggestions, _summary = await find_improvement_candidates(
        db, patient=p, iso_year=ISO_YEAR, iso_week=ISO_WEEK
    )
    assert suggestions
    for s in suggestions:
        src = s.source_course
        assert src is not None
        assert any(v.patient_id == p.id for v in src.visits)
        starts = [v.start_time for v in src.visits]
        assert starts == sorted(starts)
        same_course = (s.cand_office_id, s.cand_weekday, s.cand_course_code) == (
            src.office_id,
            src.weekday,
            src.course_code,
        )
        if same_course:
            assert s.destination_course is None
        else:
            assert s.destination_course is not None
            assert s.destination_course.course_code == s.cand_course_code
    # このシナリオは course B (別コース) への移動候補を含むはず.
    assert any(s.destination_course is not None for s in suggestions)
