"""services/kaipoke/sources.py — カイポケの材料の取り直し (RPA はモック).

正典: docs/plans/shared-foundation-leave-acceptance-2026-10-20.md §0-2〜0-4。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.kaipoke_job import KaipokeJob
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.staff import Staff
from app.services.kaipoke import sources
from app.services.kaipoke.sources import (
    KIND_ACTUAL_CSV,
    KIND_PLAN_CSV,
    KIND_TASKS,
    OP_ACCEPTANCE_REFRESH,
    OP_LEAVE_COMPARE,
    OP_LEAVE_OUTBOUND,
    RpaJobConflictError,
    covered_dates_of,
    evening_warning,
    fetch_month_csv,
    fetch_week_tasks,
    finish_source_job,
    start_source_job,
)
from app.services.kaipoke_client import KaipokeBusyError

HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,事業所名,日付,曜日,"
    "利用者,業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)
CSV_TEXT = (
    "﻿" + HEADER + "\n"
    "山田　花子,看護師,-,,,,,,稲毛,1,水,患者Ａ,医療保険,訪問看護,09:00,10:00,60,\n"
    "鈴木 一郎,看護師,知らない人,看護師,,,,,都賀,2,木,患者Ｂ,医療保険,訪問看護,10:00,11:00,60,\n"
    "\n"
)
MONDAY = date(2026, 10, 19)


class StubClient:
    """RPA のモック。``busy`` は status() が実行中を返す残り回数。"""

    def __init__(
        self,
        *,
        export_results: list[Any] | None = None,
        tasks_results: list[Any] | None = None,
        busy: int = 0,
    ) -> None:
        self.export_results = list(export_results or [])
        self.tasks_results = list(tasks_results or [])
        self.busy = busy
        self.status_calls = 0
        self.export_payloads: list[dict[str, Any]] = []
        self.tasks_payloads: list[dict[str, Any]] = []

    async def status(self) -> dict[str, Any]:
        self.status_calls += 1
        if self.busy > 0:
            self.busy -= 1
            return {"current_task": {"running": True}}
        return {"current_task": {"running": False}}

    @staticmethod
    def _next(queue: list[Any]) -> dict[str, Any]:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def export(self, payload: dict[str, Any], *, timeout: float | None = None) -> dict:
        self.export_payloads.append(dict(payload))
        return self._next(self.export_results)

    async def individual_tasks(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict:
        self.tasks_payloads.append(dict(payload))
        return self._next(self.tasks_results)


def export_ok(csv_text: str, division: str, row_count: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"success": True, "csv_content": csv_text, "division": division}
    if row_count is not None:
        result["row_count"] = row_count
    return {"result": result}


def tasks_ok(tasks: list[dict[str, Any]], week_dates: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"success": True, "tasks": tasks}
    if week_dates is not None:
        result["week_dates"] = week_dates
    return {"success": True, "result": result}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "RETRY_INTERVAL_SECONDS", 0.0)


async def _staff(db, *names: str) -> None:
    for name in names:
        db.add(Staff(name=name))
    await db.commit()


async def _snapshot(db, kind: str, period_start: date) -> KaipokeSourceSnapshot | None:
    return await db.scalar(
        select(KaipokeSourceSnapshot)
        .where(
            KaipokeSourceSnapshot.kind == kind,
            KaipokeSourceSnapshot.period_start == period_start,
        )
        .execution_options(populate_existing=True)
    )


async def _job(db, *, op: str = "events-preview", minutes_ago: int = 0, status: str = "running"):
    created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    job = KaipokeJob(
        job_type="fetch",
        status=status,
        week_start=MONDAY,
        params={"op": op},
        created_at=created,
        updated_at=created,
    )
    db.add(job)
    await db.commit()
    return job


# --- CSV ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_csv_saved_by_header_with_unmatched_names(db) -> None:
    await _staff(db, "山田花子", "鈴木一郎")
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan")])

    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")

    assert result.status == "ok"
    assert result.row_count == 2
    # 「-」(担当なし) と空は名前として数えない。結び付かない名前は件数とともに返す。
    assert result.unmatched_names == {"知らない人": 1}
    assert client.export_payloads == [{"month": "2026-10", "division": "plan", "async": False}]

    snap = await _snapshot(db, KIND_PLAN_CSV, date(2026, 10, 1))
    assert snap is not None
    assert snap.id == result.snapshot_id
    assert snap.status == "ok"
    assert snap.row_count == 2
    assert snap.office_id is None
    assert snap.covered_dates is None
    assert snap.rows[0]["職員名１"] == "山田　花子"
    assert snap.rows[1]["事業所名"] == "都賀"  # 拠点で行を絞らない


@pytest.mark.asyncio
async def test_actual_csv_explicit_zero_rows_is_empty(db) -> None:
    client = StubClient(export_results=[export_ok("", "actual", row_count=0)])

    result = await fetch_month_csv(db, client, KIND_ACTUAL_CSV, "2026-11")

    assert result.status == "empty"
    snap = await _snapshot(db, KIND_ACTUAL_CSV, date(2026, 11, 1))
    assert snap is not None and snap.status == "empty" and snap.row_count == 0
    assert snap.rows == []


@pytest.mark.asyncio
async def test_header_only_csv_is_empty(db) -> None:
    client = StubClient(export_results=[export_ok(HEADER + "\n", "actual")])
    result = await fetch_month_csv(db, client, KIND_ACTUAL_CSV, "2026-10")
    assert result.status == "empty"


@pytest.mark.asyncio
async def test_failure_keeps_previous_ok_row(db) -> None:
    client = StubClient(
        export_results=[
            export_ok(CSV_TEXT, "plan"),
            {"result": {"success": False, "error": "古いファイル"}},
        ]
    )
    first = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")
    second = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")

    assert second.status == "failed"
    assert "古いファイル" in (second.error or "")
    snap = await _snapshot(db, KIND_PLAN_CSV, date(2026, 10, 1))
    assert snap is not None
    assert snap.id == first.snapshot_id
    assert snap.status == "ok"
    assert snap.row_count == 2


@pytest.mark.asyncio
async def test_failure_without_previous_row_is_recorded(db) -> None:
    # 区分のエコーバックが無い (RPA が予実区分に未対応) も失敗。
    client = StubClient(export_results=[{"result": {"success": True, "csv_content": CSV_TEXT}}])

    result = await fetch_month_csv(db, client, KIND_ACTUAL_CSV, "2026-10")

    assert result.status == "failed"
    snap = await _snapshot(db, KIND_ACTUAL_CSV, date(2026, 10, 1))
    assert snap is not None and snap.status == "failed" and snap.error
    assert snap.rows == []


@pytest.mark.asyncio
async def test_retake_replaces_row(db) -> None:
    one_row = "﻿" + HEADER + "\n" + CSV_TEXT.splitlines()[1] + "\n"
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan"), export_ok(one_row, "plan")])
    await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")
    await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")

    rows = (await db.scalars(select(KaipokeSourceSnapshot))).all()
    assert len(rows) == 1
    assert rows[0].row_count == 1


# --- RPA の空き ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_waits_while_rpa_busy_then_fetches(db) -> None:
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan")], busy=2)
    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")
    assert result.status == "ok"
    assert client.status_calls == 3


@pytest.mark.asyncio
async def test_retries_when_export_itself_is_busy(db) -> None:
    client = StubClient(
        export_results=[KaipokeBusyError({"error": "busy"}), export_ok(CSV_TEXT, "plan")]
    )
    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")
    assert result.status == "ok"
    assert len(client.export_payloads) == 2


@pytest.mark.asyncio
async def test_gives_up_after_max_retries(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "MAX_RETRIES", 2)
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan")], busy=100)

    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10")

    assert result.status == "failed"
    assert result.error == "カイポケが空かなかったため取れませんでした"
    assert client.status_calls == 3  # 最初 + 再試行 2 回
    assert client.export_payloads == []


@pytest.mark.asyncio
async def test_other_running_job_blocks_each_fetch(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "MAX_RETRIES", 1)
    own = await _job(db, op=OP_ACCEPTANCE_REFRESH)
    await _job(db, op="events-preview")
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan")])

    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10", job_id=own.id)

    assert result.status == "failed"
    assert "送信・取り込み" in (result.error or "")
    assert client.export_payloads == []


@pytest.mark.asyncio
async def test_own_job_does_not_block(db) -> None:
    own = await _job(db, op=OP_LEAVE_COMPARE)
    client = StubClient(export_results=[export_ok(CSV_TEXT, "plan")])

    result = await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10", job_id=own.id)

    assert result.status == "ok"
    snap = await _snapshot(db, KIND_PLAN_CSV, date(2026, 10, 1))
    assert snap is not None and snap.job_id == own.id


# --- 個別業務 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_tasks_saved_with_covered_dates(db) -> None:
    await _staff(db, "山田花子")
    tasks = [
        {"external_key": "1:2:2026-10-19", "date": "2026-10-19", "start": "09:00",
         "end": "18:00", "title": "休み", "staff_name": "山田　花子"},
        {"external_key": "3:4:2026-10-20", "date": "2026-10-20", "start": "08:45",
         "end": "09:00", "title": "朝会", "staff_name": "辞めた人"},
    ]  # fmt: skip
    week_dates = [f"2026-10-{d}" for d in range(19, 26)]
    client = StubClient(tasks_results=[tasks_ok(tasks, week_dates)])

    result = await fetch_week_tasks(db, client, MONDAY)

    assert result.status == "ok"
    assert result.row_count == 2
    assert result.unmatched_names == {"辞めた人": 1}
    assert client.tasks_payloads == [{"date": "2026-10-19"}]  # 同期モード (async・job_id なし)
    snap = await _snapshot(db, KIND_TASKS, MONDAY)
    assert snap is not None
    assert snap.rows == tasks  # RPA の配列そのまま
    assert snap.covered_dates == week_dates
    assert snap.office_id is None


@pytest.mark.asyncio
async def test_week_tasks_empty_and_failed(db) -> None:
    client = StubClient(
        tasks_results=[
            tasks_ok([], ["2026-10-19"]),
            {"success": False, "error": "ログインできません", "result": {}},
        ]
    )
    empty = await fetch_week_tasks(db, client, MONDAY)
    failed = await fetch_week_tasks(db, client, MONDAY)

    assert empty.status == "empty"
    assert failed.status == "failed"
    assert "ログインできません" in (failed.error or "")
    snap = await _snapshot(db, KIND_TASKS, MONDAY)
    assert snap is not None and snap.status == "empty"  # 失敗は取れた行を消さない
    assert snap.covered_dates == ["2026-10-19"]


@pytest.mark.asyncio
async def test_week_tasks_requires_monday(db) -> None:
    with pytest.raises(ValueError):
        await fetch_week_tasks(db, StubClient(), date(2026, 10, 20))


# --- ジョブ --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_job_conflicts_with_any_active_rpa_job(db) -> None:
    await _job(db, op="push-apply", status="pending")
    with pytest.raises(RpaJobConflictError) as exc_info:
        await start_source_job(db, op=OP_ACCEPTANCE_REFRESH, week_start=MONDAY, user_id=None)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_start_job_ignores_stale_jobs(db) -> None:
    stale = await _job(db, op="events-preview", minutes_ago=31)

    job = await start_source_job(
        db, op=OP_LEAVE_COMPARE, week_start=MONDAY, user_id=None, params={"month": "2026-10"}
    )

    assert job.status == "running"
    assert job.job_type == "fetch"
    assert job.params == {"month": "2026-10", "op": OP_LEAVE_COMPARE}
    await db.refresh(stale)
    assert stale.status == "running"  # 残骸は書き換えない


@pytest.mark.asyncio
async def test_start_job_rejects_unknown_op(db) -> None:
    with pytest.raises(ValueError):
        await start_source_job(db, op="smart-preview", week_start=MONDAY, user_id=None)


@pytest.mark.asyncio
async def test_finish_job_completed_with_partial_failures(db) -> None:
    await _staff(db, "山田花子")
    job = await start_source_job(db, op=OP_ACCEPTANCE_REFRESH, week_start=MONDAY, user_id=None)
    client = StubClient(
        export_results=[export_ok(CSV_TEXT, "plan"), {"result": {"success": False}}]
    )
    results = [
        await fetch_month_csv(db, client, KIND_PLAN_CSV, "2026-10", job_id=job.id),
        await fetch_month_csv(db, client, KIND_ACTUAL_CSV, "2026-10", job_id=job.id),
    ]

    done = await finish_source_job(db, job.id, results)

    assert done is not None
    assert done.status == "completed"
    summary = done.result_summary or {}
    assert summary["done"] == 2
    assert summary["failed"] == [
        {"kind": "actual_csv", "period_start": "2026-10-01", "reason": results[1].error}
    ]
    assert summary["unmatched_names"] == {"鈴木 一郎": 1, "知らない人": 1}


@pytest.mark.asyncio
async def test_finish_job_failed_when_broken(db) -> None:
    job = await start_source_job(db, op=OP_ACCEPTANCE_REFRESH, week_start=MONDAY, user_id=None)
    done = await finish_source_job(db, job.id, [], error="落ちました")
    assert done is not None and done.status == "failed"
    assert (done.result_summary or {})["error"] == "落ちました"


def test_evening_warning_jst() -> None:
    # 17:30 JST = 08:30 UTC
    assert evening_warning(datetime(2026, 10, 20, 8, 30, tzinfo=UTC))
    assert evening_warning(datetime(2026, 10, 20, 9, 59, tzinfo=UTC))  # 18:59 JST
    assert evening_warning(datetime(2026, 10, 20, 10, 0, tzinfo=UTC)) is None  # 19:00 JST
    assert evening_warning(datetime(2026, 10, 20, 7, 59, tzinfo=UTC)) is None  # 16:59 JST


@pytest.mark.asyncio
async def test_job_type_per_op(db) -> None:
    fetch = await start_source_job(db, op=OP_ACCEPTANCE_REFRESH, week_start=MONDAY, user_id=None)
    await finish_source_job(db, fetch.id, [])
    push = await start_source_job(db, op=OP_LEAVE_OUTBOUND, week_start=MONDAY, user_id=None)
    assert fetch.job_type == "fetch"
    assert push.job_type == "push"  # 今のイベント送信 (events-outbound) と同じ
    assert push.params == {"op": OP_LEAVE_OUTBOUND}


@pytest.mark.asyncio
async def test_progress_written_back_after_each_fetch(db) -> None:
    job = await start_source_job(db, op=OP_LEAVE_COMPARE, week_start=MONDAY, user_id=None, total=2)
    old = datetime.now(UTC) - timedelta(minutes=25)
    job.updated_at = old
    await db.commit()
    client = StubClient(
        export_results=[{"result": {"success": False, "error": "x"}}],
        tasks_results=[tasks_ok([], ["2026-10-19"])],
    )

    await fetch_month_csv(db, client, KIND_ACTUAL_CSV, "2026-10", job_id=job.id)
    await db.refresh(job)
    summary = job.result_summary or {}
    assert summary["done"] == 1
    assert summary["total"] == 2
    assert summary["current"] == {"kind": "actual_csv", "period_start": "2026-10-01"}
    assert [f["kind"] for f in summary["failed"]] == ["actual_csv"]
    touched = job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=UTC)
    assert touched > old

    await fetch_week_tasks(db, client, MONDAY, job_id=job.id)
    await db.refresh(job)
    assert (job.result_summary or {})["done"] == 2
    assert job.status == "running"


@pytest.mark.asyncio
async def test_failure_with_previous_row_only_in_job_summary(db) -> None:
    first = StubClient(export_results=[export_ok("", "actual", row_count=0)])
    await fetch_month_csv(db, first, KIND_ACTUAL_CSV, "2026-10")
    job = await start_source_job(db, op=OP_LEAVE_COMPARE, week_start=MONDAY, user_id=None)
    failing = StubClient(export_results=[{"result": {"success": False, "error": "壊れた"}}])

    result = await fetch_month_csv(db, failing, KIND_ACTUAL_CSV, "2026-10", job_id=job.id)

    assert result.status == "failed" and result.snapshot_id is None
    snap = await _snapshot(db, KIND_ACTUAL_CSV, date(2026, 10, 1))
    assert snap is not None and snap.status == "empty" and snap.error is None
    assert snap.job_id is None  # 前の行は書き換えない
    await db.refresh(job)
    assert "壊れた" in (job.result_summary or {})["failed"][0]["reason"]


@pytest.mark.asyncio
async def test_recently_updated_long_job_still_blocks(db) -> None:
    # 作られて 60 分たっても 1 本ごとに updated_at を書き戻している取り直しは残骸ではない。
    old = datetime.now(UTC) - timedelta(minutes=60)
    db.add(
        KaipokeJob(
            job_type="fetch",
            status="running",
            week_start=MONDAY,
            params={"op": OP_ACCEPTANCE_REFRESH},
            created_at=old,
            updated_at=datetime.now(UTC),
        )
    )
    await db.commit()
    with pytest.raises(RpaJobConflictError):
        await start_source_job(db, op=OP_LEAVE_COMPARE, week_start=MONDAY, user_id=None)


def test_covered_dates_of() -> None:
    assert covered_dates_of({}) is None
    assert covered_dates_of({"week_dates": "2026-10-19"}) is None
    assert covered_dates_of({"week_dates": ["x"]}) is None
    assert covered_dates_of({"week_dates": ["2026-10-20", "bad", "2026-10-19", "2026-10-20"]}) == [
        "2026-10-19",
        "2026-10-20",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("op", "minutes_ago", "closed"),
    [
        (OP_ACCEPTANCE_REFRESH, 60, False),
        (OP_LEAVE_COMPARE, 119, False),
        (OP_LEAVE_OUTBOUND, 60, False),
        (OP_LEAVE_COMPARE, 121, True),
        ("events-outbound", 31, True),  # 他の op は今のまま 30 分
    ],
)
async def test_reconcile_latest_job_holds_source_ops_120_minutes(
    db, op: str, minutes_ago: int, closed: bool
) -> None:
    from app.api.v1.integrations import _reconcile_latest_job

    await _job(db, op=op, minutes_ago=minutes_ago)
    job = await _reconcile_latest_job(db, kaipoke_idle=True, result_payload=None)
    assert job is not None
    assert (job.status != "running") is closed
