"""Tests for scripts/reattach_cancelled_checkins.py (2026-10-01・取込の不具合 #6).

9/23 藤原様の形: 取込で取り消された訪問 (13:00) に打刻 (11:55 到着・12:29 退出) が
残り、同じ日の生きている訪問 (11:30) には打刻が無い。
"""

# ruff: noqa: I001
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import select

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from reattach_cancelled_checkins import apply_pairs, find_cases, reattach  # noqa: E402

from app.models.office import Office  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.staff import Staff  # noqa: E402
from app.models.visit import Visit  # noqa: E402
from app.models.visit_checkin import VisitCheckin  # noqa: E402

DAY = date(2026, 7, 8)


async def _seed(db, *, live_starts: list[time]) -> dict:
    office = Office(name="拠点A", code="OFA")
    db.add(office)
    await db.flush()
    staff = Staff(name="職員 一", role="staff", primary_office_id=office.id)
    db.add(staff)
    patient = Patient(code="PT-RA-1", name="患者 一", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()

    def _visit(start: time, status: str) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=DAY,
            start_time=start,
            end_time=time(start.hour, 30),
            type="regular",
            status=status,
            source="import",
            required_staff_count=1,
            primary_staff_id=staff.id,
        )

    cancelled = _visit(time(13, 0), "cancelled")
    live = [_visit(s, "planned") for s in live_starts]
    db.add_all([cancelled, *live])
    await db.flush()
    for kind, hm in (("arrival", time(2, 55)), ("departure", time(3, 29))):  # JST 11:55/12:29
        db.add(
            VisitCheckin(
                visit_id=cancelled.id,
                patient_id=patient.id,
                staff_id=staff.id,
                kind=kind,
                scanned_at=datetime.combine(DAY, hm, tzinfo=UTC),
                match_status="match",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()
    return {"cancelled": cancelled, "live": live}


@pytest.mark.asyncio
async def test_find_and_reattach_single_candidate(db) -> None:
    seeded = await _seed(db, live_starts=[time(11, 30)])
    cases = await find_cases(db)
    assert len(cases) == 1
    case = cases[0]
    assert case.status == "ready"
    assert case.target is not None and case.target.id == seeded["live"][0].id

    # dry-run 相当 (find_cases だけ) では何も動いていない。
    n = await db.scalar(
        select(VisitCheckin.id).where(VisitCheckin.visit_id == seeded["live"][0].id)
    )
    assert n is None

    moved = await reattach(db, case, now=datetime(2026, 10, 1, tzinfo=UTC))
    await db.commit()
    assert moved["checkins"] == 2

    target = seeded["live"][0]
    await db.refresh(target)
    assert target.status == "completed"  # 退出の打刻があるので完了へ
    assert "打刻の付け替え" in (target.note or "")
    rows = (await db.scalars(select(VisitCheckin).where(VisitCheckin.visit_id == target.id))).all()
    assert len(rows) == 2
    # 付け替え後はもう対象として出てこない (冪等)。
    assert await find_cases(db) == []


@pytest.mark.asyncio
async def test_multiple_candidates_need_explicit_pair(db) -> None:
    seeded = await _seed(db, live_starts=[time(11, 30), time(16, 0)])
    cases = await find_cases(db)
    assert [c.status for c in cases] == ["ambiguous"]

    errors = apply_pairs(cases, {seeded["cancelled"].id: seeded["live"][1].id})
    assert errors == []
    assert cases[0].target is not None and cases[0].target.id == seeded["live"][1].id


@pytest.mark.asyncio
async def test_date_filter_excludes_other_days(db) -> None:
    await _seed(db, live_starts=[time(11, 30)])
    assert await find_cases(db, date_from=date(2026, 7, 9)) == []
