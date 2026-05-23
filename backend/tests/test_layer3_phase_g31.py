"""Phase G-31: pinned PFV emit で course_code を course_template_id から伝播する.

背景:
    Phase G-30.1 で weekly_pattern + pinned PFV の **時刻 (start_time)** は
    pinned 固定された (= ``apply_travel_corrections`` post-restore で PFV 値に
    戻る). しかし VPS 検証で次の事象が残っていた:

        BEFORE [14:00 A pinned=True] → AFTER [14:00 M pinned=True]
        時刻 OK だが course_code が動く (A → M).

    根本原因:
        ``build_visits_for_pool`` の **weekly_pattern 分岐 pinned PFV emit**
        (auto_allocator_v2.py:1830-1853) で V2Visit を作る際に ``course_code``
        を渡していなかった (= デフォルト None). 後段 Stage 4 (course 振り分け)
        が None 候補を見て idx ベース付番を行い、他 set との衝突回避で別 course
        (M overflow 等) にアサインされていた.

    対比: 同ファイル fixed-source 分岐 (L1716-1721) は
        ``course_code_by_template_id.get(_row.course_template_id)`` で
        course_code を埋めている. weekly_pattern 分岐 pinned emit も同じ規約
        にする.

Phase G-31 修正:
    1. ``build_visits_for_pool`` weekly_pattern 分岐 pinned emit で
       ``cc_eff = course_code_by_template_id.get(pinned_pfv.course_template_id)``
       を計算し、 ``V2Visit(course_code=cc_eff, ...)`` で渡す.
    2. ``run_v2_pipeline`` の pool 呼び出し側で ``course_code_by_template_id``
       を事前ロード (CourseTemplate ロード) して ``build_visits_for_pool`` に
       渡す (= orphan 経路 / G-21 経路と同じ pre-load 規約).

検証観点:
    pinned PFV が ``course_template_id`` を持つとき、 after_visit.course_code が
    その template の label と一致する.
"""

from __future__ import annotations

from datetime import time

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
# Helpers (g30 と同じ規約)
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
# 1) 核心: legacy 経路 weekly_pattern + pinned PFV で course_code が保たれる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_pinned_pfv_keeps_course_code(db) -> None:
    """legacy 経路 (= canary OFF) で weekly_pattern + pinned PFV が
    ``course_template_id`` を持つとき、 after_visit.course_code が その
    template の label と一致する (= Stage 4 で別 course に動かない).

    シナリオ:
        - office に course_template "A" を作成.
        - patient.weekly_pattern.entries = [{Mon, 14:00, 固定}].
        - PFV: weekday=0, start_time=14:00, is_pinned=True,
          course_template_id = A の template id.
        - 同 office にスタッフを複数置き、 Stage 4 で M overflow も理屈上
          発生可能な状況にする.
        - 期待: after_visit.course_code == "A".

    Phase G-31 修正前 (sabotage):
        weekly_pattern 分岐 pinned emit で course_code=None のまま V2Visit を
        作るため、 Stage 4 の idx ベース付番が走り、 他 set と衝突した場合に
        M / M2 等の overflow course に動く可能性あり.

    Phase G-31 修正後:
        course_code="A" で emit → Stage 4 の existing_codes 経路が候補を採用 →
        他 set 衝突時は他 set 側を別 course に動かす → pinned visit は "A" を保つ.
    """
    office = await _make_office(db, name="g31-1-office")
    ct_a = await _make_course_template(db, office=office, label="A", capacity_mon=6)

    p_pinned = await _make_patient(
        db,
        code="G31-PIN",
        name="g31 pinned patient",
        office=office,
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
        patient=p_pinned,
        weekday=0,
        start_hhmm=(14, 0),
        is_pinned=True,
        duration_min=30,
        course_template_id=ct_a.id,
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g31-staff1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    pinned_after = [v for v in after_visits if v.patient_id == p_pinned.id and v.weekday == 0]
    assert len(pinned_after) == 1, (
        f"G-31: pinned visit が after_visits に 1 件期待だが {len(pinned_after)} 件: {pinned_after}"
    )
    assert pinned_after[0].is_pinned is True, (
        "G-31 regression: G-30.1 で立てた is_pinned=True が失われている"
    )
    assert pinned_after[0].start_time == time(14, 0), (
        "G-31 regression: G-30.1 で固定した start_time=14:00 が動いている: "
        f"actual={pinned_after[0].start_time}"
    )
    assert pinned_after[0].course_code == "A", (
        f"G-31: PFV.course_template_id が指す course_code='A' が保たれるべき "
        f"(Stage 4 で別 course に動いた可能性あり): actual={pinned_after[0].course_code}"
    )


# ---------------------------------------------------------------------------
# 2) unit (load-bearing): build_visits_for_pool 直接呼びで weekly_pattern
#    分岐 pinned emit が V2Visit.course_code に template_id 由来の値を流す.
# ---------------------------------------------------------------------------


def test_build_visits_for_pool_pinned_pfv_propagates_course_code_from_template() -> None:
    """build_visits_for_pool の weekly_pattern 分岐 pinned PFV emit で、
    ``PFV.course_template_id`` から引いた course_code が V2Visit.course_code に
    入る (= G-31 fix の core).

    本 unit test は Stage 4 を経由せず ``build_visits_for_pool`` の戻り値を
    直接観測するため、 G-31 修正 (= ``cc_eff = course_code_by_template_id.get(...)``
    + ``V2Visit(course_code=cc_eff, ...)``) が無効化されると即 fail する
    load-bearing assertion になる.

    検証:
        - patient.weekly_pattern = {preferred_weekdays:[Mon], preferred_start:14:00}
        - PFV: weekday=0, start_time=14:00, is_pinned=True,
          course_template_id = template_id_a
        - course_code_by_template_id = {template_id_a: "A"}
        - 期待: build_visits_for_pool が返す Mon visit の course_code == "A".
    """
    from uuid import uuid4

    office_id = uuid4()
    template_id_a = uuid4()

    patient = Patient(
        code="G31-UNIT",
        name="g31 unit patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
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
        course_template_id=template_id_a,
    )

    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv_pinned]},
        # use_fixed_as_source=False (デフォルト) で weekly_pattern 分岐に入る
        course_code_by_template_id={template_id_a: "A"},
    )
    mon = [v for v in visits if v.weekday == 0]
    assert len(mon) == 1, f"Mon visit が 1 件期待だが {len(mon)} 件: {mon}"
    assert mon[0].is_pinned is True, (
        "G-30.1 regression: weekly_pattern 分岐 pinned PFV emit で is_pinned=True が乗っていない"
    )
    assert mon[0].start_time == time(14, 0), (
        "G-30.1 regression: weekly_pattern 分岐 pinned PFV emit で PFV.start_time=14:00 が "
        f"採用されていない: actual={mon[0].start_time}"
    )
    assert mon[0].course_code == "A", (
        "G-31 (load-bearing): weekly_pattern 分岐 pinned PFV emit で "
        "PFV.course_template_id → course_code='A' が V2Visit に伝播していない "
        f"(= G-31 fix が無効化されている可能性): actual={mon[0].course_code}"
    )


def test_build_visits_for_pool_pinned_pfv_without_template_keeps_none() -> None:
    """regression: PFV.course_template_id=None の場合は V2Visit.course_code=None
    のまま (= G-31 修正は course_template_id がある時だけ動く).
    """
    from uuid import uuid4

    office_id = uuid4()

    patient = Patient(
        code="G31-UNIT-NULL",
        name="g31 unit no-template patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    patient.id = uuid4()

    pfv_pinned_no_template = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(14, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=True,
        course_template_id=None,  # template 未指定
    )

    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv_pinned_no_template]},
        course_code_by_template_id={},
    )
    mon = [v for v in visits if v.weekday == 0]
    assert len(mon) == 1
    assert mon[0].is_pinned is True
    assert mon[0].course_code is None, (
        f"G-31 regression: course_template_id=None なら course_code=None のままのはず: "
        f"actual={mon[0].course_code}"
    )
