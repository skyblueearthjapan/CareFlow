"""カイポケ取込のスワップ/玉突き対応 (2026-08-24 本番障害の根治) のテスト。

実録: 井川様の訪問 2 件が 8/10 ↔ 8/13 (同時刻 13:00) の日付入れ替えとして
date_change に出たとき、``apply_inbound_items`` の移動先占有チェックが
**両方を failed** にした (どちらを先に処理しても相手が枠に居座る恒久デッドロック)。

ここでは ``apply_inbound_items`` を直接呼んで以下を固定する:
  * 相互スワップ 2 件が両方 updated になる
  * 直鎖の玉突き (A→B, B→C, C→空き) が全件解消される
  * 循環 + バッチ外占有の混在 (循環は成功・バッチ外占有は failed のまま)
  * 従来の単純 date_change / 時刻変更 / バッチ外占有 failed の回帰
  * dry-run の予測が実適用と一致する
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import text

from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.services.kaipoke.inbound import apply_inbound_items

WEEK_START = date(2026, 8, 10)  # 月曜
WEEK_END = date(2026, 8, 16)
NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)

PATIENT_NAME = "井川　太郎"
STAFF_NAME = "田中　看護師"


async def _seed(db) -> dict[str, Any]:
    office = Office(name="稲毛", code="INAGE-SWAP")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-SWAP-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    other = Patient(
        code="PT-SWAP-2",
        name="別川　次郎",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add_all([patient, other])
    await db.flush()
    sheet = CorrectionSheet(
        target_month="2026-08",
        status="ready",
        direction="inbound",
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    db.add(sheet)
    await db.flush()
    return {"office": office, "staff": staff, "patient": patient, "other": other, "sheet": sheet}


async def _visit(db, seeded, d: date, start: time, *, patient: Patient | None = None) -> Visit:
    v = Visit(
        patient_id=(patient or seeded["patient"]).id,
        visit_date=d,
        start_time=start,
        end_time=time(start.hour, start.minute + 35) if start.minute + 35 < 60 else time(23, 59),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
        primary_staff_id=seeded["staff"].id,
    )
    db.add(v)
    await db.flush()
    return v


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _move_item(
    seeded,
    visit: Visit,
    *,
    to_date: date,
    to_start: time,
    patient_name: str = PATIENT_NAME,
) -> CorrectionSheetItem:
    """date_change / edit の item を組み立てる (diff engine の出力と同じ形)。"""
    action = "date_change" if to_date != visit.visit_date else "edit"
    return CorrectionSheetItem(
        sheet_id=seeded["sheet"].id,
        patient_id=visit.patient_id,
        visit_id=visit.id,
        action=action,
        before={
            "user_name": patient_name,
            "date": str(visit.visit_date.day),
            "start_time": _hhmm(visit.start_time),
            "end_time": _hhmm(visit.end_time),
            "staff1": STAFF_NAME,
            "staff2": "",
        },
        after={
            "user_name": patient_name,
            "date": str(to_date.day),
            "start_time": _hhmm(to_start),
            "end_time": _hhmm(visit.end_time),
            "staff1": STAFF_NAME,
            "staff2": "",
        },
        include=True,
    )


async def _apply(db, items, *, dry_run: bool = False):
    db.add_all(items)
    await db.flush()
    return await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=dry_run,
        now=NOW,
    )


def _outcomes(summary) -> list[str]:
    return [r.outcome for r in summary.results]


# --- ① 相互スワップ ----------------------------------------------------------


@pytest.mark.asyncio
async def test_mutual_swap_both_updated(db) -> None:
    """8/10 13:00 ↔ 8/13 13:00 の入れ替え 2 件が **両方 updated** になる。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(13, 0))
    b = await _visit(db, seeded, date(2026, 8, 13), time(13, 0))
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 13), to_start=time(13, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 10), to_start=time(13, 0)),
    ]

    summary = await _apply(db, items)

    assert summary.failed == 0, [r.__dict__ for r in summary.results]
    assert summary.updated == 2
    assert sorted(_outcomes(summary)) == ["updated", "updated"]
    # 実際に入れ替わっている & 仮値 (マイクロ秒) が残っていない。
    assert (a.visit_date, a.start_time) == (date(2026, 8, 13), time(13, 0))
    assert (b.visit_date, b.start_time) == (date(2026, 8, 10), time(13, 0))
    assert a.start_time.microsecond == 0
    assert b.start_time.microsecond == 0
    assert a.source == "manual_week"
    assert b.source == "manual_week"
    # detail は退避前の時刻で書かれる (13:00→13:00 のような偽の時刻変更を出さない)。
    details = " / ".join(r.detail for r in summary.results)
    assert "8/10→8/13" in details
    assert "8/13→8/10" in details


@pytest.mark.asyncio
async def test_mutual_swap_under_partial_unique_index(db) -> None:
    """本番と同じ partial UNIQUE がある状態でも入れ替えが通る (一時退避の検証)。

    テスト用 SQLite はモデル定義のみでテーブルを作るため migration 0027 の
    ``uq_visits_pds_group_active`` が無い = 一時退避無しでも DB は通ってしまう。
    ここだけ本番と同じ index を張り、マイクロ秒退避が本当に効いていることを固定する。
    """
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(13, 0))
    b = await _visit(db, seeded, date(2026, 8, 13), time(13, 0))
    await db.flush()
    await db.execute(
        text(
            "CREATE UNIQUE INDEX uq_visits_pds_group_active "
            "ON visits (patient_id, visit_date, start_time, "
            "COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000')) "
            "WHERE deleted_at IS NULL"
        )
    )
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 13), to_start=time(13, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 10), to_start=time(13, 0)),
    ]

    summary = await _apply(db, items)
    await db.flush()

    assert (summary.updated, summary.failed) == (2, 0), [r.__dict__ for r in summary.results]
    assert (a.visit_date, a.start_time) == (date(2026, 8, 13), time(13, 0))
    assert (b.visit_date, b.start_time) == (date(2026, 8, 10), time(13, 0))


@pytest.mark.asyncio
async def test_mutual_swap_dry_run_predicts_updated(db) -> None:
    """dry-run の予測も 2 件 updated (実適用と一致・DB は無変更)。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(13, 0))
    b = await _visit(db, seeded, date(2026, 8, 13), time(13, 0))
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 13), to_start=time(13, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 10), to_start=time(13, 0)),
    ]

    summary = await _apply(db, items, dry_run=True)

    assert summary.updated == 2
    assert summary.failed == 0
    assert a.visit_date == date(2026, 8, 10)
    assert b.visit_date == date(2026, 8, 13)


@pytest.mark.asyncio
async def test_time_only_swap_same_day(db) -> None:
    """同日内の時刻入れ替え (edit ×2) も両方 updated。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 11), time(9, 0))
    b = await _visit(db, seeded, date(2026, 8, 11), time(15, 0))
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 11), to_start=time(15, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 11), to_start=time(9, 0)),
    ]

    summary = await _apply(db, items)

    assert (summary.updated, summary.failed) == (2, 0)
    assert a.start_time == time(15, 0)
    assert b.start_time == time(9, 0)


# --- ② 直鎖の玉突き ----------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_shift_resolves(db) -> None:
    """A→B, B→C, C→空き の直鎖 3 件が全て updated (順序替えだけで解ける)。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(9, 0))
    b = await _visit(db, seeded, date(2026, 8, 11), time(9, 0))
    c = await _visit(db, seeded, date(2026, 8, 12), time(9, 0))
    # シート順は A→B が先 (=従来なら A が B に塞がれて failed)。
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 11), to_start=time(9, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 12), to_start=time(9, 0)),
        _move_item(seeded, c, to_date=date(2026, 8, 14), to_start=time(9, 0)),
    ]

    summary = await _apply(db, items)

    assert summary.failed == 0, [r.__dict__ for r in summary.results]
    assert summary.updated == 3
    assert a.visit_date == date(2026, 8, 11)
    assert b.visit_date == date(2026, 8, 12)
    assert c.visit_date == date(2026, 8, 14)


@pytest.mark.asyncio
async def test_three_way_cycle_resolves(db) -> None:
    """A→B→C→A の 3 件循環も全て updated。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(10, 0))
    b = await _visit(db, seeded, date(2026, 8, 11), time(10, 0))
    c = await _visit(db, seeded, date(2026, 8, 12), time(10, 0))
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 11), to_start=time(10, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 12), to_start=time(10, 0)),
        _move_item(seeded, c, to_date=date(2026, 8, 10), to_start=time(10, 0)),
    ]

    summary = await _apply(db, items)

    assert summary.failed == 0, [r.__dict__ for r in summary.results]
    assert summary.updated == 3
    assert a.visit_date == date(2026, 8, 11)
    assert b.visit_date == date(2026, 8, 12)
    assert c.visit_date == date(2026, 8, 10)
    for v in (a, b, c):
        assert v.start_time == time(10, 0)


# --- ③ 循環 + バッチ外占有の混在 ---------------------------------------------


@pytest.mark.asyncio
async def test_cycle_succeeds_while_external_block_fails(db) -> None:
    """循環は成功し、バッチ外の訪問に塞がれた 1 件だけ failed のまま。"""
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(13, 0))
    b = await _visit(db, seeded, date(2026, 8, 13), time(13, 0))
    # 動かない訪問 (バッチ外) と、そこへ移ろうとする訪問。
    blocker = await _visit(db, seeded, date(2026, 8, 12), time(16, 0))
    mover = await _visit(db, seeded, date(2026, 8, 14), time(16, 0))
    items = [
        _move_item(seeded, a, to_date=date(2026, 8, 13), to_start=time(13, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 10), to_start=time(13, 0)),
        _move_item(seeded, mover, to_date=date(2026, 8, 12), to_start=time(16, 0)),
    ]

    summary = await _apply(db, items)

    assert summary.updated == 2
    assert summary.failed == 1
    by_item = {uuid.UUID(r.item_id): r for r in summary.results}
    assert by_item[items[0].id].outcome == "updated"
    assert by_item[items[1].id].outcome == "updated"
    failed = by_item[items[2].id]
    assert failed.outcome == "failed"
    assert "別の予定があります" in failed.detail
    # 循環は確定・バッチ外占有は据え置き。
    assert a.visit_date == date(2026, 8, 13)
    assert b.visit_date == date(2026, 8, 10)
    assert (mover.visit_date, mover.start_time) == (date(2026, 8, 14), time(16, 0))
    assert (blocker.visit_date, blocker.start_time) == (date(2026, 8, 12), time(16, 0))


@pytest.mark.asyncio
async def test_cycle_rolls_back_when_a_member_cannot_be_finalized(db) -> None:
    """循環メンバーの 1 件が確定できなければ、循環まるごと巻き戻して全件 failed。

    仮値 (マイクロ秒付き start_time) のまま commit される状態を作らないための
    原子性テスト。ここでは同一バッチの delete が片方をキャンセルしてしまうため
    循環側は skipped になり、確定できない。
    """
    seeded = await _seed(db)
    a = await _visit(db, seeded, date(2026, 8, 10), time(13, 0))
    b = await _visit(db, seeded, date(2026, 8, 13), time(13, 0))
    delete_item = CorrectionSheetItem(
        sheet_id=seeded["sheet"].id,
        patient_id=a.patient_id,
        visit_id=a.id,
        action="delete",
        before={
            "user_name": PATIENT_NAME,
            "date": "10",
            "start_time": "13:00",
            "end_time": _hhmm(a.end_time),
            "staff1": STAFF_NAME,
        },
        after={},
        include=True,
    )
    items = [
        delete_item,
        _move_item(seeded, a, to_date=date(2026, 8, 13), to_start=time(13, 0)),
        _move_item(seeded, b, to_date=date(2026, 8, 10), to_start=time(13, 0)),
    ]

    summary = await _apply(db, items)
    await db.flush()

    assert summary.cancelled == 1
    assert summary.updated == 0
    assert summary.failed == 2
    swap_results = [r for r in summary.results if r.item_id != str(delete_item.id)]
    assert [r.outcome for r in swap_results] == ["failed", "failed"]
    assert all("入れ替え（スワップ）" in r.detail for r in swap_results)
    # 位置は元のまま・仮値は残っていない。delete の結果は savepoint の外なので生きる。
    assert (a.visit_date, a.start_time) == (date(2026, 8, 10), time(13, 0))
    assert (b.visit_date, b.start_time) == (date(2026, 8, 13), time(13, 0))
    assert a.start_time.microsecond == 0
    assert b.start_time.microsecond == 0
    assert a.status == "cancelled"
    assert b.status == "planned"


# --- ④ 従来挙動の回帰 --------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_date_change_still_updates(db) -> None:
    """単純な date_change (移動先が空き) は従来どおり updated。"""
    seeded = await _seed(db)
    v = await _visit(db, seeded, date(2026, 8, 10), time(11, 0))
    items = [_move_item(seeded, v, to_date=date(2026, 8, 12), to_start=time(11, 0))]

    summary = await _apply(db, items)

    assert (summary.updated, summary.failed) == (1, 0)
    assert v.visit_date == date(2026, 8, 12)
    assert v.source == "manual_week"


@pytest.mark.asyncio
async def test_simple_time_change_still_updates(db) -> None:
    """単純な時刻変更 (edit) は従来どおり updated。"""
    seeded = await _seed(db)
    v = await _visit(db, seeded, date(2026, 8, 10), time(11, 0))
    items = [_move_item(seeded, v, to_date=date(2026, 8, 10), to_start=time(14, 0))]

    summary = await _apply(db, items)

    assert (summary.updated, summary.failed) == (1, 0)
    assert v.start_time == time(14, 0)
    assert "11:00→14:00" in summary.results[0].detail


@pytest.mark.asyncio
async def test_external_occupied_still_fails(db) -> None:
    """バッチ外の訪問が移動先を占有していれば従来どおり item 単位 failed。"""
    seeded = await _seed(db)
    v = await _visit(db, seeded, date(2026, 8, 10), time(11, 0))
    await _visit(db, seeded, date(2026, 8, 12), time(11, 0))
    items = [_move_item(seeded, v, to_date=date(2026, 8, 12), to_start=time(11, 0))]

    summary = await _apply(db, items)

    assert (summary.updated, summary.failed) == (0, 1)
    assert "別の予定があります" in summary.results[0].detail
    assert v.visit_date == date(2026, 8, 10)


@pytest.mark.asyncio
async def test_other_patient_same_slot_is_not_a_conflict(db) -> None:
    """別患者の同時刻は占有ではない (索引キーに patient_id を含むため)。"""
    seeded = await _seed(db)
    v = await _visit(db, seeded, date(2026, 8, 10), time(11, 0))
    await _visit(db, seeded, date(2026, 8, 12), time(11, 0), patient=seeded["other"])
    items = [_move_item(seeded, v, to_date=date(2026, 8, 12), to_start=time(11, 0))]

    summary = await _apply(db, items)

    assert (summary.updated, summary.failed) == (1, 0)
