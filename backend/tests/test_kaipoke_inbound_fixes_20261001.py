"""取込の不具合 6 件の根治 (2026-10-01) — docs/plans/inbound-fixes-2026-10-01.md.

session-2026-09-24-HANDOFF.md §4-2 の 1〜6 に対応する回帰テスト:
  1. 取込 delete が打刻済み訪問を取り消さない (要確認として残す)
  2. 跨ぎ date_change と移動先 delete の同居 (同日同時刻の対を優先・取消枠へは移動可)
  3. 担当2 の解除で取込由来の同行 (accompaniments) も外す
  4. date_change でコースが移動先の日のコースへ付け替わる
  5. 予定外訪問 (is_unplanned) がカイポケ行と突合したら昇格する
  6. 打刻の付け替えスクリプト (dry-run 既定)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.services.kaipoke.inbound import apply_inbound_items
from tests.test_kaipoke_inbound import (
    MONTH,
    PATIENT_NAME,
    STAFF_NAME,
    WEEK_START,
    _kp_row,
    _seed_week,
)
from tests.test_kaipoke_smart_inbound import (
    _apply,
    _csv,
    _make_admin,
    _preview,
    stub_kaipoke,  # noqa: F401  (pytest フィクスチャの再利用)
)

WEEK_END = WEEK_START + timedelta(days=6)
NOW = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


async def _checkin(db, visit: Visit, staff_id, *, kind: str = "arrival") -> VisitCheckin:
    c = VisitCheckin(
        visit_id=visit.id,
        patient_id=visit.patient_id,
        staff_id=staff_id,
        kind=kind,
        scanned_at=datetime.combine(visit.visit_date, visit.start_time, tzinfo=UTC),
        match_status="match",
        threshold_snapshot={"v": 1},
    )
    db.add(c)
    await db.commit()
    return c


async def _sheet_items(db, specs: list[dict[str, Any]]) -> list[CorrectionSheetItem]:
    sheet = CorrectionSheet(
        target_month=MONTH,
        status="ready",
        direction="inbound",
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    db.add(sheet)
    await db.flush()
    items = [CorrectionSheetItem(sheet_id=sheet.id, include=True, **s) for s in specs]
    db.add_all(items)
    await db.flush()
    return items


def _side(d: int, start: str, end: str, staff1: str = STAFF_NAME, staff2: str = "") -> dict:
    return {
        "user_name": PATIENT_NAME,
        "date": str(d),
        "start_time": start,
        "end_time": end,
        "staff1": staff1,
        "staff2": staff2,
    }


EMPTY = {"user_name": PATIENT_NAME, "date": "", "start_time": "", "end_time": ""}


# --- 1. 打刻済み訪問は取り消さない ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_delete_keeps_checked_in_visit(db, dry_run: bool) -> None:
    seeded = await _seed_week(db)
    wed = seeded["wed"]
    await _checkin(db, wed, seeded["staff"].id)
    items = await _sheet_items(
        db,
        [
            {
                "patient_id": seeded["patient"].id,
                "visit_id": wed.id,
                "action": "delete",
                "before": _side(8, "11:00", "11:35"),
                "after": EMPTY,
            }
        ],
    )

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=dry_run,
        now=NOW,
    )

    assert summary.cancelled == 0
    assert summary.failed == 1
    (r,) = summary.results
    assert r.outcome == "failed"
    assert r.reason == "checked_in"
    assert "打刻済みのため取り消していません" in r.detail
    await db.flush()
    await db.refresh(wed)
    assert wed.status == "planned"


@pytest.mark.asyncio
async def test_delete_without_checkin_still_cancels(db) -> None:
    """打刻の無い訪問の delete は従来どおり取り消す (既存挙動の維持)。"""
    seeded = await _seed_week(db)
    wed = seeded["wed"]
    items = await _sheet_items(
        db,
        [
            {
                "patient_id": seeded["patient"].id,
                "visit_id": wed.id,
                "action": "delete",
                "before": _side(8, "11:00", "11:35"),
                "after": EMPTY,
            }
        ],
    )
    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=NOW,
    )
    assert summary.cancelled == 1
    await db.flush()
    await db.refresh(wed)
    assert wed.status == "cancelled"


@pytest.mark.asyncio
async def test_smart_preview_and_apply_report_checked_in_delete(client, db, stub_kaipoke) -> None:  # noqa: F811
    """smart: プレビューに件数と印・実適用は取り消さず要確認として結果に残る。"""
    seeded = await _seed_week(db)
    tue = seeded["tue"]
    await _checkin(db, tue, seeded["staff"].id)
    admin = await _make_admin(db)
    # カイポケ現況: 火曜の訪問が無い (= 打刻済み訪問への delete)・木曜は不変。
    stub_kaipoke.by_month[MONTH] = _csv(_kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)))

    res = await _preview(client, admin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["diffSummary"]["checked_in_delete"] == 1
    item = await db.scalar(
        select(CorrectionSheetItem).where(
            CorrectionSheetItem.visit_id == tue.id, CorrectionSheetItem.action == "delete"
        )
    )
    assert item is not None and "打刻済み" in (item.comment or "")

    res2 = await _apply(client, admin, sheet_id=body["sheetId"], dry_run=False)
    assert res2.status_code == 200, res2.text
    diff = res2.json()["diff"]
    assert diff["cancelled"] == 0
    assert any(r["reason"] == "checked_in" for r in diff["results"])
    await db.refresh(tue)
    assert tue.status == "planned" and tue.deleted_at is None
