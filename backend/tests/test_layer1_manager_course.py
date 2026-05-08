"""W29: Layer 1 で manager 用 M course を毎週全曜日自動生成するテスト.

検証観点:
  1. M template + manager 在籍 → 全曜日 (capacity > 0) で M course 生成
  2. M template あるが manager 不在 → 生成されない
  3. 既存 M course あり → 重複作成されない (冪等)
  4. capacity=0 の曜日は生成されない
  5. expand_week 経由でも同様に M course が生成される
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import Office, Staff
from app.models.course import COURSE_STATUS_COURSE_FIXED, Course
from app.models.course_template import CourseTemplate
from app.services.scheduling.layer1_expander import (
    Layer1Expander,
    _ensure_manager_courses_for_week,
)

# ISO week 22 of 2026 — Monday is 2026-05-25.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_office(db, *, name: str = "本店(稲毛)") -> Office:
    office = Office(name=name, lat=35.6383, lng=140.1041)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_manager(db, *, office: Office, code: str = "MGR-01") -> Staff:
    mgr = Staff(
        code=code,
        name="川名 千恵",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.commit()
    await db.refresh(mgr)
    return mgr


async def _make_m_template(
    db,
    *,
    office: Office,
    label: str = "M",
    mon: int = 7,
    tue: int = 7,
    wed: int = 7,
    thu: int = 7,
    fri: int = 7,
    sat: int = 5,
    sun: int = 0,
) -> CourseTemplate:
    ct = CourseTemplate(
        office_id=office.id,
        label=label,
        capacity_mon=mon,
        capacity_tue=tue,
        capacity_wed=wed,
        capacity_thu=thu,
        capacity_fri=fri,
        capacity_sat=sat,
        capacity_sun=sun,
    )
    db.add(ct)
    await db.commit()
    await db.refresh(ct)
    return ct


async def _count_m_courses(db, *, template_id: UUID, iso_year: int, iso_week: int) -> list[Course]:
    rows = (
        await db.scalars(
            select(Course).where(
                Course.template_id == template_id,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.deleted_at.is_(None),
            )
        )
    ).all()
    return list(rows)


# ---------------------------------------------------------------------------
# 1) M template + manager 在籍 → 全曜日 (capacity > 0) で M course 生成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_creates_m_courses_for_all_capacity_days(db) -> None:
    """manager 在籍 + M template の曜日別 capacity > 0 → course が生成される."""
    office = await _make_office(db)
    await _make_manager(db, office=office)
    # Mon-Fri: 7, Sat: 5, Sun: 0 (= 6 曜日)
    ct = await _make_m_template(db, office=office)

    created = await _ensure_manager_courses_for_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK)
    await db.commit()

    assert created == 6  # Mon-Sat の 6 曜日 (Sun は capacity=0 なのでスキップ)

    courses = await _count_m_courses(
        db, template_id=ct.id, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    assert len(courses) == 6

    weekdays = sorted(c.weekday for c in courses)
    assert weekdays == [0, 1, 2, 3, 4, 5]  # Mon=0 .. Sat=5, Sun=6 は生成されない

    for c in courses:
        assert c.code == "M"
        assert c.course_status == COURSE_STATUS_COURSE_FIXED
        assert c.office_id == office.id


# ---------------------------------------------------------------------------
# 2) M template あるが manager 不在 → 生成されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_skips_office_without_manager(db) -> None:
    """manager 不在の office では M course を生成しない."""
    office = await _make_office(db)
    # role='staff' のみ (manager なし)
    staff = Staff(
        code="S-01",
        name="一般スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.commit()

    ct = await _make_m_template(db, office=office)

    created = await _ensure_manager_courses_for_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK)
    await db.commit()

    assert created == 0

    courses = await _count_m_courses(
        db, template_id=ct.id, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    assert len(courses) == 0


# ---------------------------------------------------------------------------
# 3) 既存 M course あり → 重複作成されない (冪等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_idempotent_when_courses_already_exist(db) -> None:
    """既存 M course がある場合、再呼び出しで重複作成しない."""
    office = await _make_office(db)
    await _make_manager(db, office=office)
    ct = await _make_m_template(db, office=office)

    # 1 回目: 6 件生成
    c1 = await _ensure_manager_courses_for_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK)
    await db.commit()
    assert c1 == 6

    # 2 回目: 何も生成しない
    c2 = await _ensure_manager_courses_for_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK)
    await db.commit()
    assert c2 == 0

    courses = await _count_m_courses(
        db, template_id=ct.id, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    assert len(courses) == 6


# ---------------------------------------------------------------------------
# 4) capacity=0 の曜日は生成されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_skips_zero_capacity_weekdays(db) -> None:
    """capacity=0 の曜日には course を生成しない."""
    office = await _make_office(db)
    await _make_manager(db, office=office)
    # 月・水・金のみ capacity=1, 残りは 0
    ct = await _make_m_template(
        db,
        office=office,
        mon=1,
        tue=0,
        wed=1,
        thu=0,
        fri=1,
        sat=0,
        sun=0,
    )

    created = await _ensure_manager_courses_for_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK)
    await db.commit()

    assert created == 3  # Mon=0, Wed=2, Fri=4 の 3 件

    courses = await _count_m_courses(
        db, template_id=ct.id, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    assert len(courses) == 3
    weekdays = sorted(c.weekday for c in courses)
    assert weekdays == [0, 2, 4]


# ---------------------------------------------------------------------------
# 5) expand_week 経由でも M course が生成される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_week_also_creates_m_courses(db) -> None:
    """Layer1Expander.expand_week を呼ぶと M course も生成される."""
    office = await _make_office(db)
    await _make_manager(db, office=office)
    ct = await _make_m_template(db, office=office)  # Mon-Sat の 6 曜日

    expander = Layer1Expander()
    # patient なし (patients_processed=0 でも M course 生成は走る)
    result = await expander.expand_week(db, TEST_ISO_YEAR, TEST_ISO_WEEK, office_id=office.id)
    await db.commit()

    assert result.iso_year == TEST_ISO_YEAR
    assert result.iso_week == TEST_ISO_WEEK

    courses = await _count_m_courses(
        db, template_id=ct.id, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    assert len(courses) == 6

    for c in courses:
        assert c.course_status == COURSE_STATUS_COURSE_FIXED
        assert c.code == "M"
