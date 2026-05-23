"""Phase G-32: Stage 5 course_code 上書きで pinned visit が動かないことを担保.

背景:
    Phase G-31 で ``build_visits_for_pool`` の weekly_pattern + pinned PFV emit が
    ``course_template_id`` を引いて V2Visit.course_code に正しく入るようにした.
    しかし VPS 検証で残った事象:

        BEFORE [14:00 A pinned=True] → AFTER [14:00 M pinned=True]

    時刻 / is_pinned は維持されているが、 course_code が 'A' → 'M' に変化する.

    根本原因:
        ``run_v2_pipeline`` Stage 5 (auto_allocator_v2.py:6437- 周辺) で
        ``(office_id, weekday)`` ごとに am_set / pm_set を combine して
        course_code を発番している. ここでは既存 ``existing_codes``
        (= pinned PFV 由来 course) を尊重するロジックがあるが、

          - candidate code が他 set と衝突 (= ``candidate in assigned_codes``) →
            ``fallback`` で別 code に振り替わる経路
          - ``_find_next_available_code`` が None を返す経路 (空き code 不足)
          - manager 数超過で本 set 自体に code を割当てられない経路

        のいずれでも `for v in am_set.visits if am_set else []: v.course_code = code`
        が pinned visit の course_code を **無条件で書き換える**. snapshot は
        ``_apply_corrections_to_visits`` (= Stage 5 後) で取られるため、 既に
        ここで M に書き換わった後の snapshot を post-restore しても M のまま.

    Phase G-32 修正:
        Stage 5 の course_code 書き戻し 3 箇所 (主経路 / fallback unassigned /
        manager 超過 unassigned) に ``if v.is_pinned: continue`` を追加し、
        pinned visit は元 course_code を絶対動かさない.

検証観点:
    1. pinned PFV (course='A') を持つ患者と course='B'/'C' に振り分けたい
       他患者群を seed して run_v2_pipeline を回す. pinned の after_visit が
       'A' を維持する.
    2. 同住所 pinned ペア (両方 course='A') が 'A' のまま揃う.
    3. 同住所 pinned + non-pinned ペアで pinned 側が 'A' を維持する.
"""

from __future__ import annotations

from datetime import time

import pytest

from app.models import Office, Patient
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers (g30 / g31 と同じ規約)
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
# 1) 核心: pinned visit の course_code が Stage 5 clustering で動かない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_visit_course_code_not_overwritten_by_clustering(db) -> None:
    """pinned PFV (course='A') を持つ patient と、 PFV を持たず weekly_pattern のみで
    別 set に配置される異住所 patient を seed し、 Stage 5 で normal_course_limit=1
    の中で 'A' が他 set に取られた後の candidate fallback path で pinned が
    'A' から 'M' (= manager overflow) に上書きされないことを担保する.

    シナリオ (= 本番 VPS で観測された A→M バグの最小再現):
        - office に course_template "A" のみ作成.
        - p_pinned: 住所 X + Mon 14:00 PFV pinned + course_template = A.
        - p_other1, p_other2: 住所 Y (= X から十分離れる) + 同じ住所同士で
          weekly_pattern Mon 14:00 (PFV なし → V2Visit.course_code=None,
          V2Visit.is_pinned=False).
        - staff 1 名 (= normal_course_limit=1, 'A' しか出ない).
        - manager 1 名 (= m_overflow_limit=1, 'M' で fallback 可能).

        cluster_by_distance_greedy は p_other1+p_other2 を 1 set (近接) にまとめ、
        p_pinned を別 set にする (= 2 set).

        Stage 5: 1 つ目の set が processed.
          - p_other set: existing_codes={} → idx-based で 'A' を取る → assigned_codes={'A'}.
          - p_pinned set: existing_codes={'A'} → candidate='A' だが
            ``'A' in assigned_codes`` で fallback. normal_max=1 で B 以降は使えず、
            ``_find_next_available_code`` が 'M' を返す → code='M'.
          - G-32 fix なしだと pinned visit が 'A' → 'M' に上書きされる.

    期待:
        - p_pinned.after_visit.course_code == 'A' を維持.
        - is_pinned=True / start_time=14:00 を維持.

    Sabotage (= Phase G-32 fix の主経路を無効化):
        auto_allocator_v2.py Stage 5 主経路 (line ~6675 周辺) の
        ``if v.is_pinned: continue`` を取り除くと、 candidate fallback で pinned visit が
        'A' → 'M' に上書きされ、 本 test が fail する (= load-bearing).
    """
    office = await _make_office(db, name="g32-1-office")
    ct_a = await _make_course_template(db, office=office, label="A", capacity_mon=6)

    p_pinned = await _make_patient(
        db,
        code="G32-PIN",
        name="g32 pinned patient",
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
    # 同住所ペア (= 必ず 1 set にまとまる) を p_pinned から離して置く.
    # cluster_by_distance_greedy は最も近い 2 名を最初の seed にするため、 同住所
    # 2 名 (距離 0) が seed されて 1 set になり、 p_pinned が別 set になる.
    await _make_patient(
        db,
        code="G32-OTH1",
        name="g32 other 1",
        office=office,
        lat=35.80,
        lng=140.30,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    await _make_patient(
        db,
        code="G32-OTH2",
        name="g32 other 2",
        office=office,
        lat=35.80,
        lng=140.30,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    # staff 1 名 (= normal_course_limit=1, 'A' しか発番できない).
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g32-staff1")
    # manager 1 名 (= 'M' に fallback できる = バグの A→M 再現条件).
    s_mgr = Staff(
        name="g32-mgr1",
        role="manager",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s_mgr)
    await db.flush()
    db.add(StaffShift(staff_id=s_mgr.id, weekday=0, is_on=True))
    await db.flush()
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
        f"G-32: pinned visit が after_visits に 1 件期待だが {len(pinned_after)} 件: {pinned_after}"
    )
    assert pinned_after[0].is_pinned is True, (
        "G-32 regression: G-30/G-31 で立てた is_pinned=True が失われている"
    )
    assert pinned_after[0].start_time == time(14, 0), (
        "G-32 regression: G-30.1 で固定した start_time=14:00 が動いている: "
        f"actual={pinned_after[0].start_time}"
    )
    assert pinned_after[0].course_code == "A", (
        f"G-32 (load-bearing): pinned PFV の course='A' が Stage 5 candidate "
        f"fallback で別 code (= 'M' 等) に上書きされた "
        f"(= G-32 fix が無効化されている可能性): "
        f"actual={pinned_after[0].course_code}"
    )


# ---------------------------------------------------------------------------
# 2) 同住所 pinned ペア (両方 course='A') が 'A' のまま揃う
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_visit_pair_keeps_course_codes_when_matching(db) -> None:
    """同住所 + 同時刻 + 両方 pinned + 両方 course='A' のペアは、 Stage 5 後も
    両方 course='A' のまま維持される (= 一致なので衝突せず、 既存 pinned 規約で
    candidate='A' が採用される).
    """
    office = await _make_office(db, name="g32-2-office")
    ct_a = await _make_course_template(db, office=office, label="A", capacity_mon=6)

    p_a1 = await _make_patient(
        db,
        code="G32-PAIR-A1",
        name="g32 pair a1",
        office=office,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    p_a2 = await _make_patient(
        db,
        code="G32-PAIR-A2",
        name="g32 pair a2",
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
        patient=p_a1,
        weekday=0,
        start_hhmm=(14, 0),
        is_pinned=True,
        duration_min=30,
        course_template_id=ct_a.id,
    )
    await _make_pfv(
        db,
        patient=p_a2,
        weekday=0,
        start_hhmm=(14, 0),
        is_pinned=True,
        duration_min=30,
        course_template_id=ct_a.id,
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g32-pair-staff1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    a1_after = [v for v in after_visits if v.patient_id == p_a1.id and v.weekday == 0]
    a2_after = [v for v in after_visits if v.patient_id == p_a2.id and v.weekday == 0]
    assert len(a1_after) == 1 and len(a2_after) == 1, (
        f"G-32: 同住所 pinned ペアが両方 after に 1 件ずつ期待: a1={a1_after}, a2={a2_after}"
    )
    assert a1_after[0].course_code == "A", (
        f"G-32: 同住所 pinned ペア (両方 'A') の a1 が 'A' を維持すべき: "
        f"actual={a1_after[0].course_code}"
    )
    assert a2_after[0].course_code == "A", (
        f"G-32: 同住所 pinned ペア (両方 'A') の a2 が 'A' を維持すべき: "
        f"actual={a2_after[0].course_code}"
    )
    assert a1_after[0].is_pinned is True
    assert a2_after[0].is_pinned is True


# ---------------------------------------------------------------------------
# 3) 同住所 pinned + non-pinned ペアで pinned 側が 'A' を維持する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_visit_pair_keeps_pinned_course_when_one_unpinned(db) -> None:
    """同住所 + 同時刻 ペアで:
        - p_pinned: PFV pinned + course='A'.
        - p_unpinned: weekly_pattern のみ (= PFV なし、 V2Visit.is_pinned=False).
    の場合、 Stage 5 後に pinned 側は 'A' を維持し、 unpinned 側は (clustering 結果
    次第で) 'A' に追従するか別 course になるかいずれか. pinned 側の 'A' 不動が
    本テストの核心.
    """
    office = await _make_office(db, name="g32-3-office")
    ct_a = await _make_course_template(db, office=office, label="A", capacity_mon=6)

    p_pinned = await _make_patient(
        db,
        code="G32-MIX-PIN",
        name="g32 mix pinned",
        office=office,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    p_unpinned = await _make_patient(
        db,
        code="G32-MIX-UNP",
        name="g32 mix unpinned",
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
    # p_unpinned は PFV を作らない (= weekly_pattern のみで V2Visit.is_pinned=False)
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g32-mix-staff1")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    pin_after = [v for v in after_visits if v.patient_id == p_pinned.id and v.weekday == 0]
    unp_after = [v for v in after_visits if v.patient_id == p_unpinned.id and v.weekday == 0]
    assert len(pin_after) == 1, f"G-32: pinned visit が after に 1 件期待: {pin_after}"
    assert pin_after[0].course_code == "A", (
        f"G-32: pinned + non-pinned 同住所ペアでも pinned 側は 'A' 維持: "
        f"actual={pin_after[0].course_code}"
    )
    assert pin_after[0].is_pinned is True
    assert pin_after[0].start_time == time(14, 0)
    # unpinned 側は 'A' に合わせるか別 course か (= 制約しない); 但し
    # after_visits に存在することは確認 (= 未割当に流れていないこと).
    assert len(unp_after) == 1, (
        f"G-32: non-pinned 同住所相手も after に 1 件期待 (= 未割当に流れていない): {unp_after}"
    )
