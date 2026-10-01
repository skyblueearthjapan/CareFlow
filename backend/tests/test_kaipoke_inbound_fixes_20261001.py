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
from app.models.kaipoke_job import KaipokeJob
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


# --- 2. 跨ぎ date_change と移動先 delete の同居 --------------------------------


def _row(d: date, start: time, end: time, staff: str, service: str):
    from app.services.kaipoke.csv_builder import KaipokeCsvRow, StaffCell

    return KaipokeCsvRow(
        patient_name=PATIENT_NAME,
        visit_date=d,
        start_time=start,
        end_time=end,
        office_name="稲毛",
        business_type="医療保険",
        service_content=service,
        primary=StaffCell(name=staff, qualification="看護師"),
    )


def test_engine_inbound_pairs_same_slot_before_date_change() -> None:
    """担当が替わって区分 (正看/准看) が違う同日同時刻の枠は edit で結ぶ。

    9/24 の形: らく助 = 21日 10:00 (A) と 22日 10:00 (B・別の担当)、
    カイポケ = 22日 10:00 (A の担当) だけ。従来は A を 22日へ date_change・B を
    delete に割り、適用で失敗していた。
    """
    from app.services.diff.engine import compare_schedules_from_content

    rakusuke = _csv(
        _row(date(2026, 7, 7), time(10, 0), time(10, 35), STAFF_NAME, "精神基本療養費Ⅰ・正看"),
        _row(date(2026, 7, 8), time(10, 0), time(10, 35), "佐藤　次郎", "精神基本療養費Ⅰ・准看"),
    )
    kaipoke = _csv(
        _row(date(2026, 7, 8), time(10, 0), time(10, 35), STAFF_NAME, "精神基本療養費Ⅰ・正看"),
    )

    def _actions(**kw) -> list[tuple[str, str, str]]:
        cs = compare_schedules_from_content(
            rakusuke,
            kaipoke,
            target_week_start=6,
            target_week_end=12,
            normalize_names=True,
            flag_grade_change=False,
            **kw,
        )
        return sorted((c.action, c.date_from, c.date_to) for c in cs)

    # inbound (prefer_same_slot=True): 8日は同じ枠の担当変更 (edit)・7日は delete。
    assert _actions(prefer_same_slot=True) == [("delete", "7", ""), ("edit", "8", "8")]
    # 既定 (outbound 等) は従来どおり — 挙動を変えていないことの確認。
    assert ("date_change", "7", "8") in _actions()


async def _add_visit(db, seeded, d: date, start: time, end: time, *, status="planned") -> Visit:
    v = Visit(
        patient_id=seeded["patient"].id,
        visit_date=d,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        source="auto",
        required_staff_count=1,
        primary_staff_id=seeded["staff"].id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_date_change_into_slot_cancelled_in_same_run(db, dry_run: bool) -> None:
    """同じ実行で取り消す枠への date_change は失敗しない (取消行を整理して移る)。"""
    seeded = await _seed_week(db)
    tue = seeded["tue"]  # 7/7 10:00
    wed10 = await _add_visit(db, seeded, date(2026, 7, 8), time(10, 0), time(10, 35))
    items = await _sheet_items(
        db,
        [
            # シート順は「移動 → 削除」(並べ替えで削除が先に処理されること)
            {
                "patient_id": seeded["patient"].id,
                "visit_id": tue.id,
                "action": "date_change",
                "before": _side(7, "10:00", "10:35"),
                "after": _side(8, "10:00", "10:35"),
            },
            {
                "patient_id": seeded["patient"].id,
                "visit_id": wed10.id,
                "action": "delete",
                "before": _side(8, "10:00", "10:35"),
                "after": EMPTY,
            },
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
    assert summary.failed == 0, [r.detail for r in summary.results]
    assert (summary.cancelled, summary.updated) == (1, 1)
    if dry_run:
        return
    await db.flush()
    await db.refresh(tue)
    await db.refresh(wed10)
    assert (tue.visit_date, tue.start_time, tue.status) == (
        date(2026, 7, 8),
        time(10, 0),
        "planned",
    )
    assert wed10.status == "cancelled" and wed10.deleted_at is not None


@pytest.mark.asyncio
async def test_smart_holiday_shift_shape_keeps_target_visit(client, db, stub_kaipoke) -> None:  # noqa: F811
    """9/24 の再現 (smart): 置換日 (火) の訪問 A と打刻日 (水) の同時刻の訪問 B。

    カイポケは水 10:00 (A の担当) だけ。B の担当は准看護師なので区分が違う。
    従来: A を水へ date_change・B を delete → B 取消・A は移動失敗・火の置換見送り。
    根治後: B を担当変更 (edit) で残し、A は置換で白紙化。失敗 0・見送り 0。
    """
    from tests.test_kaipoke_inbound import _seed_second_staff

    seeded = await _seed_week(db)
    sato = await _seed_second_staff(db, seeded["office"])
    sato.qualification = "准看護師"
    await db.commit()
    tue = seeded["tue"]  # A: 7/7 10:00 田中
    b = await _add_visit(db, seeded, date(2026, 7, 8), time(10, 0), time(10, 35))
    b.primary_staff_id = sato.id
    await db.commit()
    await _checkin(db, b, sato.id)  # 水曜 = 打刻日 (差分担当)
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 8), time(10, 0), time(10, 35)),  # 田中・正看
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
    )

    res = await _preview(client, admin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["diffSummary"].get("date_change", 0) == 0
    res2 = await _apply(client, admin, sheet_id=body["sheetId"], dry_run=False)
    assert res2.status_code == 200, res2.text
    out = res2.json()
    assert out["diff"]["failed"] == 0, out["diff"]["results"]

    await db.refresh(b)
    await db.refresh(tue)
    assert b.status == "planned" and b.deleted_at is None
    assert b.primary_staff_id == seeded["staff"].id  # カイポケの担当へ
    assert tue.deleted_at is not None  # 置換日の A は白紙化 (カイポケに無い)
    job = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.params["op"].as_string() == "smart-apply")
    )
    assert job is not None and job.params.get("held_days") == []


@pytest.mark.asyncio
async def test_date_change_into_locally_cancelled_slot_still_fails(db) -> None:
    """らく助側の意思による取消 (今週だけ取消) の枠は退かさない (従来どおり failed)。"""
    seeded = await _seed_week(db)
    tue = seeded["tue"]
    wed10 = await _add_visit(
        db, seeded, date(2026, 7, 8), time(10, 0), time(10, 35), status="cancelled"
    )
    wed10.source = "manual_cancel"
    await db.commit()
    items = await _sheet_items(
        db,
        [
            {
                "patient_id": seeded["patient"].id,
                "visit_id": tue.id,
                "action": "date_change",
                "before": _side(7, "10:00", "10:35"),
                "after": _side(8, "10:00", "10:35"),
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
    assert summary.failed == 1 and "別の予定があります" in summary.results[0].detail


# --- 3. 担当2 の解除で取込由来の同行を外す --------------------------------------


async def _seed_trainee_accompaniment(db, seeded, *, source: str) -> tuple[Any, Any]:
    from app.models.accompaniment import Accompaniment
    from app.models.staff import Staff

    trainee = Staff(name="小西　新人", role="staff", primary_office_id=seeded["office"].id)
    trainee.qualification = "看護師"
    trainee.is_trainee = True
    db.add(trainee)
    await db.flush()
    acc = Accompaniment(
        accompanying_staff_id=trainee.id,
        target_type="visit",
        visit_id=seeded["tue"].id,
        source=source,
        kind="trainee",
    )
    db.add(acc)
    await db.commit()
    return trainee, acc


def _staff2_clear_item(seeded) -> dict:
    return {
        "patient_id": seeded["patient"].id,
        "visit_id": seeded["tue"].id,
        "action": "edit",
        "before": _side(7, "10:00", "10:35", staff2="小西　新人"),
        "after": _side(7, "10:00", "10:35", staff2=""),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_staff2_clear_removes_import_accompaniment(db, dry_run: bool) -> None:
    from app.models.accompaniment import Accompaniment

    seeded = await _seed_week(db)
    _trainee, acc = await _seed_trainee_accompaniment(db, seeded, source="import")
    acc_id = acc.id
    items = await _sheet_items(db, [_staff2_clear_item(seeded)])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=dry_run,
        now=NOW,
    )
    assert summary.updated == 1, [r.detail for r in summary.results]
    assert "同行「小西　新人」を外しました" in summary.results[0].detail
    await db.flush()
    remaining = await db.scalar(select(Accompaniment).where(Accompaniment.id == acc_id))
    assert (remaining is None) is (not dry_run)


@pytest.mark.asyncio
async def test_staff2_clear_keeps_manual_accompaniment_with_note(db) -> None:
    """画面で人が張った同行は外さない (要確認の注記のみ)。"""
    from app.models.accompaniment import Accompaniment

    seeded = await _seed_week(db)
    _trainee, acc = await _seed_trainee_accompaniment(db, seeded, source="manual")
    acc_id = acc.id
    items = await _sheet_items(db, [_staff2_clear_item(seeded)])

    summary = await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=NOW,
    )
    assert summary.failed == 0
    assert "らく助で設定された同行のため残しています" in summary.results[0].detail
    await db.flush()
    assert await db.scalar(select(Accompaniment).where(Accompaniment.id == acc_id)) is not None


# --- 4. date_change でコースが移動先の日へ付け替わる ----------------------------


def _date_change_item(seeded, *, to_day: int) -> dict:
    return {
        "patient_id": seeded["patient"].id,
        "visit_id": seeded["tue"].id,
        "action": "date_change",
        "before": _side(7, "10:00", "10:35"),
        "after": _side(to_day, "10:00", "10:35"),
    }


@pytest.mark.asyncio
async def test_date_change_moves_course_to_target_day(db) -> None:
    from tests.test_kaipoke_inbound import _seed_course

    seeded = await _seed_week(db)
    tue_course = await _seed_course(
        db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A"
    )
    wed_course = await _seed_course(
        db, office=seeded["office"], staff=seeded["staff"], weekday=2, code="B"
    )
    tue = seeded["tue"]
    tue.course_id = tue_course.id
    await db.commit()
    items = await _sheet_items(db, [_date_change_item(seeded, to_day=8)])

    dry = await apply_inbound_items(
        db, items=items, week_start=WEEK_START, week_end=WEEK_END, days=None, dry_run=True, now=NOW
    )
    assert "コースA→B" in dry.results[0].detail

    summary = await apply_inbound_items(
        db, items=items, week_start=WEEK_START, week_end=WEEK_END, days=None, dry_run=False, now=NOW
    )
    assert summary.updated == 1
    await db.flush()
    await db.refresh(tue)
    assert tue.visit_date == date(2026, 7, 8)
    assert tue.course_id == wed_course.id


@pytest.mark.asyncio
async def test_date_change_without_target_course_creates_temp_course(db) -> None:
    from app.models.course import Course
    from tests.test_kaipoke_inbound import _seed_course

    seeded = await _seed_week(db)
    tue_course = await _seed_course(
        db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A"
    )
    tue = seeded["tue"]
    tue.course_id = tue_course.id
    await db.commit()
    items = await _sheet_items(db, [_date_change_item(seeded, to_day=10)])  # 金曜

    summary = await apply_inbound_items(
        db, items=items, week_start=WEEK_START, week_end=WEEK_END, days=None, dry_run=False, now=NOW
    )
    assert summary.updated == 1, [r.detail for r in summary.results]
    await db.flush()
    await db.refresh(tue)
    course = await db.get(Course, tue.course_id)
    assert course is not None
    assert (course.weekday, course.code, course.assigned_staff_id) == (4, "臨", seeded["staff"].id)
