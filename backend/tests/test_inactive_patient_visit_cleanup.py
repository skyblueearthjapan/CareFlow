"""Phase G-13: 一時休止 (status='inactive') 患者の既存 visits が、
reset_visits_to_fixed / apply_week_only / run_v2_pipeline の 3 経路で適切に
自動 soft-delete されることを検証する.

背景:
    `_load_active_patients` は status='active' のみを返すため、 旧実装では
    visit 削除 SQL のフィルタも active 患者のみで絞っていた. その結果、
    inactive (一時休止) 患者の既存 visits が DB に残ったまま画面に出続け、
    User の意図 「一時休止 → スケジュールから消える」 と乖離していた.

修正:
    visit 削除対象を `_load_visit_delete_target_patient_ids` (active + inactive,
    deleted_at IS NULL) に拡張. 新規 visit の生成 / 提案は引き続き active のみ.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator_v2 import (
    apply_week_only,
    reset_visits_to_fixed,
    run_v2_pipeline,
)


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_soft_deletes_inactive_patient_visit(db) -> None:
    """ケース 1: reset_visits_to_fixed が inactive 患者の既存 visit を soft-delete する.

    Setup:
        - 拠点 X
        - patient A (status='active', PFV: 月 10:00, 35 min)
        - patient B (status='inactive', 既存の月 11:00 visit が DB に残っている)
        - staff (月曜 出勤)
    Assert:
        - patient A の visit が新規生成されている (10:00, source='reset_v2')
        - **patient B の既存 visit が soft-delete されている**
          (deleted_at IS NOT NULL)
        - patient B の新規 visit は生成されていない
    """
    office = Office(name="g13-r-office")
    db.add(office)
    await db.flush()

    pa = Patient(
        code="G13RA",
        name="g13 reset patient A (active)",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    pb = Patient(
        code="G13RB",
        name="g13 reset patient B (inactive)",
        status="inactive",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
    )
    db.add_all([pa, pb])
    await db.flush()

    # A の PFV (active 患者のみ生成対象).
    db.add(
        PatientFixedVisit(
            patient_id=pa.id,
            mode="normal",
            weekday=0,  # Mon
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
        )
    )

    # B の既存 visit (= inactive にする前に auto_alloc で生成された残骸).
    week_monday = date(2026, 5, 11)  # ISO 2026-W20 Mon
    stale_visit = Visit(
        patient_id=pb.id,
        visit_date=week_monday,
        start_time=time(11, 0),
        end_time=time(11, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc_v2",
        required_staff_count=1,
    )
    db.add(stale_visit)

    staff = Staff(
        name="g13-r-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.commit()
    stale_id = stale_visit.id

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
    )
    await db.commit()

    # A は新規生成されている.
    a_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pa.id,
                Visit.visit_date == week_monday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(a_visits) == 1, f"patient A の visit が 1 件生成されているはず: {a_visits}"
    assert a_visits[0].start_time == time(10, 0)
    assert a_visits[0].source == "reset_v2"

    # B の既存 visit は soft-delete されている.
    await db.refresh(stale_visit)
    assert stale_visit.deleted_at is not None, (
        "Phase G-13: inactive 患者 B の既存 visit が soft-delete されていない (旧 bug 再発)"
    )

    # B の新規 visit (active visit) は存在しない.
    b_actives = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pb.id,
                Visit.visit_date == week_monday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert b_actives == [], f"inactive 患者 B の新規 visit が誤って生成された: {b_actives}"

    # soft-delete カウントに含まれている.
    assert result["visits_soft_deleted"] >= 1
    # 削除された visit ID = stale_visit.id.
    assert stale_id == stale_visit.id


@pytest.mark.asyncio
async def test_apply_week_only_soft_deletes_inactive_patient_visit(db) -> None:
    """ケース 2: apply_week_only が inactive 患者の既存 visit を soft-delete する.

    Setup:
        - 拠点 X
        - patient A (status='active') — visit_plans 経由で月 10:00 を申請
        - patient B (status='inactive') — 既存の月 11:00 visit が DB に残存
    実行:
        - apply_week_only(visit_plans=[A の plan のみ])
    Assert:
        - patient B の既存 visit が soft-delete されている
        - patient A の新規 visit が INSERT されている
    """
    office = Office(name="g13-a-office")
    db.add(office)
    await db.flush()

    pa = Patient(
        code="G13AA",
        name="g13 apply patient A (active)",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    pb = Patient(
        code="G13AB",
        name="g13 apply patient B (inactive)",
        status="inactive",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
    )
    db.add_all([pa, pb])
    await db.flush()

    week_monday = date(2026, 5, 11)  # W20 Mon
    # B の既存 visit (inactive 化前の残骸).
    stale_visit = Visit(
        patient_id=pb.id,
        visit_date=week_monday,
        start_time=time(11, 0),
        end_time=time(11, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc_v2",
        required_staff_count=1,
    )
    db.add(stale_visit)

    staff = Staff(
        name="g13-a-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.commit()

    result = await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=[
            {
                "patient_id": pa.id,
                "visit_plans": [
                    {
                        "weekday": 0,
                        "start_time": time(10, 0),
                        "end_time": time(10, 35),
                        "duration_min": 35,
                        "course_code": "A",
                        "office_id": office.id,
                        "am_pm": "am",
                    }
                ],
            }
        ],
    )
    await db.commit()

    # B の既存 visit が soft-delete されている.
    await db.refresh(stale_visit)
    assert stale_visit.deleted_at is not None, (
        "Phase G-13: apply_week_only で inactive 患者 B の visit が "
        "soft-delete されていない (旧 bug 再発)"
    )

    # A の新規 visit が INSERT されている.
    a_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pa.id,
                Visit.visit_date == week_monday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(a_visits) == 1, f"patient A の visit が 1 件 INSERT されているはず: {a_visits}"
    assert a_visits[0].start_time == time(10, 0)

    # B の新規 visit (active) は存在しない.
    b_actives = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == pb.id,
                Visit.visit_date == week_monday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert b_actives == [], f"inactive 患者 B の新規 visit が誤って INSERT された: {b_actives}"

    # soft-delete カウントに含まれている.
    assert result["visits_soft_deleted"] >= 1


@pytest.mark.asyncio
async def test_run_v2_pipeline_proposal_excludes_inactive_patient(db) -> None:
    """ケース 3 (任意): run_v2_pipeline の提案返却に inactive 患者が含まれない.

    run_v2_pipeline は visit を直接 soft-delete しない (= 提案返却のみ).
    `_load_active_patients` で active 患者のみを pool 化するため、 inactive 患者は
    そもそも提案にも新規生成にも現れない. これを担保する.
    """
    office = Office(name="g13-p-office")
    db.add(office)
    await db.flush()

    pa = Patient(
        code="G13PA",
        name="g13 pipeline patient A (active)",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "12:00",
            "duration_min": 35,
            "time_type": "固定",
        },
    )
    pb = Patient(
        code="G13PB",
        name="g13 pipeline patient B (inactive)",
        status="inactive",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "12:00",
            "duration_min": 35,
            "time_type": "固定",
        },
    )
    db.add_all([pa, pb])
    await db.flush()

    staff = Staff(
        name="g13-p-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )

    proposal_patient_ids = {v.patient_id for v in result.get("after_visits", [])}
    assert pa.id in proposal_patient_ids or len(proposal_patient_ids) == 0, (
        "active patient A は提案 pool 候補 (時刻配置できない可能性は許容)"
    )
    assert pb.id not in proposal_patient_ids, (
        f"inactive 患者 B が提案 after_visits に含まれている: {proposal_patient_ids}"
    )

    pool_patient_ids = {v.patient_id for v in result.get("pool_visits", [])}
    assert pb.id not in pool_patient_ids, (
        f"inactive 患者 B が pool_visits に含まれている: {pool_patient_ids}"
    )
