"""週の作り直し (reset_visits_to_fixed) で 2 名で訪問の方を組のまま作る.

Layer1 (_expand_fixed_visits_to_visits) とそろえる. 2026-10-30 から 2 名で訪問は
**1 行** (本番の形・カイポケ取込と同じ):
    - required_staff_count=2・visit_group_id なし・course_id / primary = 1 人目 (slot 0)
      のコースと担当・secondary_staff_id = 2 人目 (slot 1) のコースの担当. VSA も 2 人.
    - 1 人目 (slot_index=0) の時刻で作る. mode="auto" で 2 人目のコースだけ補正で
      時刻がずれても 1 人目の時刻.
    - 古いデータで 2 行の時刻が違っても 1 人目の時刻にそろえる.
    - どちらかが作れない (物理不可能・保護 visit と衝突) ときは作らない.
    - 片方の枠しか無い曜日は作らず警告を出す.
    - 2 人目のコースの担当が 1 人目と同じ人なら 2 人目は空 (足りない) で警告.

本番と同じ partial UNIQUE (uq_visits_pds_group_active) を張って流す.
ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling import auto_allocator_v2 as mod
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

ISO_YEAR = 2026
ISO_WEEK = 20
MONDAY = date(2026, 5, 11)

pytestmark = pytest.mark.usefixtures("visits_pds_unique_index")


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


async def _assert_one_pair_row(db, office: Office, p: Patient, start: time, end: time) -> Visit:
    """2 名で訪問の 1 行: 1 人目 = A コースの担当・2 人目 = B コースの担当・VSA も 2 人."""
    rows = await _visits(db, p)
    assert len(rows) == 1, rows
    v = rows[0]
    assert (v.start_time, v.end_time) == (start, end)
    assert v.visit_group_id is None
    assert v.required_staff_count == 2
    courses = {
        c.code: c
        for c in (
            await db.scalars(
                select(Course).where(
                    Course.office_id == office.id,
                    Course.iso_year == ISO_YEAR,
                    Course.iso_week == ISO_WEEK,
                    Course.weekday == 0,
                )
            )
        ).all()
    }
    assert v.course_id == courses["A"].id
    assert v.primary_staff_id == courses["A"].assigned_staff_id is not None
    assert v.secondary_staff_id == courses["B"].assigned_staff_id is not None
    assert v.primary_staff_id != v.secondary_staff_id
    vsa = set(
        (
            await db.scalars(
                select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == v.id)
            )
        ).all()
    )
    assert vsa == {v.primary_staff_id, v.secondary_staff_id}
    return v


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
    """mode=auto で 2 人目のコースだけ補正で 30 分ずれても、1 行・1 人目の時刻."""
    office, p = await _seed(db, "AUTO", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)})
    _patch_corrections(monkeypatch, shift_code="B", drop_code=None)

    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], mode="auto"
    )
    await db.commit()

    await _assert_one_pair_row(db, office, p, time(10, 0), time(10, 30))


@pytest.mark.asyncio
async def test_legacy_differing_slot_times_use_slot0_time(db) -> None:
    """古いデータで 2 人目の固定訪問の時刻・長さが違っても、1 人目の時刻で 1 行にする."""
    office, p = await _seed(db, "LEGACY", slots={0: (time(10, 0), 30), 1: (time(11, 0), 60)})

    await reset_visits_to_fixed(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id])
    await db.commit()

    await _assert_one_pair_row(db, office, p, time(10, 0), time(10, 30))


@pytest.mark.asyncio
async def test_rerun_replaces_pair_row_without_unique_violation(db) -> None:
    """2 回続けて作り直しても 1 行のまま (本番の partial UNIQUE に当たらない)."""
    office, p = await _seed(db, "RERUN", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)})
    for _ in range(2):
        await reset_visits_to_fixed(
            db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], mode="auto"
        )
        await db.commit()
    await _assert_one_pair_row(db, office, p, time(10, 0), time(10, 30))


@pytest.mark.asyncio
async def test_same_staff_on_both_courses_leaves_secondary_empty(db) -> None:
    """2 人目のコースの担当が 1 人目と同じ人 → 1 行は作り、2 人目は空 (足りない) で警告."""
    office, p = await _seed(db, "SAME", slots={0: (time(10, 0), 30), 1: (time(10, 0), 30)})
    staff_a = await db.scalar(select(Staff).where(Staff.name == "rs2-SAME-A"))
    for tpl in (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.office_id == office.id))
    ).all():
        db.add(
            Course(
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=0,
                code=tpl.label,
                course_status="staff_assigned",
                template_id=tpl.id,
                office_id=office.id,
                assigned_staff_id=staff_a.id,
            )
        )
    await db.commit()

    result = await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    await db.commit()

    rows = await _visits(db, p)
    assert len(rows) == 1, rows
    assert rows[0].required_staff_count == 2
    assert rows[0].primary_staff_id == staff_a.id
    assert rows[0].secondary_staff_id is None
    assert any("2 人目が足りません" in w for w in result["warnings"]), result["warnings"]


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
    """2 名の組の時刻に保護される訪問 (実施済み) があれば、作らない."""
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
