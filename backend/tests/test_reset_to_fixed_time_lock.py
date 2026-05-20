"""Phase G-10: reset_visits_to_fixed は PFV.start_time を厳守する.

User シートでは PFV = 固定時刻 (= 動かない) のはず.
しかし patient.weekly_pattern.time_type が "時間帯" の場合、 旧実装は
``_extract_time_type_for_weekday`` で取得した "時間帯" を V2Visit.time_type に
セットしていたため、 apply_travel_corrections が preferred_start-preferred_end
範囲内で自由配置 / 時刻 shift してしまう bug があった.

本テストは reset_visits_to_fixed 実行後、 visit.start_time が PFV.start_time
と完全一致することを担保する (= shift しない).
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import Visit
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed


@pytest.mark.asyncio
async def test_reset_to_fixed_locks_time_for_jikantai_patient(db) -> None:
    """time_type='時間帯' の患者でも reset_visits_to_fixed は PFV.start_time を厳守する.

    Setup:
      - patient A: weekly_pattern.time_type='時間帯', preferred 09:00-17:00,
        PFV.start_time=10:00, duration=35 min.
    Assert:
      - reset 後の visit.start_time = 10:00 (= 動いていない).
    """
    office = Office(name="g10-office-a")
    db.add(office)
    await db.flush()
    pa = Patient(
        code="G10A",
        name="g10 patient A",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    db.add(pa)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=pa.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
        )
    )
    s = Staff(name="g10-staff-a", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()

    assert result["visits_regenerated"] >= 1

    visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pa.id,
                Visit.visit_date == date(2026, 5, 11),  # Mon of W20
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(visits) == 1, f"patient A の visit が 1 件生成されるはず: {visits}"
    v = visits[0]
    assert v.start_time == time(10, 0), (
        f"PFV.start_time=10:00 だが visit.start_time={v.start_time} に shift された "
        f"(= 時間帯 patient が apply_travel_corrections で動かされている bug)"
    )
    # end_time も 10:00 + 35 分 = 10:35 のまま.
    assert v.end_time == time(10, 35), f"end_time={v.end_time} (expected 10:35)"


@pytest.mark.asyncio
async def test_reset_to_fixed_locks_time_for_two_jikantai_patients(db) -> None:
    """別住所 / 別時刻の 2 患者 (両方とも time_type='時間帯') が reset 後それぞれ
    PFV.start_time を維持することを確認.

    Setup:
      - patient A: 10:00 PFV, 時間帯 09:00-17:00.
      - patient B: 15:00 PFV, 時間帯 09:00-17:00. (別住所)
    Assert:
      - A.visit.start_time = 10:00, B.visit.start_time = 15:00 (= 両方とも shift なし).
    """
    office = Office(name="g10-office-ab")
    db.add(office)
    await db.flush()
    pa = Patient(
        code="G10AA",
        name="g10 patient A",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    pb = Patient(
        code="G10BB",
        name="g10 patient B",
        status="active",
        lat=35.70,  # 別住所
        lng=140.15,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    db.add_all([pa, pb])
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=pa.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=pb.id,
            mode="normal",
            weekday=0,
            start_time=time(15, 0),
            duration_min=35,
            slot_index=0,
        )
    )
    s = Staff(name="g10-staff-ab", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()
    assert result["visits_regenerated"] >= 2

    va = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pa.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    vb = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pb.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(va) == 1 and len(vb) == 1
    assert va[0].start_time == time(10, 0), (
        f"patient A: PFV=10:00 だが visit={va[0].start_time} に shift された"
    )
    assert vb[0].start_time == time(15, 0), (
        f"patient B: PFV=15:00 だが visit={vb[0].start_time} に shift された"
    )
