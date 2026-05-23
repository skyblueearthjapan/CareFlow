"""Phase G-33: After 側 (build_visits_for_pool) で cross-office PFV の
``V2Visit.office_id`` を ``course_template.office_id`` 由来にする.

背景:
    Phase G-24 で **Before 側** (``_load_before_visits_from_pfv``) を fix:
    cross-office PFV (= 患者の primary_office_id と異なる office の
    course_template を指す PFV) は V2Visit.office_id を
    ``course_template.office_id`` から取るように修正した. これにより Before
    の Course グループは (template_office, weekday, course_code) で正しく
    集計される.

    しかし **After 側** (``build_visits_for_pool``) で同じ修正が漏れていた.
    User 報告 (Phase G-33 driver):
        全面最適化の After で 「都賀A コース が消えて 稲毛A に合体」.
        以前 (G-24) Before で起きていた現象が今度は After で発生.

    根本原因:
        ``build_visits_for_pool`` の V2Visit 構築で
        ``office_id=patient.primary_office_id`` (= 稲毛) を渡している箇所が
        2 つ存在:
          - fixed-source 分岐 (use_fixed_as_source=True, orphan PFV 経路)
          - weekly_pattern 分岐 pinned PFV emit (= G-30.1 で追加した箇所)

        cross-office PFV の場合 PFV.course_template_id → CourseTemplate.office_id
        を取得して使うべき.

Phase G-33 修正:
    1. ``build_visits_for_pool`` に ``ct_office_by_id: dict[UUID, UUID] | None``
       引数を追加 (デフォルト None で既存呼び出しは破壊しない).
    2. fixed-source 分岐: ``wd_to_course_office_id`` を fixed_rows から事前構築し、
       office_id_eff の precedence を
       ``sub_office_id (E-5) > course_template.office_id (G-33) > patient.primary_office_id``
       にする.
    3. weekly_pattern 分岐 pinned PFV emit: pinned_pfv.course_template_id →
       ct_office_by_id で差し替え.
    4. ``run_v2_pipeline`` 側で CourseTemplate ロード (G-31 の既存 query を拡張)
       して ``ct_office_by_id`` map を ``build_visits_for_pool`` に渡す.

検証観点:
    1. 核心: cross-office pinned PFV (患者 primary=INAGE, PFV template=TSUGA)
       で after_visit.office_id == TSUGA (= G-24 After 側適用).
    2. regression: same-office pinned PFV (患者 primary=INAGE, PFV template=INAGE)
       で after_visit.office_id == INAGE (= 既存挙動維持).
    3. regression: PFV 無し weekly_pattern のみで V2Visit.office_id ==
       patient.primary_office_id (= 既存挙動).

G-21 経路 (= build_visits_for_pool_v2) は本タスク対象外.
"""

from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from app.models import Office, Patient
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    build_visits_for_pool,
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers (G-30 / G-31 と同じ規約)
# ---------------------------------------------------------------------------


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
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
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _make_course_template(
    db, *, office: Office, label: str, capacity_mon: int = 6
) -> CourseTemplate:
    ct = CourseTemplate(
        office_id=office.id,
        label=label,
        capacity_mon=capacity_mon,
    )
    db.add(ct)
    await db.flush()
    return ct


async def _seed_staff_and_shift(db, *, office: Office, weekday: int, name: str) -> Staff:
    s = Staff(
        name=name,
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=weekday, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# 1) 核心: cross-office pinned PFV で after_visit.office_id が
#    course_template.office_id (= G-33 修正対象) になる.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_office_pinned_pfv_uses_template_office_id(db) -> None:
    """cross-office pinned PFV の After で V2Visit.office_id が
    course_template.office_id 由来になる (= G-33 fix の core).

    シナリオ:
        - office INAGE (= 患者の primary_office) と office TSUGA を作成.
        - TSUGA に course_template "A" (= TSUGA-A) を作成.
        - patient.primary_office_id = INAGE.
        - PFV: course_template_id = TSUGA-A.id, is_pinned=True.
        - 両 office にスタッフ + シフトを置き run_v2_pipeline (full_optimize).
        - 期待: 当該 patient の after_visit.office_id == TSUGA.id
                (= 稲毛ではない / G-24 After 側適用).
        - 期待: after_visit.course_code == "A" (= TSUGA-A の label / G-31 規約).

    Phase G-33 修正前 (sabotage):
        build_visits_for_pool が office_id=patient.primary_office_id (= INAGE)
        を渡すため、 After Course グループは (INAGE, Mon, "A") に集計され
        TSUGA-A 行が消えて INAGE-A に合体する.
    """
    office_inage = await _make_office(db, name="g33-INAGE")
    office_tsuga = await _make_office(db, name="g33-TSUGA")
    ct_tsuga_a = await _make_course_template(db, office=office_tsuga, label="A", capacity_mon=6)

    p_cross = await _make_patient(
        db,
        code="G33-CROSS",
        name="g33 cross-office patient",
        office=office_inage,  # primary = INAGE
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_cross,
        weekday=0,
        start_hhmm=(14, 0),
        is_pinned=True,
        duration_min=30,
        course_template_id=ct_tsuga_a.id,  # cross-office: TSUGA template
    )
    # 両 office にスタッフを配置 (= TSUGA バケットに V2Visit が振り分け可能).
    await _seed_staff_and_shift(db, office=office_inage, weekday=0, name="g33-inage-st1")
    await _seed_staff_and_shift(db, office=office_tsuga, weekday=0, name="g33-tsuga-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office_inage.id, office_tsuga.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    cross_after = [v for v in after_visits if v.patient_id == p_cross.id and v.weekday == 0]
    assert len(cross_after) == 1, (
        f"G-33: cross-office pinned visit が after_visits に 1 件期待だが "
        f"{len(cross_after)} 件: {cross_after}"
    )
    assert cross_after[0].office_id == office_tsuga.id, (
        "G-33 (load-bearing): cross-office pinned PFV の V2Visit.office_id は "
        f"course_template.office_id (= TSUGA={office_tsuga.id}) のはず. "
        f"patient.primary_office_id (= INAGE={office_inage.id}) を返している場合は "
        f"build_visits_for_pool で ct_office_by_id 差し替えが効いていない: "
        f"actual={cross_after[0].office_id}"
    )
    assert cross_after[0].course_code == "A", (
        "G-33 regression (G-31 規約): cross-office pinned PFV の course_code は "
        f"course_template.label (= 'A') のはず: actual={cross_after[0].course_code}"
    )
    assert cross_after[0].is_pinned is True, (
        "G-33 regression (G-30 規約): pinned PFV 由来の V2Visit は is_pinned=True"
    )


# ---------------------------------------------------------------------------
# 2) regression: same-office pinned PFV で V2Visit.office_id ==
#    patient.primary_office_id (= 既存挙動維持).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_office_pinned_pfv_keeps_patient_office(db) -> None:
    """same-office pinned PFV (= 患者 primary_office と PFV template office が
    一致) では V2Visit.office_id が patient.primary_office_id のまま (= 既存挙動).

    シナリオ:
        - office INAGE のみ.
        - INAGE に course_template "A" (= INAGE-A) を作成.
        - patient.primary_office_id = INAGE.
        - PFV: course_template_id = INAGE-A.id, is_pinned=True.
        - 期待: after_visit.office_id == INAGE.id (= patient.primary と一致).
    """
    office_inage = await _make_office(db, name="g33-2-INAGE")
    ct_inage_a = await _make_course_template(db, office=office_inage, label="A", capacity_mon=6)

    p_same = await _make_patient(
        db,
        code="G33-SAME",
        name="g33 same-office patient",
        office=office_inage,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_same,
        weekday=0,
        start_hhmm=(14, 0),
        is_pinned=True,
        duration_min=30,
        course_template_id=ct_inage_a.id,  # same-office
    )
    await _seed_staff_and_shift(db, office=office_inage, weekday=0, name="g33-2-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office_inage.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    same_after = [v for v in after_visits if v.patient_id == p_same.id and v.weekday == 0]
    assert len(same_after) == 1, (
        f"G-33 #2: same-office pinned visit が after_visits に 1 件期待だが "
        f"{len(same_after)} 件: {same_after}"
    )
    assert same_after[0].office_id == office_inage.id, (
        "G-33 regression: same-office pinned PFV (patient primary と PFV template "
        f"office が一致) では V2Visit.office_id == patient.primary_office_id (= "
        f"INAGE={office_inage.id}) のはず: actual={same_after[0].office_id}"
    )


# ---------------------------------------------------------------------------
# 3) regression: PFV 無し weekly_pattern のみで V2Visit.office_id ==
#    patient.primary_office_id (= 既存挙動).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pfv_uses_patient_primary_office(db) -> None:
    """PFV 無し (weekly_pattern entry のみ) では V2Visit.office_id が
    patient.primary_office_id のまま (= G-33 修正が non-pinned 経路に影響しない).

    シナリオ:
        - office INAGE のみ.
        - patient.weekly_pattern = {preferred_weekdays:[Mon], preferred_start:14:00}.
        - PFV は作らない.
        - 期待: after_visit.office_id == INAGE.id, is_pinned=False.
    """
    office_inage = await _make_office(db, name="g33-3-INAGE")

    p_nopfv = await _make_patient(
        db,
        code="G33-NOPFV",
        name="g33 no-pfv patient",
        office=office_inage,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "希望",
        },
    )
    await _seed_staff_and_shift(db, office=office_inage, weekday=0, name="g33-3-st1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office_inage.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    nopfv_after = [v for v in after_visits if v.patient_id == p_nopfv.id and v.weekday == 0]
    assert len(nopfv_after) == 1, (
        f"G-33 #3: no-PFV weekly_pattern visit が after_visits に 1 件期待だが "
        f"{len(nopfv_after)} 件: {nopfv_after}"
    )
    assert nopfv_after[0].office_id == office_inage.id, (
        "G-33 regression: PFV 無し weekly_pattern のみでは V2Visit.office_id == "
        f"patient.primary_office_id (= INAGE={office_inage.id}) のはず "
        f"(G-33 修正が non-pinned 経路に副作用を出していないか): "
        f"actual={nopfv_after[0].office_id}"
    )
    assert nopfv_after[0].is_pinned is False, (
        "G-33 regression: PFV 無し weekly_pattern のみは is_pinned=False"
    )


# ---------------------------------------------------------------------------
# 4) unit (load-bearing): build_visits_for_pool 直接呼びで weekly_pattern 分岐
#    pinned emit が ct_office_by_id 経由で office_id を差し替える.
# ---------------------------------------------------------------------------


def test_build_visits_for_pool_pinned_pfv_cross_office_diverts_office_id() -> None:
    """build_visits_for_pool の weekly_pattern 分岐 pinned PFV emit で、
    ``PFV.course_template_id`` から引いた office_id が V2Visit.office_id に
    入る (= G-33 fix の core unit test).

    本 unit test は Stage 4 を経由せず ``build_visits_for_pool`` の戻り値を
    直接観測するため、 G-33 修正 (= ``pinned_course_office_id = ct_office_by_id.get(...)``
    + ``office_id=office_id_eff``) が無効化されると即 fail する load-bearing
    assertion になる.

    検証:
        - patient.primary_office_id = INAGE.
        - patient.weekly_pattern = {Mon, 14:00, 固定}.
        - PFV: weekday=0, start_time=14:00, is_pinned=True, course_template_id=TSUGA-A.
        - ct_office_by_id = {TSUGA-A.id: TSUGA.id}
        - 期待: build_visits_for_pool が返す Mon visit の office_id == TSUGA.id.
    """
    inage_office_id = uuid4()
    tsuga_office_id = uuid4()
    template_id_tsuga_a = uuid4()

    patient = Patient(
        code="G33-UNIT-CROSS",
        name="g33 unit cross-office patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=inage_office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    patient.id = uuid4()

    pfv_pinned = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(14, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=True,
        course_template_id=template_id_tsuga_a,
    )

    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv_pinned]},
        # use_fixed_as_source=False (デフォルト) で weekly_pattern 分岐に入る
        course_code_by_template_id={template_id_tsuga_a: "A"},
        ct_office_by_id={template_id_tsuga_a: tsuga_office_id},
    )
    mon = [v for v in visits if v.weekday == 0]
    assert len(mon) == 1, f"Mon visit が 1 件期待だが {len(mon)} 件: {mon}"
    assert mon[0].is_pinned is True, (
        "G-30.1 regression: weekly_pattern 分岐 pinned PFV emit で is_pinned=True が 乗っていない"
    )
    assert mon[0].course_code == "A", (
        "G-31 regression: weekly_pattern 分岐 pinned PFV emit で course_code='A' が "
        f"伝播していない: actual={mon[0].course_code}"
    )
    assert mon[0].office_id == tsuga_office_id, (
        "G-33 (load-bearing): weekly_pattern 分岐 pinned PFV emit で "
        f"PFV.course_template_id → office_id (= TSUGA={tsuga_office_id}) が V2Visit に "
        f"伝播していない (= G-33 fix が無効化されている可能性). "
        f"INAGE (= {inage_office_id}) を返している場合は ct_office_by_id 差し替えが "
        f"効いていない: actual={mon[0].office_id}"
    )


def test_build_visits_for_pool_fixed_source_cross_office_diverts_office_id() -> None:
    """build_visits_for_pool の fixed-source 分岐 (= orphan PFV 経路) でも
    cross-office 差し替えが効くこと (= G-33 fix のもう一つの分岐).

    検証:
        - patient.primary_office_id = INAGE.
        - PFV: weekday=0, start_time=09:00, is_pinned=False,
          course_template_id=TSUGA-B.
        - use_fixed_as_source=True で fixed-source 分岐に入る.
        - ct_office_by_id = {TSUGA-B.id: TSUGA.id}
        - 期待: visit.office_id == TSUGA.id (= patient.primary でない).
        - regression: visit.course_code == "B".
    """
    inage_office_id = uuid4()
    tsuga_office_id = uuid4()
    template_id_tsuga_b = uuid4()

    patient = Patient(
        code="G33-UNIT-FIXED",
        name="g33 unit fixed-source patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=inage_office_id,
        # fixed-source 分岐は weekly_pattern を見ないが、 None だと
        # _extract_fixed_visits_for_patient 周りで分岐が安定するよう dict にしておく.
        weekly_pattern={},
    )
    patient.id = uuid4()

    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=False,
        course_template_id=template_id_tsuga_b,
    )

    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv]},
        use_fixed_as_source=True,
        course_code_by_template_id={template_id_tsuga_b: "B"},
        ct_office_by_id={template_id_tsuga_b: tsuga_office_id},
    )
    mon = [v for v in visits if v.weekday == 0]
    assert len(mon) == 1, f"Mon visit が 1 件期待だが {len(mon)} 件: {mon}"
    assert mon[0].course_code == "B", (
        "G-33 regression (CareFlow #102 Fix A): fixed-source 分岐で course_code='B' が "
        f"埋まるはず: actual={mon[0].course_code}"
    )
    assert mon[0].office_id == tsuga_office_id, (
        "G-33 (load-bearing): fixed-source 分岐で PFV.course_template_id → office_id "
        f"(= TSUGA={tsuga_office_id}) が V2Visit に伝播していない (= G-33 fix が "
        "無効化されている可能性): "
        f"actual={mon[0].office_id}"
    )


def test_build_visits_for_pool_fixed_source_no_ct_office_keeps_primary() -> None:
    """regression: fixed-source 分岐で ct_office_by_id が None または entry 無しなら
    V2Visit.office_id は patient.primary_office_id のまま (= G-33 修正は明示的に
    template office を引けたときだけ差し替える).
    """
    inage_office_id = uuid4()
    template_id = uuid4()

    patient = Patient(
        code="G33-UNIT-FIXED-NULL",
        name="g33 unit fixed-source no-ct-office patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=inage_office_id,
        weekly_pattern={},
    )
    patient.id = uuid4()

    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=False,
        course_template_id=template_id,
    )

    # ct_office_by_id=None で呼ぶ (= 既存呼び出し互換).
    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv]},
        use_fixed_as_source=True,
        course_code_by_template_id={template_id: "B"},
        ct_office_by_id=None,
    )
    mon = [v for v in visits if v.weekday == 0]
    assert len(mon) == 1
    assert mon[0].office_id == inage_office_id, (
        "G-33 regression: ct_office_by_id=None なら V2Visit.office_id は "
        f"patient.primary_office_id (= INAGE={inage_office_id}) のはず: "
        f"actual={mon[0].office_id}"
    )

    # ct_office_by_id={} (= entry 無し) でも同じ挙動.
    visits2 = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv]},
        use_fixed_as_source=True,
        course_code_by_template_id={template_id: "B"},
        ct_office_by_id={},
    )
    mon2 = [v for v in visits2 if v.weekday == 0]
    assert len(mon2) == 1
    assert mon2[0].office_id == inage_office_id, (
        "G-33 regression: ct_office_by_id={} なら V2Visit.office_id は "
        f"patient.primary_office_id (= INAGE={inage_office_id}) のはず: "
        f"actual={mon2[0].office_id}"
    )
