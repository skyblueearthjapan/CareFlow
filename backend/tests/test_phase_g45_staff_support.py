"""Phase G-45: staff 応援拡張 (= primary 休業日に secondary に転入カウント).

責務:
    - ``count_active_staff_per_weekday`` で primary 休業日に secondary 転入が発生.
    - secondary 無し staff は当該 weekday カウント外.
    - ``count_active_managers_per_weekday`` も同様.
    - Layer 3 ``load_active_staff`` で StaffInfo.effective_office_for_weekday(wd)
      が primary 休業日に secondary を返す.
"""

from __future__ import annotations

from datetime import date as date_cls

import pytest

from app.models import Office
from app.models.staff import Staff, StaffSecondaryOffice, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    count_active_managers_per_weekday,
    count_active_staff_per_weekday,
)
from app.services.scheduling.layer3_assignment import Layer3Assigner


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
        operating_weekdays=operating_weekdays or [0, 1, 2, 3, 4, 5],
    )
    db.add(o)
    await db.flush()
    return o


async def _make_staff(
    db,
    *,
    name: str,
    primary_office: Office,
    secondary_offices: list[Office] | None = None,
    role: str = "staff",
    work_days: list[int] | None = None,
) -> Staff:
    s = Staff(
        name=name,
        role=role,
        is_trainee=False,
        primary_office_id=primary_office.id,
    )
    db.add(s)
    await db.flush()
    for sec in secondary_offices or []:
        db.add(StaffSecondaryOffice(staff_id=s.id, office_id=sec.id))
    for wd in work_days or []:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# count_active_staff_per_weekday
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_active_staff_redirects_to_secondary_when_primary_closed(db) -> None:
    """primary が休業の weekday は secondary に転入カウントされる.

    シナリオ:
      - tsuga = 月-金 (Sat 休業), inage = 月-土 (Sat 営業).
      - staff A: primary = tsuga, secondary = [inage], work_days = [Sat].
      - office_ids = [tsuga, inage].
      - 期待: count[(inage, Sat)] = 1, count[(tsuga, Sat)] = 0.
    """
    tsuga = await _make_office(
        db, name="g45s-TSUGA", code="TS1", operating_weekdays=[0, 1, 2, 3, 4]
    )
    inage = await _make_office(
        db, name="g45s-INAGE", code="IN1", operating_weekdays=[0, 1, 2, 3, 4, 5]
    )
    await _make_staff(
        db,
        name="本名 (testfixture)",
        primary_office=tsuga,
        secondary_offices=[inage],
        work_days=[5],
    )
    await db.commit()

    counts = await count_active_staff_per_weekday(
        db, office_ids=[tsuga.id, inage.id], iso_year=2026, iso_week=20
    )
    assert counts.get((inage.id, 5), 0) == 1, (
        f"primary=TSUGA (Sat 休業) + secondary=INAGE (Sat 営業) staff は INAGE に "
        f"転入カウントのはず: {counts}"
    )
    assert counts.get((tsuga.id, 5), 0) == 0, (
        f"primary=TSUGA は Sat 休業のため (TSUGA, Sat) には 0 件のはず: {counts}"
    )


@pytest.mark.asyncio
async def test_count_active_staff_no_secondary_excluded_when_primary_closed(db) -> None:
    """secondary 無しで primary が休業なら当該 weekday カウント外."""
    tsuga = await _make_office(
        db, name="g45s-TSUGA-noSec", code="TS2", operating_weekdays=[0, 1, 2, 3, 4]
    )
    await _make_staff(
        db,
        name="g45s no-secondary",
        primary_office=tsuga,
        secondary_offices=[],
        work_days=[5],
    )
    await db.commit()

    counts = await count_active_staff_per_weekday(
        db, office_ids=[tsuga.id], iso_year=2026, iso_week=20
    )
    assert counts.get((tsuga.id, 5), 0) == 0, (
        f"primary 休業 + secondary 無し staff は除外される期待: {counts}"
    )


@pytest.mark.asyncio
async def test_count_active_staff_open_weekday_counts_primary(db) -> None:
    """営業日は primary にカウントされる (regression: 既存挙動維持)."""
    tsuga = await _make_office(
        db, name="g45s-TSUGA-open", code="TS3", operating_weekdays=[0, 1, 2, 3, 4]
    )
    inage = await _make_office(db, name="g45s-INAGE-open", code="IN3")
    await _make_staff(
        db,
        name="g45s open-day",
        primary_office=tsuga,
        secondary_offices=[inage],
        work_days=[2],  # Wed = 都賀営業日
    )
    await db.commit()

    counts = await count_active_staff_per_weekday(
        db, office_ids=[tsuga.id, inage.id], iso_year=2026, iso_week=20
    )
    assert counts.get((tsuga.id, 2), 0) == 1, (
        f"Wed (= TSUGA 営業日) なので primary=TSUGA にカウント: {counts}"
    )
    assert counts.get((inage.id, 2), 0) == 0, (
        f"primary 営業日なら secondary には転入しない: {counts}"
    )


@pytest.mark.asyncio
async def test_count_active_managers_redirects_to_secondary_when_primary_closed(db) -> None:
    """manager も同様に primary 休業日は secondary に転入."""
    tsuga = await _make_office(
        db, name="g45s-TSUGA-mgr", code="TSM", operating_weekdays=[0, 1, 2, 3, 4]
    )
    inage = await _make_office(db, name="g45s-INAGE-mgr", code="INM")
    await _make_staff(
        db,
        name="g45s mgr",
        primary_office=tsuga,
        secondary_offices=[inage],
        role="manager",
        work_days=[5],
    )
    await db.commit()

    counts = await count_active_managers_per_weekday(
        db, office_ids=[tsuga.id, inage.id], iso_year=2026, iso_week=20
    )
    assert counts.get((inage.id, 5), 0) == 1, f"manager 応援が INAGE Sat にカウント: {counts}"


# ---------------------------------------------------------------------------
# Layer 3 load_active_staff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer3_load_active_staff_uses_secondary_on_closed_day(db) -> None:
    """``StaffInfo.effective_office_for_weekday`` が primary 休業日に secondary を返す."""
    tsuga = await _make_office(
        db, name="g45L3-TSUGA", code="TSGL3", operating_weekdays=[0, 1, 2, 3, 4]
    )
    inage = await _make_office(
        db, name="g45L3-INAGE", code="INGL3", operating_weekdays=[0, 1, 2, 3, 4, 5]
    )
    s = await _make_staff(
        db,
        name="g45L3-aux-staff",
        primary_office=tsuga,
        secondary_offices=[inage],
        work_days=[2, 5],  # Wed + Sat
    )
    await db.commit()

    assigner = Layer3Assigner()
    staff_pool = await assigner.load_active_staff(
        db,
        iso_year=2026,
        iso_week=20,
        week_monday=date_cls(2026, 5, 11),
    )
    staff_info = next((x for x in staff_pool if x.staff_id == s.id), None)
    assert staff_info is not None, "対象 staff が staff_pool に含まれない"
    # Wed (= primary TSUGA 営業日) は TSUGA を返す.
    assert staff_info.effective_office_for_weekday(2) == tsuga.id, (
        f"Wed では primary=TSUGA を返すはず: actual={staff_info.effective_office_for_weekday(2)}"
    )
    # Sat (= primary TSUGA 休業日) は secondary=INAGE を返す.
    assert staff_info.effective_office_for_weekday(5) == inage.id, (
        f"Sat では secondary=INAGE に転入. actual={staff_info.effective_office_for_weekday(5)}"
    )
    # secondary_office_ids が tuple で expose されている.
    assert inage.id in staff_info.secondary_office_ids, (
        f"secondary_office_ids に inage.id が含まれるはず: {staff_info.secondary_office_ids}"
    )
