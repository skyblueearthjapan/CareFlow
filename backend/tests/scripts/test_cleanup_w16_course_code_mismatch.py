"""Tests for scripts/cleanup_w16_course_code_mismatch.py (W18 Phase 0-a).

カバー範囲:
  1. _label_to_code: label → code 正規化
  2. _scan_mismatches: 不整合行を抽出 (整合行は無視)
  3. _apply_fix: 1 件の UPDATE が成功する
  4. dry-run vs apply の違い
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# sys.path にスクリプトディレクトリを追加
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from cleanup_w16_course_code_mismatch import (  # noqa: E402
    _apply_fix,
    _label_to_code,
    _Mismatch,
    _scan_mismatches,
)


def test_label_to_code_normalises_first_char_uppercase() -> None:
    """label の先頭 1 文字を大文字化して code 値域に入っていれば返す."""
    assert _label_to_code("a") == "A"
    assert _label_to_code("E") == "E"
    assert _label_to_code("M") == "M"
    # CHECK 値域外 → None
    assert _label_to_code("X") is None
    assert _label_to_code(None) is None
    assert _label_to_code("") is None
    # 複数文字 → 先頭 1 文字
    assert _label_to_code("Alpha") == "A"


@pytest.mark.asyncio
async def test_scan_finds_mismatch_and_apply_fix(_engine, db) -> None:
    """W16 残骸 (template-E, code='M') を scan で抽出し、_apply_fix で正規化できる."""
    from app.models.course import COURSE_STATUS_PROPOSED, Course
    from app.models.course_template import CourseTemplate
    from app.models.office import Office

    office = Office(name="事業所CW16")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    tpl_e = CourseTemplate(
        office_id=office.id,
        label="E",
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    tpl_a = CourseTemplate(
        office_id=office.id,
        label="A",
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    db.add_all([tpl_e, tpl_a])
    await db.commit()
    await db.refresh(tpl_e)
    await db.refresh(tpl_a)

    # 不整合 row: template=tpl_e (label='E') だが code='M'
    bad = Course(
        iso_year=2026,
        iso_week=19,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_PROPOSED,
        template_id=tpl_e.id,
        office_id=office.id,
    )
    # 整合 row: template=tpl_a (label='A'), code='A'
    good = Course(
        iso_year=2026,
        iso_week=19,
        weekday=1,
        code="A",
        course_status=COURSE_STATUS_PROPOSED,
        template_id=tpl_a.id,
        office_id=office.id,
    )
    db.add_all([bad, good])
    await db.commit()
    await db.refresh(bad)
    await db.refresh(good)

    # scan: 不整合 row だけ抽出される
    mismatches = await _scan_mismatches()
    assert len(mismatches) == 1, f"expected 1 mismatch, got {len(mismatches)}"
    m = mismatches[0]
    assert m.course_id == str(bad.id)
    assert m.current_code == "M"
    assert m.desired_code == "E"
    assert m.template_label == "E"

    # apply: UPDATE が成功する
    ok, msg = await _apply_fix(m)
    assert ok, f"apply failed: {msg}"

    # 再 scan で 0 件 (冪等)
    again = await _scan_mismatches()
    assert len(again) == 0, "apply 後に再 scan で mismatch が残っている"


@pytest.mark.asyncio
async def test_apply_fix_skips_when_collision_detected(_engine, db) -> None:
    """desired_code の row が既にあると _apply_fix は skip して False を返す."""
    from app.models.course import COURSE_STATUS_PROPOSED, Course
    from app.models.course_template import CourseTemplate
    from app.models.office import Office

    office = Office(name="事業所CW16-COL")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    tpl_e = CourseTemplate(
        office_id=office.id,
        label="E",
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    db.add(tpl_e)
    await db.commit()
    await db.refresh(tpl_e)

    # 既に code='E' の row が存在 (UNIQUE 衝突を起こす)
    existing_e = Course(
        iso_year=2026,
        iso_week=20,
        weekday=2,
        code="E",
        course_status=COURSE_STATUS_PROPOSED,
        template_id=tpl_e.id,
        office_id=office.id,
    )
    # 不整合 row: 同じ slot に code='M' で入っている (cleanup 対象)
    bad = Course(
        iso_year=2026,
        iso_week=20,
        weekday=2,
        code="M",
        course_status=COURSE_STATUS_PROPOSED,
        template_id=tpl_e.id,
        office_id=office.id,
    )
    db.add_all([existing_e, bad])
    await db.commit()
    await db.refresh(bad)

    m = _Mismatch(
        course_id=str(bad.id),
        office_id=str(office.id),
        iso_year=2026,
        iso_week=20,
        weekday=2,
        current_code="M",
        template_id=str(tpl_e.id),
        template_label="E",
        desired_code="E",
    )
    ok, msg = await _apply_fix(m)
    assert ok is False, f"collision should skip, got ok={ok} msg={msg}"
    assert "collision" in msg
