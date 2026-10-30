"""週の作り直し (reset_visits_to_fixed) で 2 名で訪問の方を組のまま作る.

Layer1 (_expand_fixed_visits_to_visits) とそろえる:
    - 2 件とも 1 人目 (slot_index=0) の時刻で作り、同じ visit_group_id にする.
      mode="auto" で 2 人目のコースだけ補正で時刻がずれても組が割れない.
    - 古いデータで 2 行の時刻が違っても 1 人目の時刻にそろえる.
    - どちらかが作れない (物理不可能・保護 visit と衝突) ときは 2 件とも作らない.
    - 片方の枠しか無い曜日は作らず警告を出す.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import Visit
from app.services.scheduling import auto_allocator_v2 as mod
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

ISO_YEAR = 2026
ISO_WEEK = 20
MONDAY = date(2026, 5, 11)


async def _seed(
    db,
    code: str,
    *,
    slots: dict[int, tuple[time, int]],
    multi: bool = True,
) -> tuple[Office, Patient]:
    """月曜のコース A/B (ひな形・担当 2 人) と、2 名で訪問の方の固定訪問を作る."""
    office = Office(name=f"rs2-{code}")
    db.add(office)
    await db.flush()
    tpls: dict[int, CourseTemplate] = {}
    for idx, label in ((0, "A"), (1, "B")):
        tpl = CourseTemplate(office_id=office.id, label=label)
        db.add(tpl)
        staff = Staff(
            name=f"rs2-{code}-{label}", role="staff", is_trainee=False, primary_office_id=office.id
        )
        db.add(staff)
        await db.flush()
        db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
        tpls[idx] = tpl
    p = Patient(
        code=f"RS2-{code}",
        name=f"rs2 患者{code}",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        requires_multiple_staff=multi,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    db.add(p)
    await db.flush()
    for slot, (st, dur) in slots.items():
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=0,
                start_time=st,
                duration_min=dur,
                slot_index=slot,
                course_template_id=tpls[slot].id,
            )
        )
    await db.commit()
    return office, p


async def _visits(db, p: Patient) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == p.id,
                    Visit.visit_date == MONDAY,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
    )


def _plus30(t: time) -> time:
    m = t.hour * 60 + t.minute + 30
    return time(m // 60, m % 60)


def _patch_corrections(monkeypatch, *, shift_code: str | None, drop_code: str | None) -> None:
    """補正を差し替える: shift_code のコースの訪問を 30 分後ろへ、drop_code は物理不可能にする."""

    def _fake(visits, *, warnings, office_name_by_id, config):  # noqa: ARG001
        dropped: set[int] = set()
        for v in visits:
            if shift_code is not None and v.course_code == shift_code:
                v.start_time = _plus30(v.start_time)
                v.end_time = _plus30(v.end_time)
            if drop_code is not None and v.course_code == drop_code:
                dropped.add(id(v))
        return dropped

    monkeypatch.setattr(mod, "_apply_corrections_to_visits", _fake)


@pytest.mark.asyncio
async def test_auto_mode_keeps_pair_together_when_partner_shifts(db, monkeypatch) -> None:
    """mode=auto で 2 人目のコースだけ補正で 30 分ずれても、2 件とも 1 人目の時刻・同じ組."""
    office, p = await _seed(db, "AUTO", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)})
    _patch_corrections(monkeypatch, shift_code="B", drop_code=None)

    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], mode="auto"
    )
    await db.commit()

    rows = await _visits(db, p)
    assert len(rows) == 2, rows
    assert {(v.start_time, v.end_time) for v in rows} == {(time(10, 0), time(10, 30))}
    assert rows[0].visit_group_id is not None
    assert rows[0].visit_group_id == rows[1].visit_group_id
    assert {v.required_staff_count for v in rows} == {2}


@pytest.mark.asyncio
async def test_legacy_differing_slot_times_use_slot0_time(db) -> None:
    """古いデータで 2 人目の固定訪問の時刻・長さが違っても、1 人目にそろえて組にする."""
    office, p = await _seed(db, "LEGACY", slots={0: (time(10, 0), 30), 1: (time(11, 0), 60)})

    await reset_visits_to_fixed(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id])
    await db.commit()

    rows = await _visits(db, p)
    assert len(rows) == 2, rows
    assert {(v.start_time, v.end_time) for v in rows} == {(time(10, 0), time(10, 30))}
    assert len({v.visit_group_id for v in rows}) == 1
    assert rows[0].visit_group_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("drop_code", ["A", "B"])
async def test_physically_impossible_side_skips_both(db, monkeypatch, drop_code: str) -> None:
    """どちらかが補正で物理不可能なら、もう片方だけの訪問は作らない (警告つき)."""
    office, p = await _seed(
        db, f"DROP{drop_code}", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)}
    )
    _patch_corrections(monkeypatch, shift_code=None, drop_code=drop_code)

    result = await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], mode="auto"
    )
    await db.commit()

    assert await _visits(db, p) == []
    assert any("2 名の組の片方" in w for w in result["warnings"]), result["warnings"]


@pytest.mark.asyncio
async def test_protected_visit_at_pair_time_skips_both(db) -> None:
    """2 名の組の時刻に保護される訪問 (実施済み) があれば、2 件とも作らない."""
    office, p = await _seed(db, "PROT", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)})
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=MONDAY,
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status="completed",
            source="manual",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    await db.commit()

    rows = await _visits(db, p)
    assert len(rows) == 1 and rows[0].status == "completed", rows
    assert any("衝突するため再生成スキップ" in w for w in result["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("only_slot", [0, 1])
async def test_one_slot_only_two_staff_day_not_created(db, only_slot: int) -> None:
    """2 名で訪問の方で、その曜日の固定訪問が 1 人分しか無い → 1 人の訪問を作らない (Layer1 と同じ)."""
    office, p = await _seed(db, f"ONE{only_slot}", slots={only_slot: (time(10, 0), 30)})

    result = await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    await db.commit()

    assert await _visits(db, p) == []
    assert any("1 人分しか無い" in w for w in result["warnings"]), result["warnings"]


@pytest.mark.asyncio
async def test_single_staff_patient_slot0_still_created(db) -> None:
    """1 名の方は今までどおり 1 件 (visit_group_id なし)."""
    office, p = await _seed(db, "SINGLE", slots={0: (time(10, 0), 30)}, multi=False)

    await reset_visits_to_fixed(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id])
    await db.commit()

    rows = await _visits(db, p)
    assert len(rows) == 1, rows
    assert rows[0].visit_group_id is None
    assert rows[0].required_staff_count == 1
