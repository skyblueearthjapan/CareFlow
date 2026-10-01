"""Tests for scripts/reattach_cancelled_checkins.py (2026-10-01・取込の不具合 #6).

9/23 藤原様の形: 取込で取り消された訪問 (13:00・担当 A) に別の職員 (C) の打刻
(11:55 到着・12:29 退出) が残り、同じ日の生きている訪問 (11:30・担当 B) には打刻が無い。
"""

# ruff: noqa: I001
from __future__ import annotations

import os
import subprocess
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
from app.services.kaipoke.inbound import IMPORT_CANCEL_NOTE  # noqa: E402

DAY = date(2026, 7, 8)
IMPORT_NOTE = f"カイポケ取込 9/24: {IMPORT_CANCEL_NOTE}"


async def _base(db) -> dict:
    office = Office(name="拠点A", code="OFA")
    db.add(office)
    await db.flush()
    staff = {k: Staff(name=f"職員 {k}", role="staff", primary_office_id=office.id) for k in "ABCD"}
    db.add_all(staff.values())
    patient = Patient(code="PT-RA-1", name="患者 一", status="active", primary_office_id=office.id)
    db.add(patient)
    await db.flush()
    return {"office": office, "staff": staff, "patient": patient}


async def _visit(
    db,
    base,
    start: time,
    *,
    staff: str,
    status: str = "planned",
    source: str = "import",
    note: str | None = None,
) -> Visit:
    v = Visit(
        patient_id=base["patient"].id,
        visit_date=DAY,
        start_time=start,
        end_time=time(start.hour, 30),
        type="regular",
        status=status,
        source=source,
        required_staff_count=1,
        primary_staff_id=base["staff"][staff].id,
        note=note,
    )
    db.add(v)
    await db.flush()
    return v


async def _cancelled_with_checkins(
    db, base, start: time = time(13, 0), *, source: str = "import", note: str = IMPORT_NOTE
) -> Visit:
    cv = await _visit(db, base, start, staff="A", status="cancelled", source=source, note=note)
    for kind, hm in (("arrival", time(2, 55)), ("departure", time(3, 29))):  # JST 11:55/12:29
        db.add(
            VisitCheckin(
                visit_id=cv.id,
                patient_id=base["patient"].id,
                staff_id=base["staff"]["C"].id,
                kind=kind,
                scanned_at=datetime.combine(DAY, hm, tzinfo=UTC),
                match_status="match",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()
    return cv


@pytest.mark.asyncio
async def test_find_and_reattach_single_candidate(db) -> None:
    base = await _base(db)
    live = await _visit(db, base, time(11, 30), staff="B")  # 打刻 11:55 から 25 分
    await _cancelled_with_checkins(db, base)

    cases = await find_cases(db)
    assert len(cases) == 1
    case = cases[0]
    assert case.status == "ready"
    assert case.target is not None and case.target.id == live.id

    moved = await reattach(db, case, now=datetime(2026, 10, 1, tzinfo=UTC))
    await db.commit()
    assert moved["checkins"] == 2

    await db.refresh(live)
    assert live.status == "completed"  # 退出の打刻があるので完了へ
    assert "打刻の付け替え" in (live.note or "")
    rows = (await db.scalars(select(VisitCheckin).where(VisitCheckin.visit_id == live.id))).all()
    assert len(rows) == 2
    assert await find_cases(db) == []  # 付け替え後は対象に出ない (冪等)


@pytest.mark.asyncio
async def test_multiple_candidates_need_explicit_pair(db) -> None:
    base = await _base(db)
    await _visit(db, base, time(11, 30), staff="B")
    later = await _visit(db, base, time(16, 0), staff="B")
    cv = await _cancelled_with_checkins(db, base)

    cases = await find_cases(db)
    assert [c.status for c in cases] == ["ambiguous"]
    assert apply_pairs(cases, {cv.id: later.id}) == []
    assert cases[0].target is not None and cases[0].target.id == later.id


@pytest.mark.asyncio
async def test_date_filter_excludes_other_days(db) -> None:
    base = await _base(db)
    await _visit(db, base, time(11, 30), staff="B")
    await _cancelled_with_checkins(db, base)
    assert await find_cases(db, date_from=date(2026, 7, 9)) == []


# --- (a) 同じ訪問を 2 件の取消済み訪問が取り合う ------------------------------


@pytest.mark.asyncio
async def test_target_claimed_by_two_cancelled_visits_is_ambiguous(db) -> None:
    base = await _base(db)
    live = await _visit(db, base, time(11, 30), staff="B")
    first = await _cancelled_with_checkins(db, base, time(13, 0))
    second = await _cancelled_with_checkins(db, base, time(12, 0))

    cases = await find_cases(db)
    assert sorted(c.status for c in cases) == ["ambiguous", "ambiguous"]
    assert all("別の取消済み訪問" in c.reason for c in cases)
    # --pair で両方を同じ移動先へ当てるのもエラー。
    errors = apply_pairs(cases, {first.id: live.id, second.id: live.id})
    assert errors and "2 件" in errors[0]


# --- (b) 担当も時刻も合わない候補は自動で組まない ------------------------------


@pytest.mark.asyncio
async def test_far_candidate_with_other_staff_is_ambiguous(db) -> None:
    base = await _base(db)
    await _visit(db, base, time(17, 0), staff="B")  # 取消 13:00 / 打刻 11:55 から 90 分超
    await _cancelled_with_checkins(db, base)
    cases = await find_cases(db)
    assert [c.status for c in cases] == ["ambiguous"]
    assert "担当も時刻" in cases[0].reason


@pytest.mark.asyncio
async def test_far_candidate_with_same_staff_is_paired(db) -> None:
    base = await _base(db)
    live = await _visit(db, base, time(17, 0), staff="C")  # 打刻した職員と同じ担当
    await _cancelled_with_checkins(db, base)
    cases = await find_cases(db)
    assert cases[0].status == "ready" and cases[0].target.id == live.id


# --- (c) 取込以外の取消は対象外 -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "note"),
    [
        ("manual_cancel", IMPORT_NOTE),  # 今週だけ取消
        ("status_cancel", IMPORT_NOTE),  # 患者ステータス連動の取消
        ("auto", "画面で取消"),  # 取込の取消の刻印が無い
    ],
)
async def test_non_import_cancellations_are_ignored(db, source: str, note: str) -> None:
    base = await _base(db)
    await _visit(db, base, time(11, 30), staff="B")
    await _cancelled_with_checkins(db, base, source=source, note=note)
    assert await find_cases(db) == []


# --- (d) --apply には --from/--to が必須 ---------------------------------------


@pytest.mark.parametrize("extra", [[], ["--from", "2026-09-23"], ["--to", "2026-09-23"]])
def test_apply_requires_date_range(extra: list[str]) -> None:
    res = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "reattach_cancelled_checkins.py"), "--apply", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )
    assert res.returncode == 2
    assert "--from と --to" in res.stderr
