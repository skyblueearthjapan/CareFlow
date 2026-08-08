"""週のピン (青ピン) と取込 (import) の週生成保護 — PO 決定 2026-08-09.

仕様: docs/plans/pin-and-movability-spec.md §5

検証観点:
  1. week_pinned=true の visit は reset-to-fixed で soft-delete されない
     (source='auto' でも守られる = フラグが source と独立に効く)
  2. week_pinned の日は型からの再生成をスキップする (重複挿入されない)
  3. **import の日も再生成をスキップする** (案 B: 取込が正の原則。旧実装は
     削除からは保護していたが日スキップが無く、取込週で週生成を回すと型の
     時刻で重複挿入され得た)
  4. generate-week-only (Layer1) でも week_pinned は削除されない

ハーネスは test_change_scope_u0.py と同型 (reset_visits_to_fixed 直呼び)。
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient, Visit
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed
from app.services.scheduling.layer1_expander import Layer1Expander

# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date(2026, 5, 11)
TUE = date(2026, 5, 12)

# ---------------------------------------------------------------------------
# helpers (test_change_scope_u0 と同型)
# ---------------------------------------------------------------------------


async def _make_office(db, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(db, code: str, *, office_id) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        special_week_active=[],
        primary_office_id=office_id,
        lat=35.65,
        lng=140.10,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_pfv(db, *, patient: Patient, weekday: int, start: time) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=30,
        slot_index=0,
        is_pinned=False,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


async def _make_visit(
    db,
    *,
    patient: Patient,
    visit_date: date,
    start: time,
    source: str,
    week_pinned: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=time(start.hour, start.minute + 30)
        if start.minute < 30
        else time(start.hour + 1, 0),
        type="regular",
        status="planned",
        source=source,
        week_pinned=week_pinned,
        required_staff_count=1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _active_visits(db, patient_id) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 1-2) week_pinned の reset 保護 (削除されない + 日スキップ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pinned_auto_visit_survives_reset_and_blocks_regeneration(db) -> None:
    """核心: source='auto' でも week_pinned=true なら reset で消えず、
    その日は型からの再生成もスキップされる (重複しない)."""
    office = await _make_office(db, "wpr-1-office")
    p = await _make_patient(db, "WPR-1", office_id=office.id)
    # 型: 月曜 09:00。実配置: 月曜 10:25 (ズレ) + 青ピン。
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    pinned = await _make_visit(
        db, patient=p, visit_date=MON, start=time(10, 25), source="auto", week_pinned=True
    )

    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], patient_id=p.id
    )
    await db.commit()

    survivors = await _active_visits(db, p.id)
    ids = [v.id for v in survivors]
    assert pinned.id in ids, "青ピン (source=auto) は reset で消えない"
    mon_rows = [v for v in survivors if v.visit_date == MON]
    assert len(mon_rows) == 1, (
        f"青ピンの日は型から再生成されない (重複しない): {[(v.start_time, v.source) for v in mon_rows]}"
    )
    assert mon_rows[0].start_time == time(10, 25)


# ---------------------------------------------------------------------------
# 3) import の日スキップ (案 B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_day_blocks_regeneration(db) -> None:
    """案 B: 取込 (import) の日は青ピン無しでも型からの再生成をスキップする。

    旧実装の穴: import は削除からは保護されていたが日スキップが無く、
    取込週で週生成を回すと型の時刻で重複挿入され得た。
    「カイポケ取込が正」の原則をコードに落とす。
    """
    office = await _make_office(db, "wpr-2-office")
    p = await _make_patient(db, "WPR-2", office_id=office.id)
    # 型: 月曜 09:00。取込の実配置: 月曜 11:00 (型とズレ・青ピン無し)。
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    imported = await _make_visit(db, patient=p, visit_date=MON, start=time(11, 0), source="import")

    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], patient_id=p.id
    )
    await db.commit()

    survivors = await _active_visits(db, p.id)
    assert imported.id in [v.id for v in survivors], "import は reset で消えない (従来どおり)"
    mon_rows = [v for v in survivors if v.visit_date == MON]
    assert len(mon_rows) == 1, (
        f"取込の日は型から再生成されない: {[(v.start_time, v.source) for v in mon_rows]}"
    )
    assert mon_rows[0].source == "import"


# ---------------------------------------------------------------------------
# 4) generate-week-only (Layer1) でも week_pinned は消えない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pinned_survives_generate_week_only(db) -> None:
    """Layer1 の週生成 (source='auto' を白紙化→再展開) でも青ピンは残り、
    その日は再展開もスキップされる."""
    office = await _make_office(db, "wpr-3-office")
    p = await _make_patient(db, "WPR-3", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    pinned = await _make_visit(
        db, patient=p, visit_date=MON, start=time(10, 25), source="auto", week_pinned=True
    )

    expander = Layer1Expander()
    await expander.expand_week(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id)
    await db.commit()

    survivors = await _active_visits(db, p.id)
    assert pinned.id in [v.id for v in survivors], "青ピンは generate-week-only で消えない"
    mon_rows = [v for v in survivors if v.visit_date == MON]
    assert len(mon_rows) == 1, (
        f"青ピンの日は再展開されない: {[(v.start_time, v.source) for v in mon_rows]}"
    )
