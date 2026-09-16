"""置換取り込みが「今週だけ取消」を白紙化・復活させないこと (H-4).

正典 = docs/plans/week-cockpit-design.md 決定 D1 (週空間 Phase E)。

置換は対象日の visit を cancelled 含めて白紙化し、カイポケ現況で作り直す。
らく助側で「今週だけ取消」した枠 (= まだ⇧送信していない) がそこに居ると
取消が黙って復活するため、**日単位**で止める:

  * 実適用 → ReplaceBlockedError → エンドポイントは 422
  * プレビュー (dry-run) → その日を対象から外し、カイポケ行を skipped に積む
    (プレビューで 500 を出さない・smart-inbound のプレビュー経路は例外を
    捕まえていないため)

2026-09-16 (A-1 / mobile-staff-schedule-design-2026-09-16.md §1): ブロックするのは
**その取消枠がカイポケ現況にまだ残っている場合だけ**。事務がカイポケ側で既に消して
いれば置換しても取消は復活しないため、止めない (本番 W38/W39 の 422 の正体)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
    VISIT_SOURCE_STATUS_CANCEL,
    VISIT_STATUS_CANCELLED,
    Visit,
)
from app.services.kaipoke.replace_inbound import (
    ReplaceBlockedError,
    replace_week_from_kaipoke,
)
from tests.test_kaipoke_inbound import (
    MONTH,
    WEEK_START,
    _kp_row,
    _seed_course,
    _seed_week,
)
from tests.test_kaipoke_replace_inbound import (
    StubKaipokeClient,
    _csv,
    _make_admin,
    _post_replace,
)


@pytest.fixture
def stub_kaipoke():
    """export を差し替えるスタブ (test_kaipoke_replace_inbound と同じ作法)。"""
    from app.services import kaipoke_client as kc_module

    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


TUE = date(2026, 7, 7)
FRI = date(2026, 7, 10)


async def _cancel(db, visit: Visit, *, source: str = VISIT_SOURCE_MANUAL_CANCEL) -> None:
    """らく助側の「今週だけ取消」を再現する (status + source='manual_cancel')。"""
    visit.status = VISIT_STATUS_CANCELLED
    visit.source = source
    await db.commit()


def _entries(*, keep_cancelled_slot: bool = False):
    """カイポケ現況 = 火 14:00 / 金 09:00 の 2 行 (ScheduleEntry)。

    ``keep_cancelled_slot=True`` は、らく助で取消した枠 (火 10:00) が **カイポケに
    まだ残っている** 状態 (= 置換すると取消が復活する) を作る。2026-09-16 の A-1 で
    ブロックはこのケースだけに絞られた (docs/plans/mobile-staff-schedule-design-2026-09-16.md §1)。
    """
    from app.services.diff.engine import ScheduleEntry

    rows = [
        ScheduleEntry(
            user_name="山田　花子",
            date="7",
            weekday="火",
            business_type="医療保険",
            service_type="精神基本療養費Ⅰ・正看",
            start_time="14:00",
            end_time="14:35",
            staff1_name="田中　看護師",
            staff1_type="正看護師",
        ),
        ScheduleEntry(
            user_name="山田　花子",
            date="10",
            weekday="金",
            business_type="医療保険",
            service_type="精神基本療養費Ⅰ・正看",
            start_time="09:00",
            end_time="09:30",
            staff1_name="田中　看護師",
            staff1_type="正看護師",
        ),
    ]
    if keep_cancelled_slot:
        rows.append(
            ScheduleEntry(
                user_name="山田　花子",
                date="7",
                weekday="火",
                business_type="医療保険",
                service_type="精神基本療養費Ⅰ・正看",
                start_time="10:00",
                end_time="10:35",
                staff1_name="田中　看護師",
                staff1_type="正看護師",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# 1) 実適用は ReplaceBlockedError (エンドポイントで 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_real_apply_blocked_by_cancelled_visit(db) -> None:
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"])

    with pytest.raises(ReplaceBlockedError) as exc:
        await replace_week_from_kaipoke(
            db,
            week_start=WEEK_START,
            entries=_entries(keep_cancelled_slot=True),
            dry_run=False,
            now=datetime.now(UTC),
        )
    msg = str(exc.value)
    assert "らく助側で取消済み" in msg
    assert "⇧送信" in msg
    assert TUE.isoformat() in msg


@pytest.mark.asyncio
async def test_replace_endpoint_returns_422_when_cancelled_exists(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _cancel(db, seeded["tue"])
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(TUE, time(14, 0), time(14, 35)),
        # 取消した枠 (火 10:00) がカイポケにまだ残っている = ブロック対象 (A-1)
        _kp_row(TUE, time(10, 0), time(10, 35)),
        _kp_row(FRI, time(9, 0), time(9, 30)),
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res.status_code == 422, res.text
    assert "らく助側で取消済み" in res.json()["detail"]

    # 何も消えていない・取消も取消のまま
    rows = list(
        (
            await db.scalars(
                select(Visit)
                .where(Visit.deleted_at.is_(None))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert len(rows) == 3
    assert {r.id: r.status for r in rows}[seeded["tue"].id] == VISIT_STATUS_CANCELLED


# ---------------------------------------------------------------------------
# 2) プレビュー (dry-run) は当該日だけ外して可視化する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_dry_run_skips_blocked_day_and_reports_reason(db) -> None:
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"])

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(keep_cancelled_slot=True),
        dry_run=True,
        now=datetime.now(UTC),
    )

    # 火曜は白紙化対象から外れる (seed は火・水・木の 3 件 → 火を除く 2 件)
    assert plan.wiped == 2
    reasons = [s.reason for s in plan.skipped if s.date == TUE.isoformat()]
    assert reasons, plan.skipped
    assert "らく助側で取消済み" in reasons[0]
    assert "⇧送信" in reasons[0]
    # 金曜のカイポケ行は通常どおり計画に乗る
    assert plan.inserted >= 1


@pytest.mark.asyncio
async def test_replace_dry_run_is_normal_without_cancelled(db) -> None:
    """対照: 取消が無ければ従来どおり全日が対象 (退行防止)。"""
    await _seed_week(db)

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(),
        dry_run=True,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 3
    assert not [s for s in plan.skipped if "らく助側で取消済み" in s.reason]


@pytest.mark.asyncio
async def test_replace_allows_inbound_delete_cancelled(db) -> None:
    """取込 delete 由来の cancelled (source はそのまま) は置換してよい.

    止めたいのは「らく助側の今週だけ取消」(source='manual_cancel') だけ。
    """
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"], source="import")

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(),
        dry_run=True,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 3  # 火曜も白紙化対象
    assert not [s for s in plan.skipped if "らく助側で取消済み" in s.reason]


@pytest.mark.asyncio
async def test_replace_real_apply_blocked_by_status_cancel(db) -> None:
    """患者ステータス連動の取消 (source='status_cancel') も日単位で置換を止める.

    正典 = docs/plans/patient-status-schedule-design-2026-09-09.md §7-3(d)。

    **前提: 患者は稼働中** (status='active')。ステータスを戻した直後などに status_cancel
    の行が残っているかたち。非稼働のままなら Phase 1 が挿入しないためブロックしない
    (下の ``test_replace_not_blocked_by_status_cancel_of_inactive_patient``)。
    """
    seeded = await _seed_week(db)
    assert seeded["patient"].status == "active"
    await _cancel(db, seeded["tue"], source=VISIT_SOURCE_STATUS_CANCEL)

    with pytest.raises(ReplaceBlockedError) as exc:
        await replace_week_from_kaipoke(
            db,
            week_start=WEEK_START,
            entries=_entries(keep_cancelled_slot=True),
            dry_run=False,
            now=datetime.now(UTC),
        )
    msg = str(exc.value)
    assert "らく助側で取消済み" in msg
    assert TUE.isoformat() in msg


@pytest.mark.asyncio
async def test_replace_dry_run_skips_status_cancel_day(db) -> None:
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"], source=VISIT_SOURCE_STATUS_CANCEL)

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(keep_cancelled_slot=True),
        dry_run=True,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 2
    reasons = [s.reason for s in plan.skipped if s.date == TUE.isoformat()]
    assert reasons, plan.skipped
    assert "らく助側で取消済み" in reasons[0]


# ---------------------------------------------------------------------------
# 3) A-1: カイポケ現況に残っていない取消はブロックしない (2026-09-16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_not_blocked_when_cancelled_slot_gone_from_kaipoke(db) -> None:
    """事務がカイポケ側で既に消した枠 = 置換しても復活しない → 止めない。

    seed の火曜は 10:00。カイポケ現況 (既定の ``_entries()``) は 火 14:00 / 金 09:00 で
    10:00 の行が無い = 本番 W38 の 小湊様 と同じ形。
    """
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"])

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(),
        dry_run=True,
        now=datetime.now(UTC),
    )

    assert plan.wiped == 3  # 火曜も白紙化対象 (取消行はそのまま消える)
    assert not [s for s in plan.skipped if "らく助側で取消済み" in s.reason]


@pytest.mark.asyncio
async def test_replace_real_apply_not_blocked_when_cancelled_slot_gone(db) -> None:
    """実適用も 422 にならない (訪問の取込が丸ごと落ちない)。"""
    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"])

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(),
        dry_run=False,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 3
    assert plan.inserted >= 1


@pytest.mark.asyncio
async def test_replace_not_blocked_by_status_cancel_of_inactive_patient(db) -> None:
    """非稼働患者の status_cancel は、同じ枠がカイポケに残っていてもブロックしない.

    Phase 1 が非稼働患者の行を ``inactive_patient`` として挿入しない以上、置換しても
    取消は復活しない = 止める理由がない。止めてしまうと「ステータスを非稼働にした患者が
    カイポケ側に残っている」間ずっと、その週の訪問取込が丸ごと 422 で落ちる。
    """
    seeded = await _seed_week(db)
    seeded["patient"].status = "inactive"
    await db.commit()
    await _cancel(db, seeded["tue"], source=VISIT_SOURCE_STATUS_CANCEL)

    # dry-run: 例外なし・ブロック理由の skip も出ない
    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(keep_cancelled_slot=True),
        dry_run=True,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 3  # 火曜も白紙化対象
    assert not [s for s in plan.skipped if "らく助側で取消済み" in s.reason]
    # 代わりに「非稼働のため取り込まない」として可視化される (Phase 1)
    assert [s for s in plan.skipped if s.code == "inactive_patient"]

    # 実適用も 422 にならない
    applied = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=_entries(keep_cancelled_slot=True),
        dry_run=False,
        now=datetime.now(UTC),
    )
    assert applied.wiped == 3
    assert applied.inserted == 0  # 非稼働なので 1 件も入らない


@pytest.mark.asyncio
async def test_replace_blocked_when_cancel_time_has_microseconds(db) -> None:
    """退避値の μs (例 10:00:00.000001) を持つ取消行も 10:00 のカイポケ行と一致する.

    ``_hhmm`` の分単位丸め (取込スワップの μs 退避対策) が効いていないと、
    ブロック判定が μs 違いで空振りして取消が黙って復活する。
    """
    seeded = await _seed_week(db)
    seeded["tue"].start_time = time(10, 0, 0, 1)
    await db.commit()
    await _cancel(db, seeded["tue"])

    with pytest.raises(ReplaceBlockedError) as exc:
        await replace_week_from_kaipoke(
            db,
            week_start=WEEK_START,
            entries=_entries(keep_cancelled_slot=True),
            dry_run=False,
            now=datetime.now(UTC),
        )
    assert TUE.isoformat() in str(exc.value)


@pytest.mark.asyncio
async def test_replace_not_blocked_by_other_patient_same_slot(db) -> None:
    """別患者の同日同時刻の行があるだけではブロックしない。"""
    from app.models.patient import Patient
    from app.services.diff.engine import ScheduleEntry

    seeded = await _seed_week(db)
    await _cancel(db, seeded["tue"])
    other = Patient(
        code="PT-INB-2",
        name="鈴木　太郎",
        status="active",
        insurance="medical",
        primary_office_id=seeded["office"].id,
    )
    db.add(other)
    await db.commit()

    entries = [
        *_entries(),
        ScheduleEntry(
            user_name="鈴木　太郎",
            date="7",
            weekday="火",
            business_type="医療保険",
            service_type="精神基本療養費Ⅰ・正看",
            start_time="10:00",  # 取消された枠と同じ時刻・患者だけが違う
            end_time="10:35",
            staff1_name="田中　看護師",
            staff1_type="正看護師",
        ),
    ]

    plan = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=entries,
        dry_run=True,
        now=datetime.now(UTC),
    )
    assert plan.wiped == 3
    assert not [s for s in plan.skipped if "らく助側で取消済み" in s.reason]
