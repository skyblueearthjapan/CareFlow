"""休みをカイポケへ送る (段階 L #10・§7-4-4).

送る予定 (照合前の月は送らない・照合前の休み・送信済み・要対応)・件名と時刻・ジョブ
(409・一部失敗・取り消し・中断)・送れた分の記録と ``staff_events`` の二重化の防止。
RPA はモック (``individual_tasks_apply`` / ``individual_tasks_apply_result`` / ``status``)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select, update

from app.core.security import hash_password
from app.models import User
from app.models.kaipoke_job import KaipokeJob
from app.models.leave import LeaveMonth, StaffLeaveKaipoke
from app.models.staff import Staff, StaffEvent, StaffShift, StaffWeeklyOverride
from app.services.kaipoke import leave_outbound as lo
from app.services.kaipoke import sources
from app.services.kaipoke.events_outbound import build_outbound_plan
from app.services.kaipoke.sources import OP_LEAVE_COMPARE, OP_LEAVE_OUTBOUND, RpaJobConflictError
from app.services.leave_calendar import iso_key

MONTH = date(2026, 11, 1)
WED = date(2026, 11, 4)
THU = date(2026, 11, 5)
NEXT_WED = date(2026, 11, 11)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)
SHIFT = (time(9, 0), time(17, 30))
INTERNAL = "4321"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch) -> None:
    monkeypatch.setattr(lo, "APPLY_POLL_SECONDS", 0.0)
    monkeypatch.setattr(lo, "APPLY_POLL_MAX", 5)
    monkeypatch.setattr(sources, "RETRY_INTERVAL_SECONDS", 0.0)


async def _admin(db) -> User:
    user = User(
        email=f"adm-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _staff(
    db, name: str = "送信　花子", *, internal: str | None = INTERNAL, saturday_off: bool = True
) -> Staff:
    staff = Staff(name=name, role="staff")
    db.add(staff)
    await db.flush()
    for wd in range(6):
        on = not (saturday_off and wd == 5)
        db.add(
            StaffShift(
                staff_id=staff.id, weekday=wd, is_on=on, start_time=SHIFT[0], end_time=SHIFT[1]
            )
        )
    if internal is not None:
        # カイポケの職員 ID は取込済みの行の external_id から引く (events_outbound と同じ)。
        db.add(
            StaffEvent(
                staff_id=staff.id,
                event_type="event",
                starts_at=datetime(2026, 10, 1, 9, 0),
                ends_at=datetime(2026, 10, 1, 9, 15),
                title="朝会",
                source="kaipoke",
                external_id=f"999:{internal}:2026-10-01",
            )
        )
    await db.commit()
    return staff


def _override(staff_id: uuid.UUID, day: date, kind: str) -> StaffWeeklyOverride:
    y, w, wd = iso_key(day)
    return StaffWeeklyOverride(
        staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=kind
    )


async def _compared(db, month: date = MONTH) -> None:
    db.add(LeaveMonth(month=month, last_compared_at=datetime.now(UTC)))
    await db.commit()


async def _record(db, staff_id: uuid.UUID, day: date) -> StaffLeaveKaipoke | None:
    return await db.scalar(
        select(StaffLeaveKaipoke)
        .where(StaffLeaveKaipoke.staff_id == staff_id, StaffLeaveKaipoke.leave_date == day)
        .execution_options(populate_existing=True)
    )


# --- 送る予定 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_month_not_compared_is_not_sent(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    plan = await lo.build_leave_send_plan(db, "2026-11")
    assert plan.blocked_reason == lo.MSG_NOT_COMPARED
    assert len(plan.items) == 1 and plan.sendable_items == []
    started = await lo.start_leave_outbound(db, month="2026-11", user_id=None)
    assert started.job is None and started.reason == lo.MSG_NOT_COMPARED
    assert (await db.scalars(select(KaipokeJob))).all() == []
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []


@pytest.mark.asyncio
async def test_plan_states_baseline_sent_needs_action_and_unsent(db) -> None:
    staff = await _staff(db)
    d_unsent, d_baseline, d_sent, d_changed, d_cancelled = (
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 6),
    )
    db.add_all(
        [
            _override(staff.id, d_unsent, "off"),
            _override(staff.id, d_baseline, "off"),
            _override(staff.id, d_sent, "pm_off"),
            _override(staff.id, d_changed, "off"),
            _override(staff.id, date(2026, 11, 9), "custom_time"),  # 休みではない
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d_baseline, baseline_pending=True),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d_sent, sent_type="pm_off"),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d_changed, sent_type="pm_off"),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d_cancelled, sent_type="off"),
        ]
    )
    await db.commit()
    await _compared(db)
    plan = await lo.build_leave_send_plan(db, MONTH)
    assert plan.blocked_reason is None
    assert [(i.day, i.leave_type) for i in plan.items] == [(d_unsent, "off")]
    assert plan.baseline_count == 1
    assert plan.sent_count == 1
    assert [(n.day, n.rakusuke, n.sent, n.message) for n in plan.needs_action] == [
        (d_changed, "off", "pm_off", "カイポケの午後休を消してください（公休は送り直します）"),
        (d_cancelled, None, "off", "カイポケの公休を手で消してください"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "day", "title", "start", "end"),
    [
        ("off", WED, "休み", "09:00", "17:30"),
        ("am_off", WED, "AM休み", "09:00", "13:00"),
        ("pm_off", WED, "PM休み", "12:30", "17:30"),
        ("paid_off", WED, "有給", "09:00", "17:30"),
        # 型で勤務しない曜日 (土曜) は営業時間 (既定 9:30〜18:00)。
        ("off", SAT, "休み", "09:30", "18:00"),
        ("paid_off", SAT, "有給", "09:30", "18:00"),
        ("am_off", SAT, "AM休み", "09:30", "13:00"),
        ("pm_off", SAT, "PM休み", "12:30", "18:00"),
    ],
)
async def test_subject_and_times(db, kind, day, title, start, end) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, day, kind))
    await db.commit()
    await _compared(db)
    [item] = (await lo.build_leave_send_plan(db, MONTH)).items
    assert (item.title, item.start, item.end, item.sendable) == (title, start, end, True)


@pytest.mark.asyncio
async def test_sunday_and_unknown_staff_cannot_be_sent(db) -> None:
    staff = await _staff(db)
    other = await _staff(db, "未取込　太郎", internal=None)
    db.add_all([_override(staff.id, SUN, "off"), _override(other.id, WED, "off")])
    await db.commit()
    await _compared(db)
    plan = await lo.build_leave_send_plan(db, MONTH)
    reasons = {(i.staff_name, i.day): (i.sendable, i.reason) for i in plan.items}
    assert reasons == {
        ("送信　花子", SUN): (False, lo.MSG_SUNDAY),
        ("未取込　太郎", WED): (False, lo.MSG_NO_INTERNAL_ID),
    }
    started = await lo.start_leave_outbound(db, month=MONTH, user_id=None)
    assert started.job is None and started.reason == lo.MSG_NOTHING_TO_SEND


# --- ジョブ ------------------------------------------------------------------------


class _Rpa:
    """RPA のモック。週ごとの結果を ``outcomes`` (external_ref → (outcome, key)) で返す。"""

    def __init__(self, *, fail_weeks: tuple[str, ...] = ()) -> None:
        self.applied: list[dict[str, Any]] = []
        self.outcomes: dict[str, tuple[str, str | None]] = {}
        self.fail_weeks = fail_weeks
        self._last: dict[str, Any] | None = None
        self.on_poll: Any = None

    async def status(self) -> dict[str, Any]:
        return {"current_task": {"running": False}}

    async def individual_tasks_apply(self, payload, *, timeout=None) -> dict[str, Any]:
        self.applied.append(payload)
        self._last = payload
        return {"success": True, "job_id": payload["job_id"]}

    async def individual_tasks_apply_result(self) -> dict[str, Any]:
        assert self._last is not None
        if self.on_poll is not None:
            await self.on_poll(self._last)
        week = self._last["job_id"].split(":", 1)[1]
        if week in self.fail_weeks:
            return {"status": "error", "job_id": self._last["job_id"], "error": "画面が開けません"}
        results = []
        for item in self._last["items"]:
            outcome, key = self.outcomes.get(
                item["external_ref"], ("added", f"{len(results) + 100}:{INTERNAL}:{item['date']}")
            )
            results.append(
                {
                    "external_ref": item["external_ref"],
                    "outcome": outcome,
                    "external_key": key,
                    "error": "保存できませんでした" if outcome == "failed" else None,
                }
            )
        return {
            "status": "completed",
            "job_id": self._last["job_id"],
            "result": {"results": results, "total": len(results)},
        }


async def _start(db, user: User | None = None) -> lo.LeaveOutboundStart:
    started = await lo.start_leave_outbound(
        db, month=MONTH, user_id=user.id if user is not None else None
    )
    assert started.job is not None, started.reason
    return started


@pytest.mark.asyncio
async def test_start_creates_push_job_and_records_then_409(db) -> None:
    staff = await _staff(db)
    db.add_all([_override(staff.id, WED, "off"), _override(staff.id, NEXT_WED, "am_off")])
    await db.commit()
    await _compared(db)
    started = await _start(db)
    job = started.job
    assert job is not None
    assert (job.job_type, job.status, job.params["op"]) == ("push", "running", OP_LEAVE_OUTBOUND)
    assert job.week_start == date(2026, 11, 2)
    assert started.count == 2 and started.weeks == 2
    assert job.result_summary["total"] == 2
    assert [t["week_start"] for t in job.params["targets"]] == ["2026-11-02", "2026-11-09"]
    rows = (await db.scalars(select(StaffLeaveKaipoke))).all()
    assert sorted((r.leave_date, r.sent_type, r.baseline_pending) for r in rows) == [
        (WED, None, False),
        (NEXT_WED, None, False),
    ]
    assert {i["record_id"] for i in job.params["items"]} == {str(r.id) for r in rows}
    assert "credentials" not in str(job.params)

    with pytest.raises(RpaJobConflictError):
        await lo.start_leave_outbound(db, month=MONTH, user_id=None)


@pytest.mark.asyncio
async def test_other_rpa_job_running_is_409(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    await _compared(db)
    await sources.start_source_job(
        db, op=OP_LEAVE_COMPARE, week_start=date(2026, 10, 26), user_id=None, params={}
    )
    with pytest.raises(RpaJobConflictError):
        await lo.start_leave_outbound(db, month=MONTH, user_id=None)
    # 409 のときは行を作らない (rollback)。
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []


@pytest.mark.asyncio
async def test_run_sends_week_by_week_and_records_sent(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    db.add_all(
        [
            _override(staff.id, WED, "off"),
            _override(staff.id, THU, "pm_off"),
            _override(staff.id, NEXT_WED, "paid_off"),
        ]
    )
    await db.commit()
    await _compared(db)
    started = await _start(db, admin)
    assert started.job is not None
    job_id, staff_id = started.job.id, staff.id
    thu_record = await _record(db, staff_id, THU)
    assert thu_record is not None
    rpa = _Rpa()
    # 木曜は失敗・翌週は「同じ予定が既にある」(取込済みの行がある)。
    rpa.outcomes[f"leave:{thu_record.id}"] = ("failed", None)
    next_record = await _record(db, staff_id, NEXT_WED)
    assert next_record is not None
    dup_key = f"555:{INTERNAL}:{NEXT_WED.isoformat()}"
    rpa.outcomes[f"leave:{next_record.id}"] = ("skipped_duplicate", dup_key)
    db.add(
        StaffEvent(
            staff_id=staff_id,
            event_type="event",
            starts_at=datetime.combine(NEXT_WED, time(9)),
            ends_at=datetime.combine(NEXT_WED, time(17, 30)),
            title="有給",
            source="kaipoke",
            external_id=dup_key,
        )
    )
    await db.commit()

    job = await lo.run_leave_outbound(db, rpa, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed"

    # 週ごとに順番に 1 回ずつ (朝会などの予定は含まない)。
    assert [p["job_id"].split(":", 1)[1] for p in rpa.applied] == ["2026-11-02", "2026-11-09"]
    first_week = rpa.applied[0]["items"]
    assert [(i["date"], i["title"], i["start"], i["end"]) for i in first_week] == [
        (WED.isoformat(), "休み", "09:00", "17:30"),
        (THU.isoformat(), "PM休み", "12:30", "17:30"),
    ]
    assert all(i["staff_internal_id"] == INTERNAL for i in first_week)
    assert all(i["external_ref"].startswith("leave:") for i in first_week)

    wed = await _record(db, staff_id, WED)
    assert wed is not None and wed.sent_type == "off" and wed.sent_by == admin.id
    assert wed.sent_at is not None and wed.kaipoke_external_id == f"100:{INTERNAL}:{WED}"
    thu = await _record(db, staff_id, THU)
    assert thu is not None and thu.sent_type is None and thu.sent_at is None  # 未送信のまま
    nxt = await _record(db, staff_id, NEXT_WED)
    assert nxt is not None and nxt.sent_type == "paid_off" and nxt.kaipoke_external_id == dup_key

    events = (
        await db.scalars(
            select(StaffEvent).where(StaffEvent.staff_id == staff_id, StaffEvent.title != "朝会")
        )
    ).all()
    assert sorted((e.external_id, e.source, e.title) for e in events) == sorted(
        [
            (f"100:{INTERNAL}:{WED}", "kaipoke", "休み"),
            (dup_key, "kaipoke", "有給"),  # 同じキーの行があれば作らない
        ]
    )
    wed_event = next(e for e in events if e.external_id == f"100:{INTERNAL}:{WED}")
    assert (wed_event.starts_at.time(), wed_event.ends_at.time()) == (time(9), time(17, 30))

    summary = job.result_summary
    assert summary["done"] == 2
    assert summary["failed"] == [
        {"kind": lo.KIND_LEAVE_WEEK, "period_start": "2026-11-02", "reason": "1件送れませんでした"}
    ]
    outcomes = {(i["date"], i["outcome"]) for i in summary["items"]}
    assert outcomes == {
        (WED.isoformat(), lo.OUTCOME_SENT),
        (THU.isoformat(), lo.OUTCOME_FAILED),
        (NEXT_WED.isoformat(), lo.OUTCOME_SENT),
    }
    thu_item = next(i for i in summary["items"] if i["date"] == THU.isoformat())
    assert thu_item["reason"] == "保存できませんでした"

    # 送ったあとは送信済み・週の個別業務の送信 (manual/fixed) にも入らない (二重にならない)。
    plan = await lo.build_leave_send_plan(db, MONTH)
    assert [(i.day, i.leave_type) for i in plan.items] == [(THU, "pm_off")]
    assert plan.sent_count == 2
    week_plan = await build_outbound_plan(db, date(2026, 11, 2))
    assert week_plan.items == []


@pytest.mark.asyncio
async def test_rpa_error_for_a_week_keeps_items_unsent(db) -> None:
    staff = await _staff(db)
    db.add_all([_override(staff.id, WED, "off"), _override(staff.id, NEXT_WED, "off")])
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job_id, staff_id = started.job.id, staff.id
    rpa = _Rpa(fail_weeks=("2026-11-02",))
    job = await lo.run_leave_outbound(db, rpa, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed"
    assert [f["reason"] for f in job.result_summary["failed"]] == ["画面が開けません"]
    assert (await _record(db, staff_id, WED)).sent_type is None  # type: ignore[union-attr]
    assert (await _record(db, staff_id, NEXT_WED)).sent_type == "off"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_leave_changed_before_send_is_skipped(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job_id, staff_id = started.job.id, staff.id
    # 送る前に午後休へ変えた。
    y, w, wd = iso_key(WED)
    await db.execute(
        update(StaffWeeklyOverride)
        .where(StaffWeeklyOverride.staff_id == staff_id, StaffWeeklyOverride.iso_week == w)
        .values(override_type="pm_off")
    )
    await db.commit()
    rpa = _Rpa()
    job = await lo.run_leave_outbound(db, rpa, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed"
    assert rpa.applied == []  # RPA を呼ばない
    [item] = job.result_summary["items"]
    assert (item["outcome"], item["reason"]) == (lo.OUTCOME_SKIPPED, lo.MSG_CHANGED)
    assert (await _record(db, staff_id, WED)).sent_type is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancelled_job_records_sent_week_and_stops(db) -> None:
    staff = await _staff(db)
    db.add_all([_override(staff.id, WED, "off"), _override(staff.id, NEXT_WED, "off")])
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job_id, staff_id = started.job.id, staff.id
    rpa = _Rpa()

    async def cancel(_payload) -> None:
        # 1 週目の結果を待っている間に取り消された。
        await db.execute(
            update(KaipokeJob).where(KaipokeJob.id == job_id).values(status="cancelled")
        )
        await db.commit()

    rpa.on_poll = cancel
    job = await lo.run_leave_outbound(db, rpa, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "cancelled"
    assert len(rpa.applied) == 1  # 2 週目は送らない
    # RPA は作ったので、1 週目の記録は残す。
    assert (await _record(db, staff_id, WED)).sent_type == "off"  # type: ignore[union-attr]
    assert (await _record(db, staff_id, NEXT_WED)).sent_type is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_interrupted_job_is_closed_as_failed(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job_id = started.job.id

    class _Stopped(_Rpa):
        async def individual_tasks_apply(self, payload, *, timeout=None) -> dict[str, Any]:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await lo.run_leave_outbound(db, _Stopped(), job_id)  # type: ignore[arg-type]
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    assert job is not None and job.status == "failed"
    assert job.result_summary["error"] == "中断されました"


@pytest.mark.asyncio
async def test_run_leave_outbound_job_uses_own_session(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job = await lo.run_leave_outbound_job(job_id=started.job.id, client=_Rpa())  # type: ignore[arg-type]
    assert job is not None and job.status == "completed"
    assert (await _record(db, staff.id, WED)).sent_type == "off"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_lost_result_marks_week_failed(db) -> None:
    staff = await _staff(db)
    db.add(_override(staff.id, WED, "off"))
    await db.commit()
    await _compared(db)
    started = await _start(db)
    assert started.job is not None
    job_id, staff_id = started.job.id, staff.id

    class _Lost(_Rpa):
        async def individual_tasks_apply_result(self) -> dict[str, Any]:
            return {"status": "completed", "job_id": "other", "result": {"results": []}}

    job = await lo.run_leave_outbound(db, _Lost(), job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed"
    [item] = job.result_summary["items"]
    assert item["outcome"] == lo.OUTCOME_FAILED and "失われました" in item["reason"]
    assert (await _record(db, staff_id, WED)).sent_type is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_baseline_month_close_sends_only_after_compare_changes(db) -> None:
    """既存の休み (照合前) は送らない。照合の後に入れた休みだけ送る。"""
    staff = await _staff(db)
    db.add_all(
        [
            _override(staff.id, WED, "off"),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=WED, baseline_pending=True),
        ]
    )
    await db.commit()
    await _compared(db)
    started = await lo.start_leave_outbound(db, month=MONTH, user_id=None)
    assert started.job is None and started.reason == lo.MSG_NOTHING_TO_SEND
    db.add(_override(staff.id, THU, "am_off"))
    await db.commit()
    started = await _start(db)
    assert [i["date"] for i in started.job.params["items"]] == [THU.isoformat()]  # type: ignore[union-attr]
    assert started.job.params["items"][0]["start"] == "09:00"  # type: ignore[union-attr]
