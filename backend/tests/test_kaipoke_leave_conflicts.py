"""カイポケの取込・置換の dry-run が、休む人の休みの時間帯に付く担当を leaveConflicts で返す.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §4-2 (H-b)。カイポケが正なので
取り込みは止めず、プレビューで知らせるだけ。週ビュー (week-schedule) は明示の担当なしを
コース担当に読み替えない (§3-2)。
"""

from __future__ import annotations

from datetime import date, time

import pytest

import app.services.staff_leave as staff_leave
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from tests.test_kaipoke_inbound import (
    WEEK_START,
    _bearer,
    _diff_with_state,
    _kaipoke_csv,
    _kp_row,
    _make_admin,
    _seed_course,
    _seed_real_apply,
    _seed_second_staff,
    _seed_week,
    _staff_changed_state,
    stub_kaipoke,  # noqa: F401  (pytest fixture の再エクスポート)
)
from tests.test_kaipoke_replace_inbound import _post_replace

TUE = date(2026, 7, 7)


@pytest.fixture
def before_week(monkeypatch):
    """取込の週 (2026-07) を「今日以降」として扱う (過去の日は検知しない決まりのため)."""
    monkeypatch.setattr(staff_leave, "today_jst", lambda: date(2026, 7, 1))


async def _leave(db, staff_id, d: date, t: str) -> None:
    y, w, wd = iso_key(d)
    await set_staff_override(
        db, staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=t
    )
    await db.commit()


@pytest.mark.asyncio
async def test_replace_dry_run_reports_leave_conflicts(
    client,
    db,
    stub_kaipoke,  # noqa: F811
    before_week,
) -> None:
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _leave(db, seeded["staff"].id, TUE, "pm_off")
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {"csv_content": _kaipoke_csv(_kp_row(TUE, time(14, 0), time(14, 35)))}
    }
    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 1  # 取込は止めない
    conflicts = body["leaveConflicts"]
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["staffName"] == seeded["staff"].name
    assert c["date"] == "2026-07-07"
    assert c["startTime"] == "14:00"
    assert c["endTime"] == "14:35"
    assert c["leaveType"] == "pm_off"


@pytest.mark.asyncio
async def test_replace_dry_run_outside_window_is_empty(
    client,
    db,
    stub_kaipoke,  # noqa: F811
    before_week,
) -> None:
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _leave(db, seeded["staff"].id, TUE, "am_off")  # 14:00 は 13:00 以降なのでかからない
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {"csv_content": _kaipoke_csv(_kp_row(TUE, time(14, 0), time(14, 35)))}
    }
    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    assert res.json()["leaveConflicts"] == []


@pytest.mark.asyncio
async def test_diff_dry_run_staff_change_reports_leave_conflicts(
    client,
    db,
    stub_kaipoke,  # noqa: F811
    before_week,
) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    sato = await _seed_second_staff(db, seeded["office"])
    await _seed_course(db, office=seeded["office"], staff=sato, weekday=1, code="B")
    await _leave(db, sato.id, TUE, "off")

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"]},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["dryRun"] is True
    conflicts = out["leaveConflicts"]
    assert [(c["staffName"], c["date"], c["leaveType"]) for c in conflicts] == [
        (sato.name, "2026-07-07", "off")
    ]


@pytest.mark.asyncio
async def test_week_schedule_does_not_read_explicit_unassigned_as_course_staff(
    client,
    db,
    stub_kaipoke,  # noqa: F811
) -> None:
    from sqlalchemy import select

    from app.models.visit import Visit

    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    admin = await _make_admin(db)
    v = await db.scalar(select(Visit).where(Visit.visit_date == TUE))
    assert v is not None
    from app.models import Course

    course = await db.scalar(
        select(Course).where(Course.weekday == 1, Course.code == "A", Course.iso_week == 28)
    )
    v.course_id = course.id if course is not None else v.course_id
    v.primary_staff_id = None
    v.manual_staff_override = True
    await db.commit()
    res = await client.get(
        "/api/v1/integrations/week-schedule?weekStart=2026-07-06&weekEnd=2026-07-12",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = [r for r in res.json()["rows"] if r["visitDate"] == "2026-07-07"]
    assert rows and rows[0]["staff1"] == ""
