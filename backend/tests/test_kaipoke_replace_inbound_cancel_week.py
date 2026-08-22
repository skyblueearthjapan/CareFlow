"""置換取り込みが「今週だけ取消」を白紙化・復活させないこと (H-4).

正典 = docs/plans/week-cockpit-design.md 決定 D1 (週空間 Phase E)。

置換は対象日の visit を cancelled 含めて白紙化し、カイポケ現況で作り直す。
らく助側で「今週だけ取消」した枠 (= まだ⇧送信していない) がそこに居ると
取消が黙って復活するため、**日単位**で止める:

  * 実適用 → ReplaceBlockedError → エンドポイントは 422
  * プレビュー (dry-run) → その日を対象から外し、カイポケ行を skipped に積む
    (プレビューで 500 を出さない・smart-inbound のプレビュー経路は例外を
    捕まえていないため)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
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


def _entries():
    """カイポケ現況 = 火 14:00 / 金 09:00 の 2 行 (ScheduleEntry)。"""
    from app.services.diff.engine import ScheduleEntry

    return [
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
            entries=_entries(),
            dry_run=False,
            now=datetime.now(UTC),
        )
    msg = str(exc.value)
    assert "今週だけ取消済み" in msg
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
        _kp_row(FRI, time(9, 0), time(9, 30)),
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res.status_code == 422, res.text
    assert "今週だけ取消済み" in res.json()["detail"]

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
        entries=_entries(),
        dry_run=True,
        now=datetime.now(UTC),
    )

    # 火曜は白紙化対象から外れる (seed は火・水・木の 3 件 → 火を除く 2 件)
    assert plan.wiped == 2
    reasons = [s.reason for s in plan.skipped if s.date == TUE.isoformat()]
    assert reasons, plan.skipped
    assert "今週だけ取消済み" in reasons[0]
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
    assert not [s for s in plan.skipped if "今週だけ取消済み" in s.reason]


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
    assert not [s for s in plan.skipped if "今週だけ取消済み" in s.reason]
