"""W41 v1.0 hotfix: auto_allocator が既存 active visits を上書きする際に
pre-cleanup として soft-delete し、partial UNIQUE INDEX
``uq_visits_pds_group_active`` 違反を回避することを検証する.

設計: docs/plans/auto-schedule-v1.md (v0.4) + 本番 500 エラーの hotfix.
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from app.models import Office, Patient
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator import (
    AUTO_ALLOC_VISIT_SOURCE,
    ProposedVisit,
    _persist_courses_visits_and_assignments,
)


@pytest.mark.asyncio
async def test_persist_soft_deletes_conflicting_existing_visits(db) -> None:
    """同 (patient_id, visit_date, start_time) の既存 active visits を
    auto_allocator が pre-cleanup として soft-delete し、その後の Visit INSERT
    が partial UNIQUE INDEX 違反にならないことを検証する.
    """
    office = Office(name="hotfix-office")
    db.add(office)
    await db.flush()

    patient = Patient(code="W41-HF-01", name="hotfix patient", status="active")
    db.add(patient)
    await db.flush()

    # 既存 active visit: Layer 1 等が事前に作成したと想定 (source='auto')
    week_monday = date(2026, 5, 11)  # ISO 2026-W20 Monday
    existing = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 5, 12),  # Tuesday
        start_time=time(17, 0),
        end_time=time(18, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
    )
    db.add(existing)
    await db.commit()
    existing_id = existing.id

    # auto_allocator が同 (patient, date, time) で新規 INSERT したい ProposedVisit
    pv = ProposedVisit(
        patient_id=patient.id,
        weekday=1,  # Tuesday: week_monday + 1
        start_time=time(17, 0),
        end_time=time(18, 0),
        service_minutes=60,
        lat=35.65,
        lng=140.10,
        source_kind="pool",
        office_id=office.id,
        course_index=0,
    )

    warnings: list[str] = []
    await _persist_courses_visits_and_assignments(
        db,
        visits=[pv],
        iso_year=2026,
        iso_week=20,
        week_monday=week_monday,
        proposal_batch_id=uuid.uuid4(),
        warnings=warnings,
    )
    await db.commit()

    # 既存 visit が soft-delete されている
    await db.refresh(existing)
    assert existing.deleted_at is not None, "既存 active visit が soft-delete されていない"

    # 新規 visit (source='auto_alloc') が INSERT されている
    from sqlalchemy import select

    new_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.source == AUTO_ALLOC_VISIT_SOURCE,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(new_visits) == 1
    assert new_visits[0].id != existing_id
    assert new_visits[0].visit_date == date(2026, 5, 12)
    assert new_visits[0].start_time == time(17, 0)

    # warnings に soft-delete メッセージが含まれている
    assert any("soft-delete" in w for w in warnings), warnings


@pytest.mark.asyncio
async def test_persist_no_cleanup_when_no_conflict(db) -> None:
    """衝突する既存 active visit が無い場合は soft-delete を実行しない."""
    office = Office(name="hotfix-office-2")
    db.add(office)
    await db.flush()

    patient = Patient(code="W41-HF-02", name="no-conflict patient", status="active")
    db.add(patient)
    await db.flush()

    # 別曜日の既存 visit (= 衝突しない)
    other = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 5, 11),  # Monday
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
        required_staff_count=1,
    )
    db.add(other)
    await db.commit()

    pv = ProposedVisit(
        patient_id=patient.id,
        weekday=1,  # Tuesday: not conflicting with Monday existing
        start_time=time(17, 0),
        end_time=time(18, 0),
        service_minutes=60,
        lat=35.65,
        lng=140.10,
        source_kind="pool",
        office_id=office.id,
        course_index=0,
    )

    warnings: list[str] = []
    await _persist_courses_visits_and_assignments(
        db,
        visits=[pv],
        iso_year=2026,
        iso_week=20,
        week_monday=date(2026, 5, 11),
        proposal_batch_id=uuid.uuid4(),
        warnings=warnings,
    )
    await db.commit()

    # 別曜日の visit はそのまま active
    await db.refresh(other)
    assert other.deleted_at is None

    # 新規 visit (source='auto_alloc') も INSERT されている
    from sqlalchemy import select

    auto_alloc_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.source == AUTO_ALLOC_VISIT_SOURCE,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(auto_alloc_visits) == 1

    # soft-delete 警告は出ない
    assert not any("soft-delete" in w for w in warnings), warnings
