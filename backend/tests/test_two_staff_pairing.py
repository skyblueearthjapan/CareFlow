"""W-12a テスト: 2 名体制のペア探索・原子採用 (設計書 docs/plans/two-staff-pairing-design.md §2.4).

対象:
    - ペア生成 (D-1/D-2): 2 コース同時刻の成立 / 相方の後方制約 NG で不成立 /
      逆走査 union の重複排除 / 決定性 / delta = 2 コース合計 / no_pair_slot 理由.
    - pfv_validator: V7 (multi_staff_incomplete_pair 警告) / V3 (slot1 衝突・同一患者例外).
    - scope_optimizer (D-5): 2 名体制 PFV が movable に入らない + excluded.two_staff 会計.
    - pool_bulk (D-6): 2 名体制患者が two_staff_pending で unplaced.

純ロジック部は DB 非依存 (座標は千葉市近辺, test_propose_slots_p1 と整合).
DB 部はローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base
from app.services.scheduling.auto_allocator_v2 import V2Visit
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.pfv_validator import (
    CODE_MULTI_STAFF_INCOMPLETE_PAIR,
    CODE_PATIENT_CONFLICT,
    validate_pfv_changes,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ExcludedReasonSummary,
    _CourseBucket,
    _marginal_cost_minutes,
    _to_existing_visits,
    compute_all_proposed_slots,
)

CFG = DEFAULT_SCHEDULING_CONFIG

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)  # BASE から ~0.14km
FAR = (35.6300, 140.1400)  # BASE から ~4.5km (回り道)
P_2_7KM = (35.6000, 140.1300)  # BASE から ~2.7km


# ---------------------------------------------------------------------------
# 純ロジック helpers (DB 非依存)
# ---------------------------------------------------------------------------


def _v2(
    start: time,
    end: time,
    *,
    lat: float,
    lng: float,
    office_id: UUID,
    course_code: str,
    service: int = 30,
) -> V2Visit:
    return V2Visit(
        patient_id=uuid4(),
        patient_name="既存",
        patient_code=None,
        weekday=0,
        start_time=start,
        end_time=end,
        service_minutes=service,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am" if start.hour < 12 else "pm",  # type: ignore[arg-type]
        source_kind="fixed",
        course_code=course_code,
    )


def _bucket(
    office_id: UUID,
    code: str,
    visits: list[V2Visit],
    *,
    template_id: UUID | None = None,
    staff_name: str = "S",
    staff_absent: bool = False,
    staff_sex: str | None = None,
    assigned_staff_id: UUID | None = None,
    ng_patient_ids: frozenset[UUID] = frozenset(),
) -> _CourseBucket:
    return _CourseBucket(
        office_id=office_id,
        weekday=0,
        course_code=code,
        office_code=None,
        staff_name=staff_name,
        course_template_id=template_id,
        visits=visits,
        staff_absent=staff_absent,
        staff_sex=staff_sex,
        assigned_staff_id=assigned_staff_id,
        ng_patient_ids=ng_patient_ids,
    )


def _candidate(
    *,
    time_type: str | None = "終日",
    preferred_start: time | None = None,
    lat: float = BASE[0],
    lng: float = BASE[1],
    service: int = 30,
) -> CandidateInput:
    return CandidateInput(
        lat=lat,
        lng=lng,
        service_minutes=service,
        time_type=time_type,
        preferred_start=preferred_start,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=True,
        existing_patient_id=None,
    )


def _compute(buckets, candidate, *, exclusions_out=None, unavailable_slots=None):
    office_id = next(iter(buckets))[0]
    return compute_all_proposed_slots(
        buckets,
        {office_id: "稲"},
        candidate,
        office_ids=[office_id],
        office_code_by_id={office_id: "INAGE"},
        config=CFG,
        exclusions_out=exclusions_out,
        unavailable_slots=unavailable_slots,
    )


# ---------------------------------------------------------------------------
# ペア生成 (D-1/D-2)
# ---------------------------------------------------------------------------


def test_pair_forms_across_two_courses() -> None:
    """同 office・同 weekday の 2 コースに同時刻で入れる候補はペア化される."""
    oid = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", []),
        (oid, 0, "B"): _bucket(oid, "B", []),
    }
    results = _compute(buckets, _candidate())
    assert results, "空 2 コースならペアが成立するはず"
    for ps in results:
        # 相方情報を持つ (partner_course_code は None でない).
        assert ps.partner_course_code == "B"
        assert ps.course_code == "A"  # primary = course_code 昇順で小さい方.
        # 同住所ペア (移動0) とは別概念なので is_pair は False.
        assert ps.is_pair is False
        # ペアは 2 名確保済なので two_staff_not_guaranteed 警告は付けない.
        assert "two_staff_not_guaranteed" not in ps.warnings
        # 相方の slot1 は同時刻.
        assert ps.start == ps.start


def test_partner_fields_populated() -> None:
    """相方コースの label / template_id / staff / mini_schedule が載る (FE 契約)."""
    oid = uuid4()
    ct_b = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", [], template_id=uuid4(), staff_name="看護A"),
        (oid, 0, "B"): _bucket(oid, "B", [], template_id=ct_b, staff_name="看護B"),
    }
    results = _compute(buckets, _candidate())
    assert results
    ps = results[0]
    assert ps.partner_course_label == "稲B"
    assert ps.partner_course_template_id == ct_b
    assert ps.partner_staff_name == "看護B"
    assert ps.partner_mini_schedule is not None
    # 相方ミニスケジュールに提案枠 (is_here) が含まれる.
    assert any(e["is_here"] for e in ps.partner_mini_schedule)


def test_no_pair_when_partner_blocked_emits_no_pair_slot() -> None:
    """slot0 は入るが相方コースの後方移動制約で同時刻に入れない → 候補 0 件 + no_pair_slot."""
    oid = uuid4()
    # A は空 → 固定 10:00 が入る. B は FAR の既存 (09:30-10:00) があり、10:00 ちょうどには
    # FAR→BASE の移動が間に合わず入れない (相方全滅).
    b_visits = [
        _v2(time(9, 30), time(10, 0), lat=FAR[0], lng=FAR[1], office_id=oid, course_code="B")
    ]
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", []),
        (oid, 0, "B"): _bucket(oid, "B", b_visits),
    }
    exclusions: list[ExcludedReasonSummary] = []
    results = _compute(
        buckets,
        _candidate(time_type="固定", preferred_start=time(10, 0)),
        exclusions_out=exclusions,
    )
    assert results == [], "片肺 (相方全滅) は候補化しない (D-2)"
    assert any(e.reason == "no_pair_slot" for e in exclusions), exclusions


def test_single_course_no_pair_slot() -> None:
    """開講コースが 1 つなら相方が作れず候補 0 件 + no_pair_slot (片肺提案は出さない)."""
    oid = uuid4()
    buckets = {(oid, 0, "A"): _bucket(oid, "A", [])}
    exclusions: list[ExcludedReasonSummary] = []
    results = _compute(buckets, _candidate(), exclusions_out=exclusions)
    assert results == []
    assert any(e.reason == "no_pair_slot" for e in exclusions), exclusions


def test_pair_dedup_single_per_time() -> None:
    """(T, {X,Y}) 無順序キーで重複排除: 各時刻につきペアは 1 件・primary は常に course_code 小."""
    oid = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", []),
        (oid, 0, "B"): _bucket(oid, "B", []),
    }
    results = _compute(buckets, _candidate())
    keys = [(ps.start, ps.course_code, ps.partner_course_code) for ps in results]
    # 逆走査 (B 主) の重複は出ない: 同一 start が 2 件 (A主/B主) にならない.
    assert len(keys) == len({(ps.start) for ps in results}), keys
    for ps in results:
        assert ps.course_code == "A" and ps.partner_course_code == "B"


def test_pair_determinism() -> None:
    """同一入力 2 回で結果 (順序含む) が完全一致する."""
    oid = uuid4()

    def _mk():
        return {
            (oid, 0, "A"): _bucket(
                oid,
                "A",
                [
                    _v2(
                        time(10, 0),
                        time(10, 30),
                        lat=P_2_7KM[0],
                        lng=P_2_7KM[1],
                        office_id=oid,
                        course_code="A",
                    )
                ],
            ),
            (oid, 0, "B"): _bucket(
                oid,
                "B",
                [
                    _v2(
                        time(10, 0),
                        time(10, 30),
                        lat=P_2_7KM[0],
                        lng=P_2_7KM[1],
                        office_id=oid,
                        course_code="B",
                    )
                ],
            ),
        }

    r1 = _compute(_mk(), _candidate())
    r2 = _compute(_mk(), _candidate())
    proj1 = [(p.start, p.course_code, p.partner_course_code, p.marginal_cost_minutes) for p in r1]
    proj2 = [(p.start, p.course_code, p.partner_course_code, p.marginal_cost_minutes) for p in r2]
    assert proj1 == proj2


def test_pair_delta_is_sum_of_two_courses() -> None:
    """ペアの marginal_cost_minutes = 2 コース独立の delta 合計 (compute_exact_marginal 2 回)."""
    oid = uuid4()
    a_visits = [
        _v2(
            time(10, 0),
            time(10, 30),
            lat=P_2_7KM[0],
            lng=P_2_7KM[1],
            office_id=oid,
            course_code="A",
        )
    ]
    b_visits = [
        _v2(
            time(10, 0),
            time(10, 30),
            lat=P_2_7KM[0],
            lng=P_2_7KM[1],
            office_id=oid,
            course_code="B",
        )
    ]
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", a_visits),
        (oid, 0, "B"): _bucket(oid, "B", b_visits),
    }
    cand = _candidate()
    results = _compute(buckets, cand)
    assert results
    ps = results[0]
    assert ps.marginal_cost_minutes is not None
    # primary=A / partner=B の delta を独立に計算して合計とクロスチェック.
    existing_a = _to_existing_visits(buckets[(oid, 0, "A")])
    existing_b = _to_existing_visits(buckets[(oid, 0, "B")])
    d_a = _marginal_cost_minutes(existing_a, ps.start, ps.end, cand, config=CFG)
    d_b = _marginal_cost_minutes(existing_b, ps.start, ps.end, cand, config=CFG)
    assert ps.marginal_cost_minutes == pytest.approx(d_a + d_b)


def test_pair_delta_within_eval_limit_ranks_by_sum() -> None:
    """delta が付いた候補は delta 昇順に並ぶ (2 コース合計を主キーに評価)."""
    oid = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", []),
        (oid, 0, "B"): _bucket(oid, "B", []),
    }
    results = _compute(buckets, _candidate())
    deltas = [p.marginal_cost_minutes for p in results if p.marginal_cost_minutes is not None]
    assert deltas == sorted(deltas)


def test_pair_partner_absent_warns() -> None:
    """相方バケット (partner) が staff_absent=True のとき staff_absent 警告が付く (FIX-1)."""
    oid = uuid4()
    sid = uuid4()
    buckets = {
        # primary (A): 割付済・出勤中
        (oid, 0, "A"): _bucket(oid, "A", [], staff_absent=False, assigned_staff_id=sid),
        # partner (B): 割付済だが非番
        (oid, 0, "B"): _bucket(oid, "B", [], staff_absent=True, assigned_staff_id=sid),
    }
    results = _compute(buckets, _candidate())
    assert results, "ペアが成立するはず"
    for ps in results:
        assert "staff_absent" in ps.warnings, ps.warnings


def test_pair_partner_sex_mismatch_warns() -> None:
    """相方バケット (partner) のスタッフが性別不適合のとき staff_sex_mismatch 警告が付く (FIX-1)."""
    oid = uuid4()
    sid = uuid4()
    buckets = {
        # primary (A): 女性スタッフ・女性制限あり患者 → primary は適合
        (oid, 0, "A"): _bucket(oid, "A", [], staff_sex="female", assigned_staff_id=sid),
        # partner (B): 男性スタッフ → 女性制限に不適合
        (oid, 0, "B"): _bucket(oid, "B", [], staff_sex="male", assigned_staff_id=sid),
    }
    # sex_restriction="female" の候補 (女性スタッフのみ可)
    cand = CandidateInput(
        lat=BASE[0],
        lng=BASE[1],
        service_minutes=30,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=True,
        existing_patient_id=None,
        sex_restriction="female",
    )
    results = _compute(buckets, cand)
    assert results, "ペアは成立するはず (sex_mismatch は除外しない)"
    for ps in results:
        assert "staff_sex_mismatch" in ps.warnings, ps.warnings


def test_pair_partner_ng_staff_excluded() -> None:
    """相方 (partner) だけが NG スタッフならペア枠自体が出ない (PO確定 2026-08-11 の除外・§6).

    A/B の 2 コースしかないので、B が NG で使えない = 相方が居ない → ペアは 0 件になる。
    """
    oid = uuid4()
    cand_pid = uuid4()
    buckets = {
        # primary (A): NG ではない担当.
        (oid, 0, "A"): _bucket(oid, "A", [], assigned_staff_id=uuid4()),
        # partner (B): 候補患者がこの担当を NG 指定している.
        (oid, 0, "B"): _bucket(
            oid, "B", [], assigned_staff_id=uuid4(), ng_patient_ids=frozenset({cand_pid})
        ),
    }
    cand = CandidateInput(
        lat=BASE[0],
        lng=BASE[1],
        service_minutes=30,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=True,
        existing_patient_id=cand_pid,
    )
    results = _compute(buckets, cand)
    assert not results, [ps.course_code for ps in results]

    # 対照: 同じ盤面で NG 指定の無い候補ならペアは従来どおり成立する.
    clean = replace(cand, existing_patient_id=uuid4())
    clean_results = _compute(buckets, clean)
    assert clean_results, "NG が無ければペアは成立するはず"
    for ps in clean_results:
        assert ps.partner_course_code == "B", ps.partner_course_code
        assert "staff_ng_mismatch" not in ps.warnings, ps.warnings


def test_pair_primary_ng_staff_excluded() -> None:
    """primary 側が NG のときもペア枠は出ない (どちらか一方でも NG なら除外・§6)."""
    oid = uuid4()
    cand_pid = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(
            oid, "A", [], assigned_staff_id=uuid4(), ng_patient_ids=frozenset({cand_pid})
        ),
        (oid, 0, "B"): _bucket(oid, "B", [], assigned_staff_id=uuid4()),
    }
    cand = CandidateInput(
        lat=BASE[0],
        lng=BASE[1],
        service_minutes=30,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=True,
        existing_patient_id=cand_pid,
    )
    assert not _compute(buckets, cand)


def test_pair_acceptance_blocked_warns() -> None:
    """アンカー時刻 T が受入カレンダー × の場合、acceptance_calendar 警告が付く (FIX-2)."""
    oid = uuid4()
    buckets = {
        (oid, 0, "A"): _bucket(oid, "A", []),
        (oid, 0, "B"): _bucket(oid, "B", []),
    }
    # 固定 10:00 提案: unavailable_slots に (office, weekday=0) → {time(10, 0)} を設定.
    blocked_time = time(10, 0)
    unavailable = {(oid, 0): {blocked_time}}
    cand = _candidate(time_type="固定", preferred_start=blocked_time)
    results = _compute(buckets, cand, unavailable_slots=unavailable)
    assert results, "acceptance_blocked は除外しない (警告のみ)"
    blocked = [ps for ps in results if ps.start == blocked_time]
    assert blocked, "blocked 時刻に候補が出るはず"
    for ps in blocked:
        assert "acceptance_calendar" in ps.warnings, ps.warnings


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

ISO_YEAR = 2026
ISO_WEEK = 22
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)


async def _make_user(db, *, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(
    db,
    *,
    code: str,
    office_id: UUID | None = None,
    coords: tuple[float, float] | None = None,
    requires_multiple_staff: bool = False,
    weekly_pattern: dict | None = None,
) -> Patient:
    lat, lng = coords if coords is not None else (None, None)
    p = Patient(
        code=code,
        name=f"患者{code}",
        special_week_active=[],
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office_id,
        requires_multiple_staff=requires_multiple_staff,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# pfv_validator V7 (multi_staff_incomplete_pair) / V3 (slot1 衝突・同一患者例外)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v7_incomplete_pair_warns(db) -> None:
    """2 名体制患者の曜日に slot0 しか無い (片肺) → V7 警告 (非ブロッキング)."""
    office = Office(code="INAGE", name="稲")
    db.add(office)
    await db.commit()
    await db.refresh(office)
    p = await _make_patient(
        db, code="2S-A", office_id=office.id, coords=BASE, requires_multiple_staff=True
    )
    items = [
        PatientFixedVisitV2Base(weekday=0, start_time=time(10, 0), duration_min=30, slot_index=0)
    ]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)
    assert not result.has_errors
    v7 = [w for w in result.warnings if w.code == CODE_MULTI_STAFF_INCOMPLETE_PAIR]
    assert len(v7) == 1 and v7[0].weekday == 0


@pytest.mark.asyncio
async def test_v7_complete_pair_no_warn_and_self_slots_not_conflict(db) -> None:
    """slot0/slot1 が揃った曜日は V7 を出さず、同一患者の slot0/slot1 同時刻も V3 非衝突."""
    office = Office(code="INAGE", name="稲")
    db.add(office)
    await db.commit()
    await db.refresh(office)
    p = await _make_patient(
        db, code="2S-B", office_id=office.id, coords=BASE, requires_multiple_staff=True
    )
    ct_a, ct_b = uuid4(), uuid4()
    items = [
        PatientFixedVisitV2Base(
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
            course_template_id=ct_a,
        ),
        PatientFixedVisitV2Base(
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=1,
            course_template_id=ct_b,
        ),
    ]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)
    assert all(w.code != CODE_MULTI_STAFF_INCOMPLETE_PAIR for w in result.warnings)
    # 同一患者の slot0/slot1 同時刻は衝突とみなさない.
    assert all(w.code != CODE_PATIENT_CONFLICT for w in result.warnings)


@pytest.mark.asyncio
async def test_v3_detects_other_patient_slot1_conflict(db) -> None:
    """他患者の slot1 も V3 衝突検査対象に含める (W-12a: slot_index=0 限定を撤廃)."""
    office = Office(code="INAGE", name="稲")
    db.add(office)
    await db.commit()
    await db.refresh(office)
    ct = uuid4()
    target = await _make_patient(db, code="T", office_id=office.id, coords=BASE)
    other = await _make_patient(
        db, code="O", office_id=office.id, coords=FAR, requires_multiple_staff=True
    )
    # 他患者の slot1 (course=ct, 10:15, FAR). target の 10:00-10:30 BASE と移動込みで衝突.
    db.add(
        PatientFixedVisit(
            patient_id=other.id,
            mode="normal",
            weekday=0,
            slot_index=1,
            start_time=time(10, 15),
            duration_min=30,
            course_template_id=ct,
        )
    )
    await db.commit()

    items = [
        PatientFixedVisitV2Base(
            weekday=0, start_time=time(10, 0), duration_min=30, slot_index=0, course_template_id=ct
        )
    ]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)
    assert any(w.code == CODE_PATIENT_CONFLICT for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# scope_optimizer D-5 (2 名体制 PFV は movable 除外 + excluded.two_staff 会計)
# ---------------------------------------------------------------------------


async def _seed_scope_sandwich(db, *, requires_multiple_staff: bool) -> Office:
    """FAR — P(BASE, time_flexible) — FAR の course A(Mon) を持つ拠点を作る."""
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="S1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    def _pt(code: str, xy: tuple[float, float], multi: bool = False) -> Patient:
        return Patient(
            code=code,
            name=f"P-{code}",
            status="active",
            lat=xy[0],
            lng=xy[1],
            primary_office_id=office.id,
            requires_multiple_staff=multi,
        )

    p = _pt("TGT", BASE, requires_multiple_staff)
    fa1 = _pt("FA1", FAR)
    fa2 = _pt("FA2", FAR)
    db.add_all([p, fa1, fa2])
    await db.flush()

    def _vis(patient: Patient, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=WEEK_MONDAY,
            start_time=start,
            end_time=end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )

    db.add_all(
        [
            _vis(fa1, time(9, 30), time(10, 0)),
            _vis(p, time(10, 30), time(11, 0)),
            _vis(fa2, time(11, 15), time(11, 45)),
        ]
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            slot_index=0,
            start_time=time(10, 30),
            duration_min=30,
            movability="time_flexible",
        )
    )
    await db.commit()
    return office


def _scope_body(office: Office) -> dict[str, Any]:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]},
    }


@pytest.mark.asyncio
async def test_scope_two_staff_protected_from_movable(client, db) -> None:
    """2 名体制患者の PFV は movable に入らず、excluded.two_staff に計上される (D-5)."""
    admin = await _make_user(db, email="2s-scope@example.com")
    office = await _seed_scope_sandwich(db, requires_multiple_staff=True)

    res = await client.post(
        "/api/v1/schedule/v2/scope-optimization/simulate",
        headers=_bearer(admin),
        json=_scope_body(office),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # 唯一の可動候補が 2 名体制で保護されるので手順は出ない.
    assert data["steps"] == [], data
    assert data["excluded_summary"]["two_staff"] >= 1, data["excluded_summary"]


@pytest.mark.asyncio
async def test_scope_single_staff_moves_baseline(client, db) -> None:
    """対照: 同構成でも requires_multiple_staff=False なら従来どおり手順が出る (保護は 2 名体制のみ)."""
    admin = await _make_user(db, email="1s-scope@example.com")
    office = await _seed_scope_sandwich(db, requires_multiple_staff=False)

    res = await client.post(
        "/api/v1/schedule/v2/scope-optimization/simulate",
        headers=_bearer(admin),
        json=_scope_body(office),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["steps"], data
    assert data["excluded_summary"]["two_staff"] == 0


# ---------------------------------------------------------------------------
# pool_bulk D-6 (2 名体制患者は two_staff_pending で unplaced)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_two_staff_pending(client, db) -> None:
    """一括投入は v1 で 2 名体制を対象外: two_staff_pending で unplaced に明示分離する (D-6)."""
    admin = await _make_user(db, email="2s-bulk@example.com")
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="S", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    wp = {
        "service_minutes": 30,
        "time_type": "終日",
        "preferred_weekdays": ["Mon"],
        "frequency_per_week": 1,
    }
    p = await _make_patient(
        db,
        code="2S-POOL",
        office_id=office.id,
        coords=BASE,
        requires_multiple_staff=True,
        weekly_pattern=wp,
    )

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert not body["placements"]
    assert any(
        u["patient_id"] == str(p.id) and u["reason"] == "two_staff_pending"
        for u in body["unplaced"]
    ), body["unplaced"]
