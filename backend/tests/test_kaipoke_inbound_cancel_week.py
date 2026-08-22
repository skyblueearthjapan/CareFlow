"""取込 (diff-inbound) が「今週だけ取消」を勝手に復活させないこと.

正典 = docs/plans/week-cockpit-design.md 決定 D1 (週空間 Phase E)。

`inbound.apply_inbound_items` の add 分岐は、同時刻に cancelled な visit が
居るとき **同一実行内の delete+add ペア (名寄せ差)** に限って枠を復活させる。
らく助側で「今週だけ取消」しただけの (= まだ⇧送信していない) 枠は復活させず
failed にして可視化する — 黙って復活すると利用者の意思に反して訪問が戻る。

検証観点:
  1. 事前から cancelled の枠へ add → failed + 案内文言 / status は cancelled のまま
  2. 同一実行内の delete+add ペアは従来どおり復活する (退行していないこと)
  3. dry-run でも同じ判定になる (実適用と結果が一致する)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
    VISIT_STATUS_CANCELLED,
    Visit,
)
from app.services.kaipoke.inbound import apply_inbound_items

WEEK_START = date(2026, 7, 6)  # 月
WEEK_END = date(2026, 7, 11)  # 土
TUE = date(2026, 7, 7)
MONTH = "2026-07"
PATIENT_NAME = "山田　花子"
STAFF_NAME = "田中　看護師"


async def _seed(db, *, status: str, source: str = "auto") -> dict:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-CW-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()
    visit = Visit(
        patient_id=patient.id,
        visit_date=TUE,
        start_time=time(10, 0),
        end_time=time(10, 35),
        type="regular",
        status=status,
        source=source,
        required_staff_count=1,
        primary_staff_id=staff.id,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return {"office": office, "staff": staff, "patient": patient, "visit": visit}


async def _sheet_with(db, items_spec: list[dict]) -> list[CorrectionSheetItem]:
    sheet = CorrectionSheet(
        target_month=MONTH,
        direction="inbound",
        week_start=WEEK_START,
        week_end=WEEK_END,
    )
    db.add(sheet)
    await db.flush()
    rows = [CorrectionSheetItem(sheet_id=sheet.id, include=True, **spec) for spec in items_spec]
    db.add_all(rows)
    await db.commit()
    return rows


def _add_spec(patient: Patient) -> dict:
    return {
        "action": "add",
        "patient_id": patient.id,
        "after": {
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": "10:00",
            "end_time": "10:40",
            "staff1": STAFF_NAME,
            "staff2": "",
        },
    }


def _delete_spec(patient: Patient, visit: Visit) -> dict:
    return {
        "action": "delete",
        "patient_id": patient.id,
        "visit_id": visit.id,
        "before": {
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": "10:00",
            "end_time": "10:35",
        },
    }


# ---------------------------------------------------------------------------
# 1) 事前から cancelled の枠は復活しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_add_does_not_revive_manual_cancel_visit(db, dry_run: bool) -> None:
    """らく助側の「今週だけ取消」(source='manual_cancel') は復活させない."""
    seeded = await _seed(db, status=VISIT_STATUS_CANCELLED, source=VISIT_SOURCE_MANUAL_CANCEL)
    patient = seeded["patient"]
    visit = seeded["visit"]
    items = await _sheet_with(db, [_add_spec(patient)])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=dry_run,
        now=datetime.now(UTC),
    )
    await db.commit()

    assert summary.added == 0
    assert summary.failed == 1
    if not dry_run:
        assert "今週だけ取消済み" in (items[0].comment or "")
        assert "⇧送信" in (items[0].comment or "")

    # 取消は取消のまま・二重挿入もしない
    rows = list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.patient_id == patient.id,
                    Visit.visit_date == TUE,
                    Visit.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].id == visit.id
    assert rows[0].status == VISIT_STATUS_CANCELLED


# ---------------------------------------------------------------------------
# 2) 同一実行内の delete+add ペアは従来どおり復活する (退行防止)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_run_delete_add_pair_still_revives(db) -> None:
    seeded = await _seed(db, status="planned")
    patient = seeded["patient"]
    visit = seeded["visit"]
    items = await _sheet_with(db, [_add_spec(patient), _delete_spec(patient, visit)])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=datetime.now(UTC),
    )
    await db.commit()

    assert summary.failed == 0
    assert summary.cancelled == 1
    assert summary.added == 1

    rows = list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.patient_id == patient.id,
                    Visit.visit_date == TUE,
                    Visit.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].status == "planned"
    assert rows[0].end_time == time(10, 40)


# ---------------------------------------------------------------------------
# 3) edit / date_change の移動先が占有されていたら failed で継続する (H-5)
#
# visits の partial UNIQUE (patient_id, visit_date, start_time) WHERE
# deleted_at IS NULL があるため、無検査だと flush で IntegrityError → 500 に
# なり、同じ実行の残り item まで巻き添えになる。
# ---------------------------------------------------------------------------


async def _seed_two_visits(db, *, second_status: str) -> dict:
    seeded = await _seed(db, status="planned")
    patient = seeded["patient"]
    other = Visit(
        patient_id=patient.id,
        visit_date=TUE,
        start_time=time(14, 0),
        end_time=time(14, 35),
        type="regular",
        status=second_status,
        source="auto",
        required_staff_count=1,
        primary_staff_id=seeded["staff"].id,
    )
    db.add(other)
    await db.commit()
    await db.refresh(other)
    seeded["other"] = other
    return seeded


def _edit_spec(patient: Patient, *, to_start: str) -> dict:
    return {
        "action": "edit",
        "patient_id": patient.id,
        "before": {
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": "10:00",
            "end_time": "10:35",
            "staff1": STAFF_NAME,
            "staff2": "",
        },
        "after": {
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": to_start,
            "end_time": "14:35",
            "staff1": STAFF_NAME,
            "staff2": "",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("second_status", ["planned", VISIT_STATUS_CANCELLED])
async def test_edit_into_occupied_slot_fails_without_500(db, second_status: str) -> None:
    """移動先が別 visit (cancelled 含む) に取られていたら item を failed に落とす."""
    seeded = await _seed_two_visits(db, second_status=second_status)
    patient = seeded["patient"]
    visit = seeded["visit"]
    items = await _sheet_with(db, [_edit_spec(patient, to_start="14:00")])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=datetime.now(UTC),
    )
    await db.commit()

    assert summary.failed == 1
    assert summary.updated == 0
    assert "移動先" in (items[0].comment or "")

    rows = list(
        (
            await db.scalars(
                select(Visit)
                .where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
                .order_by(Visit.start_time)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert len(rows) == 2
    assert rows[0].id == visit.id
    assert rows[0].start_time == time(10, 0)  # 動いていない
    assert rows[1].start_time == time(14, 0)
    assert rows[1].status == second_status  # 占有していた側も無傷


@pytest.mark.asyncio
async def test_edit_into_free_slot_still_moves(db) -> None:
    """対照: 移動先が空いていれば従来どおり動く (退行防止)."""
    seeded = await _seed(db, status="planned")
    patient = seeded["patient"]
    visit = seeded["visit"]
    items = await _sheet_with(db, [_edit_spec(patient, to_start="14:00")])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=datetime.now(UTC),
    )
    await db.commit()

    assert summary.failed == 0
    assert summary.updated == 1
    rows = list(
        (
            await db.scalars(
                select(Visit).where(Visit.id == visit.id).execution_options(populate_existing=True)
            )
        ).all()
    )
    assert rows[0].start_time == time(14, 0)
    assert rows[0].source == "manual_week"


# ---------------------------------------------------------------------------
# 4) 取込 delete 由来の cancelled は従来どおり復活する (既存仕様の維持)
#
# 「らく助の今週だけ取消」だけを止めたいのであって、取込が付けた cancelled
# (source は元のまま) の復活は既存仕様 — 名寄せ差で delete+add に分解された
# ケースや、前回取込で消えた予定がカイポケに戻ったケースで要る。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_revives_inbound_delete_cancelled_visit(db) -> None:
    seeded = await _seed(db, status=VISIT_STATUS_CANCELLED, source="import")
    patient = seeded["patient"]
    visit = seeded["visit"]
    items = await _sheet_with(db, [_add_spec(patient)])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=datetime.now(UTC),
    )
    await db.commit()

    assert summary.failed == 0
    assert summary.added == 1

    rows = list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.patient_id == patient.id,
                    Visit.visit_date == TUE,
                    Visit.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].id == visit.id
    assert rows[0].status == "planned"  # 復活する
    assert rows[0].end_time == time(10, 40)
