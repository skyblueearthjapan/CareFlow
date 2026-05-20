"""Phase G-9: ``_resolve_course_for_pfv`` cache_key regression tests.

旧実装の cache_key=(office_id, weekday, code) には致命的バグがあった:

- 同 ``patient.primary_office_id`` (= office_id) で異なる ``course_template_id``
  を持つ 2 つの PFV を処理すると、 先に処理した PFV の Course が
  同 (office_id, weekday, code) で誤キャッシュヒットし、 後続 PFV にも
  同じ Course (= 別 office の course) が返ってしまっていた.
- 実 DB 確認: 月曜 TSUGA-A に INAGE 患者 5 件が誤混入.

本テストは fix 後の cache_key=(template.id, iso_year, iso_week, weekday)
が以下を満たすことを保証する:
  - シナリオ A: 同 office × 異 template_id の 2 PFV は別 Course を返す
  - シナリオ B: 同 template.id の 2 PFV は同 Course (cache hit) を返す
"""

from __future__ import annotations

from datetime import time
from uuid import UUID

import pytest

from app.models import (
    CourseTemplate,
    Office,
    Patient,
    PatientFixedVisit,
)
from app.models.course import Course
from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_pfv

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_office(db, *, code: str) -> Office:
    o = Office(code=code, name=code)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(db, *, code: str, primary_office_id: UUID) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        special_week_active=[],
        primary_office_id=primary_office_id,
        sex="male",
        status="active",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course_template(db, *, office_id: UUID, label: str) -> CourseTemplate:
    ct = CourseTemplate(office_id=office_id, label=label)
    db.add(ct)
    await db.commit()
    await db.refresh(ct)
    return ct


async def _make_pfv(
    db,
    *,
    patient_id: UUID,
    weekday: int,
    start: time,
    course_template_id: UUID,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient_id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=30,
        slot_index=0,
        course_template_id=course_template_id,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


# ---------------------------------------------------------------------------
# シナリオ A: 同 patient.primary_office_id (INAGE) で異なる template_id を
# 持つ 2 PFV を処理しても、 返却 Course の office_id がそれぞれ正しく
# 元 template の office_id を指す (= 誤キャッシュヒットしない).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_course_for_pfv_distinct_templates_same_office(db) -> None:
    """同 office × 異 template_id の 2 PFV は別 Course を返す.

    Phase G-9 で発見された致命的バグの regression test.
    """
    inage = await _make_office(db, code="INAGE")
    tsuga = await _make_office(db, code="TSUGA")

    # INAGE-A template (INAGE 拠点)
    inage_a_tpl = await _make_course_template(db, office_id=inage.id, label="A")
    # TSUGA-A template (TSUGA 拠点) — 同 label='A' で別 office
    tsuga_a_tpl = await _make_course_template(db, office_id=tsuga.id, label="A")

    # patient は両方とも primary_office_id=INAGE
    patient_inage_self = await _make_patient(db, code="P-INAGE-1", primary_office_id=inage.id)
    patient_inage_to_tsuga = await _make_patient(db, code="P-INAGE-2", primary_office_id=inage.id)

    # PFV-1: INAGE patient × INAGE-A template
    pfv_inage = await _make_pfv(
        db,
        patient_id=patient_inage_self.id,
        weekday=0,  # 月曜
        start=time(9, 0),
        course_template_id=inage_a_tpl.id,
    )
    # PFV-2: INAGE patient × TSUGA-A template (Phase G-8 で導入された他拠点希望)
    pfv_tsuga = await _make_pfv(
        db,
        patient_id=patient_inage_to_tsuga.id,
        weekday=0,
        start=time(9, 30),
        course_template_id=tsuga_a_tpl.id,
    )

    course_cache: dict[tuple[UUID, int, int, int], Course] = {}
    warnings: list[str] = []

    # 順序が問題: 先に TSUGA-A を解決
    course_tsuga = await _resolve_course_for_pfv(
        db,
        pfv=pfv_tsuga,
        office_id=patient_inage_to_tsuga.primary_office_id,
        iso_year=2026,
        iso_week=21,
        weekday=0,
        course_cache=course_cache,
        warnings=warnings,
    )
    # 続いて INAGE-A を解決
    course_inage = await _resolve_course_for_pfv(
        db,
        pfv=pfv_inage,
        office_id=patient_inage_self.primary_office_id,
        iso_year=2026,
        iso_week=21,
        weekday=0,
        course_cache=course_cache,
        warnings=warnings,
    )

    assert course_tsuga is not None
    assert course_inage is not None

    # 致命的バグの regression check: 異なる Course オブジェクトが返ること.
    assert course_tsuga.id != course_inage.id, (
        "different template_id should resolve to different Course (Phase G-9 cache bug regression)"
    )
    # office_id がそれぞれ元 template の office_id を指していること.
    assert course_tsuga.office_id == tsuga.id
    assert course_inage.office_id == inage.id
    # template_id も正しく紐づくこと.
    assert course_tsuga.template_id == tsuga_a_tpl.id
    assert course_inage.template_id == inage_a_tpl.id


# ---------------------------------------------------------------------------
# シナリオ B: 同 template.id の 2 PFV を続けて呼ぶと cache hit で同じ
# Course オブジェクトが返る (= cache 機能は維持されている).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_course_for_pfv_cache_hit_same_template(db) -> None:
    """同 template.id × 同 (year, week, weekday) の 2 PFV は cache hit する."""
    inage = await _make_office(db, code="INAGE")
    inage_a_tpl = await _make_course_template(db, office_id=inage.id, label="A")

    patient_a = await _make_patient(db, code="P-A", primary_office_id=inage.id)
    patient_b = await _make_patient(db, code="P-B", primary_office_id=inage.id)

    pfv_a = await _make_pfv(
        db,
        patient_id=patient_a.id,
        weekday=0,
        start=time(9, 0),
        course_template_id=inage_a_tpl.id,
    )
    pfv_b = await _make_pfv(
        db,
        patient_id=patient_b.id,
        weekday=0,
        start=time(9, 30),
        course_template_id=inage_a_tpl.id,
    )

    course_cache: dict[tuple[UUID, int, int, int], Course] = {}
    warnings: list[str] = []

    course_first = await _resolve_course_for_pfv(
        db,
        pfv=pfv_a,
        office_id=patient_a.primary_office_id,
        iso_year=2026,
        iso_week=21,
        weekday=0,
        course_cache=course_cache,
        warnings=warnings,
    )
    course_second = await _resolve_course_for_pfv(
        db,
        pfv=pfv_b,
        office_id=patient_b.primary_office_id,
        iso_year=2026,
        iso_week=21,
        weekday=0,
        course_cache=course_cache,
        warnings=warnings,
    )

    assert course_first is not None
    assert course_second is not None
    # 同 template.id なので同じ Course オブジェクトが返る (cache hit).
    assert course_first is course_second
    # cache に 1 件だけ入っていること.
    assert len(course_cache) == 1
    expected_key = (inage_a_tpl.id, 2026, 21, 0)
    assert expected_key in course_cache
