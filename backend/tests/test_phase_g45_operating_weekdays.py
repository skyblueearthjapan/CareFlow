"""Phase G-45: 拠点稼働曜日 (operating_weekdays) のパイプライン skip 動作.

責務:
    - ``_load_office_operating_weekdays`` の default fallback / 不正値 fallback.
    - ``run_v2_pipeline`` (mode=full_optimize / diff_add) で休業日 visit が
      Before / After 共に emit されないこと.
    - sub_office (Phase E-5) 経路では cross-office 振替で skip を bypass できること.
    - default (= 月-土) の office では従来挙動が変わらないこと (regression).
    - ``reset_visits_to_fixed`` も同様に skip すること.

sabotage で検証する fix の load-bearing assertion:
    - "office_closed warning が emit される"
    - "休業日 weekday の after_visit が 0 件"
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

import pytest

from app.models import Office, Patient
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    DEFAULT_OFFICE_OPERATING_WEEKDAYS,
    _coerce_operating_weekdays,
    _load_office_operating_weekdays,
    reset_visits_to_fixed,
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_office(
    db,
    *,
    name: str,
    code: str | None = None,
    operating_weekdays: list[int] | None = None,
) -> Office:
    o = Office(
        name=name,
        code=code,
        operating_weekdays=operating_weekdays
        if operating_weekdays is not None
        else [0, 1, 2, 3, 4, 5],
    )
    db.add(o)
    await db.flush()
    return o


async def _make_patient(
    db,
    *,
    code: str,
    name: str,
    office: Office,
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name,
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start_hhmm: tuple[int, int],
    is_pinned: bool = True,
    duration_min: int = 30,
    course_template_id=None,
    sub_office_id=None,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(start_hhmm[0], start_hhmm[1]),
        duration_min=duration_min,
        slot_index=0,
        is_pinned=is_pinned,
        course_template_id=course_template_id,
        sub_office_id=sub_office_id,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _make_course_template(
    db, *, office: Office, label: str, capacity_mon: int = 6
) -> CourseTemplate:
    ct = CourseTemplate(office_id=office.id, label=label, capacity_mon=capacity_mon)
    db.add(ct)
    await db.flush()
    return ct


async def _seed_staff_and_shifts(db, *, office: Office, weekdays: list[int], name: str) -> Staff:
    s = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    for wd in weekdays:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# _coerce_operating_weekdays unit tests
# ---------------------------------------------------------------------------


def test_coerce_operating_weekdays_default_when_null() -> None:
    """後方互換: None / 空 list は default (= 月-土) にフォールバック."""
    assert _coerce_operating_weekdays(None) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays([]) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)


def test_coerce_operating_weekdays_invalid_json_falls_back() -> None:
    """不正値 (= 範囲外 / 非 int / dict 等) は default にフォールバック."""
    assert _coerce_operating_weekdays("not-a-list") == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays({"foo": 1}) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays([0, 7]) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays([0, -1]) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays([True, False]) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    assert _coerce_operating_weekdays([0, "1"]) == set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)


def test_coerce_operating_weekdays_normal_values() -> None:
    assert _coerce_operating_weekdays([0, 1, 2, 3, 4]) == {0, 1, 2, 3, 4}
    assert _coerce_operating_weekdays([6]) == {6}


# ---------------------------------------------------------------------------
# _load_office_operating_weekdays DB integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_office_operating_weekdays_returns_per_office_set(db) -> None:
    """DB から各 office の operating_weekdays が正しく取れる."""
    o1 = await _make_office(db, name="g45-A", code="A", operating_weekdays=[0, 1, 2, 3, 4, 5])
    o2 = await _make_office(db, name="g45-B", code="B", operating_weekdays=[0, 1, 2, 3, 4])
    await db.commit()
    out = await _load_office_operating_weekdays(db, office_ids=[o1.id, o2.id])
    assert out[o1.id] == {0, 1, 2, 3, 4, 5}
    assert out[o2.id] == {0, 1, 2, 3, 4}


@pytest.mark.asyncio
async def test_load_office_operating_weekdays_empty_office_ids_returns_empty(db) -> None:
    out = await _load_office_operating_weekdays(db, office_ids=[])
    assert out == {}


@pytest.mark.asyncio
async def test_load_office_operating_weekdays_missing_office_id_omitted(db) -> None:
    """存在しない office_id は dict に含まれない."""
    o1 = await _make_office(db, name="g45-X", code="X")
    await db.commit()
    bogus = uuid4()
    out = await _load_office_operating_weekdays(db, office_ids=[o1.id, bogus])
    assert o1.id in out
    assert bogus not in out


@pytest.mark.asyncio
async def test_load_office_operating_weekdays_excludes_deleted(db) -> None:
    """Phase G-45 review HIGH-3: soft-delete された office は map に含まれない.

    シナリオ:
      - alive office と deleted_at セット済 office を 2 件用意.
      - _load_office_operating_weekdays に両方の office_id を渡す.
      - 期待: alive のみ map に含まれ、 deleted は除外される.

    Why: deleted office の operating_weekdays が混入すると、 ソフト削除済 office
    に向けた warning / skip 判定が誤動作する (= deleted office が「営業日」と
    扱われ得る).
    """
    alive = await _make_office(
        db, name="g45-ALIVE", code="ALIVE", operating_weekdays=[0, 1, 2, 3, 4, 5]
    )
    deleted = await _make_office(
        db, name="g45-DELETED", code="DELETED", operating_weekdays=[0, 1, 2]
    )
    deleted.deleted_at = datetime.now(UTC)
    await db.commit()

    out = await _load_office_operating_weekdays(db, office_ids=[alive.id, deleted.id])

    assert alive.id in out, "alive office は map に含まれるべき"
    assert deleted.id not in out, "deleted_at セット済 office は map に含まれてはならない"
    assert out[alive.id] == {0, 1, 2, 3, 4, 5}


# ---------------------------------------------------------------------------
# E2E: run_v2_pipeline で休業日 visit が emit されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_v2_pipeline_skips_closed_weekday_pool(db) -> None:
    """weekly_pattern 由来 visit が拠点休業日に emit されないことを確認.

    シナリオ:
      - TSUGA office (operating_weekdays = [0..4] = 月-金).
      - 患者 = primary_office TSUGA, weekly_pattern.preferred_weekdays = [Sat]
        (= weekday 5 = 都賀休業日).
      - 期待: after_visits 0 件 + office_closed warning emit.
    """
    tsuga = await _make_office(
        db, name="g45-TSUGA-pool", code="TSUGA1", operating_weekdays=[0, 1, 2, 3, 4]
    )
    p = await _make_patient(
        db,
        code="G45-POOL-1",
        name="g45 pool sat",
        office=tsuga,
        weekly_pattern={
            "preferred_weekdays": ["Sat"],
            "preferred_start": "10:00",
            "time_type": "希望",
            "service_minutes": 30,
        },
    )
    await _seed_staff_and_shifts(db, office=tsuga, weekdays=[5], name="g45-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[tsuga.id],
        mode="full_optimize",
    )
    after = result.get("after_visits") or []
    cross = [v for v in after if v.patient_id == p.id and v.weekday == 5]
    assert cross == [], f"Sat visit が emit された (期待 0 件): {cross}"
    warnings = result.get("warnings") or []
    closed = [w for w in warnings if w.type == "office_closed" and w.weekday == 5]
    assert len(closed) >= 1, f"office_closed warning が emit されていない: warnings={warnings}"


@pytest.mark.asyncio
async def test_run_v2_pipeline_skips_closed_weekday_pfv(db) -> None:
    """PFV (= Before / orphan PFV) 由来 visit も休業日には emit されない."""
    tsuga = await _make_office(
        db, name="g45-TSUGA-pfv", code="TSUGA2", operating_weekdays=[0, 1, 2, 3, 4]
    )
    ct_a = await _make_course_template(db, office=tsuga, label="A")
    p = await _make_patient(
        db,
        code="G45-PFV-1",
        name="g45 pfv sat",
        office=tsuga,
        weekly_pattern=None,  # orphan PFV シナリオ
    )
    await _make_pfv(
        db,
        patient=p,
        weekday=5,  # Sat = 都賀休業日
        start_hhmm=(10, 0),
        is_pinned=True,
        course_template_id=ct_a.id,
    )
    await _seed_staff_and_shifts(db, office=tsuga, weekdays=[5], name="g45-pfv-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[tsuga.id],
        mode="full_optimize",
    )
    after = result.get("after_visits") or []
    before = result.get("before_visits") or []
    assert all(not (v.patient_id == p.id and v.weekday == 5) for v in after), (
        f"PFV 由来 Sat visit が after に残った: {after}"
    )
    assert all(not (v.patient_id == p.id and v.weekday == 5) for v in before), (
        f"PFV 由来 Sat visit が before に残った: {before}"
    )


@pytest.mark.asyncio
async def test_run_v2_pipeline_cross_office_pfv_sub_office_overrides_closed_day(db) -> None:
    """sub_office 経由で振り替えると 主拠点が休業でも skip されない (= bypass).

    シナリオ:
      - office TSUGA = 月-金 (Sat 休業), office INAGE = 月-土 (Sat 営業).
      - 患者 primary = TSUGA, sub_office = INAGE, weekly_pattern.entries に
        Sat 14:00 (= TSUGA 休業日) で PFV. sub_office_id を INAGE に設定.
      - mode = diff_add (= sub_office 経路が有効).
      - 期待: TSUGA の Sat が休業でも、 INAGE に振り替わって visit が emit される.
    """
    tsuga = await _make_office(
        db, name="g45-TSUGA-sub", code="TSUGA3", operating_weekdays=[0, 1, 2, 3, 4]
    )
    inage = await _make_office(
        db, name="g45-INAGE-sub", code="INAGE3", operating_weekdays=[0, 1, 2, 3, 4, 5]
    )
    ct_a_inage = await _make_course_template(db, office=inage, label="A")
    p = await _make_patient(
        db,
        code="G45-SUB-1",
        name="g45 sub patient",
        office=tsuga,
        weekly_pattern=None,
    )
    await _make_pfv(
        db,
        patient=p,
        weekday=5,  # Sat
        start_hhmm=(14, 0),
        is_pinned=True,
        course_template_id=ct_a_inage.id,
        sub_office_id=inage.id,
    )
    await _seed_staff_and_shifts(db, office=inage, weekdays=[5], name="g45-sub-inage-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[inage.id],  # diff_add で INAGE scope. sub_office_id=INAGE で引き込み.
        mode="diff_add",
    )
    after = result.get("after_visits") or []
    cross = [v for v in after if v.patient_id == p.id and v.weekday == 5]
    assert len(cross) == 1, f"sub_office 振替で INAGE に Sat visit が emit される期待: {cross}"
    assert cross[0].office_id == inage.id, (
        f"sub_office 振替後の visit.office_id は INAGE のはず: actual={cross[0].office_id}"
    )


@pytest.mark.asyncio
async def test_run_v2_pipeline_default_open_weekdays_matches_prior_behavior(db) -> None:
    """default (= 月-土) office では従来挙動 (= 全 weekday 営業) と一致する regression."""
    inage = await _make_office(
        db,
        name="g45-INAGE-default",
        code="INAGE-DEF",
        operating_weekdays=[0, 1, 2, 3, 4, 5],
    )
    p = await _make_patient(
        db,
        code="G45-DEF-1",
        name="g45 default",
        office=inage,
        weekly_pattern={
            "preferred_weekdays": ["Sat"],
            "preferred_start": "10:00",
            "time_type": "希望",
            "service_minutes": 30,
        },
    )
    await _seed_staff_and_shifts(db, office=inage, weekdays=[5], name="g45-def-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[inage.id],
        mode="full_optimize",
    )
    after = result.get("after_visits") or []
    cross = [v for v in after if v.patient_id == p.id and v.weekday == 5]
    assert len(cross) == 1, f"default 月-土 office では Sat visit が emit される期待: {after}"
    warnings = result.get("warnings") or []
    closed = [w for w in warnings if w.type == "office_closed"]
    assert closed == [], f"営業日には office_closed warning が出ないはず: {closed}"


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_skips_closed_weekday(db) -> None:
    """``reset_visits_to_fixed`` で 休業日 PFV の visit 再生成が skip される."""
    tsuga = await _make_office(
        db, name="g45-TSUGA-reset", code="TSUGAR", operating_weekdays=[0, 1, 2, 3, 4]
    )
    ct_a = await _make_course_template(db, office=tsuga, label="A")
    p = await _make_patient(
        db, code="G45-RESET-1", name="g45 reset", office=tsuga, weekly_pattern=None
    )
    await _make_pfv(
        db,
        patient=p,
        weekday=5,  # Sat = 都賀休業
        start_hhmm=(10, 0),
        is_pinned=True,
        course_template_id=ct_a.id,
    )
    await _make_pfv(
        db,
        patient=p,
        weekday=2,  # Wed = 都賀稼働
        start_hhmm=(10, 0),
        is_pinned=True,
        course_template_id=ct_a.id,
    )
    await _seed_staff_and_shifts(db, office=tsuga, weekdays=[2, 5], name="g45-reset-st1")
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[tsuga.id],
        mode="legacy",
        dry_run=True,  # DB 書込みなしで件数だけ確認.
    )
    # dry_run のため visits_to_insert が件数. Sat は skip され Wed の 1 件のみ.
    assert result.get("visits_to_insert") == 1, f"reset で Sat skip + Wed 1 件のはず: {result}"
    warnings = result.get("warnings") or []
    assert any("休業日" in w and "土曜" in w for w in warnings), (
        f"reset で 土曜 休業 warning が出るはず: {warnings}"
    )
