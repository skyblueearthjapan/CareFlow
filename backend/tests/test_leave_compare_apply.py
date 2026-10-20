"""休みの照合の「合わせる」(段階 L #9) — §7-4-3 の全通りの表の行ごと.

R = らく助の休み・S = sent_type・K = カイポケの休み。照合は本物の ``compare_month`` で作り
(材料は ``kaipoke_source_snapshots`` に直接置く)、その差分を ``apply_compare_actions`` で
合わせる。override の変更と ``sent_type`` の書き込みが同じトランザクションであることも見る。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.models import Notification, Patient, User, Visit
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.leave import (
    KaipokeLeaveJudgement,
    LeaveCompareRun,
    LeaveMonth,
    StaffLeaveKaipoke,
)
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride
from app.services import leave_compare as lc
from app.services.kaipoke import sources
from app.services.kaipoke.sources import KIND_ACTUAL_CSV, KIND_TASKS, OP_LEAVE_COMPARE
from app.services.leave_calendar import iso_key, leave_type_on
from app.services.staff_leave import StaffLeaveError, today_jst

NAME = "照合　花子"
SHIFT = (time(9, 0), time(18, 0))

#: K の種類 → カイポケの件名と時刻 (勤務 9:00〜18:00 の人)。
KAIPOKE_TASK = {
    "off": ("休み", "09:00", "18:00"),
    "am_off": ("AM休み", "09:00", "13:00"),
    "pm_off": ("PM休み", "12:30", "18:00"),
    "paid_off": ("有給", "09:00", "18:00"),
}
X, Y, Z = "off", "pm_off", "paid_off"


def _month_ahead(months: int) -> date:
    first = today_jst().replace(day=1)
    for _ in range(abs(months)):
        if months > 0:
            first = (first + timedelta(days=32)).replace(day=1)
        else:
            first = (first - timedelta(days=1)).replace(day=1)
    return first


def _weekday_in(month: date, nth: int = 0) -> date:
    """その月の n 番目の月〜金 (照合の対象の日)。"""
    days = [d for d in lc.month_days(month) if d.weekday() < 5]
    return days[nth]


FUTURE = _month_ahead(2)
PAST = _month_ahead(-2)


def _task(day: date, kind: str | None = None, *, title: str = "", start="", end="") -> dict:
    if kind is not None:
        title, start, end = KAIPOKE_TASK[kind]
    return {"staff_name": NAME, "date": day.isoformat(), "start": start, "end": end, "title": title}


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


async def _staff(db) -> Staff:
    staff = Staff(name=NAME, role="staff")
    db.add(staff)
    await db.flush()
    for wd in range(6):
        db.add(
            StaffShift(
                staff_id=staff.id, weekday=wd, is_on=True, start_time=SHIFT[0], end_time=SHIFT[1]
            )
        )
    await db.commit()
    return staff


def _override(staff_id: uuid.UUID, day: date, kind: str) -> StaffWeeklyOverride:
    y, w, wd = iso_key(day)
    return StaffWeeklyOverride(
        staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=kind
    )


async def _compare(db, month: date, task_rows: list[dict[str, Any]]) -> LeaveCompareRun:
    """材料を置いて照合する (本物の compare_month)。"""
    await db.execute(delete(KaipokeSourceSnapshot))
    await db.commit()
    mondays = lc.month_mondays(month)
    job = await sources.start_source_job(
        db,
        op=OP_LEAVE_COMPARE,
        week_start=mondays[0],
        user_id=None,
        params={"month": f"{month.year:04d}-{month.month:02d}"},
    )
    now = datetime.now(UTC) + timedelta(seconds=1)
    for monday in mondays:
        week = {(monday + timedelta(days=i)).isoformat() for i in range(6)}
        rows = [r for r in task_rows if r["date"] in week]
        db.add(
            KaipokeSourceSnapshot(
                kind=KIND_TASKS,
                period_start=monday,
                rows=rows,
                covered_dates=sorted(week),
                row_count=len(rows),
                status="ok" if rows else "empty",
                fetched_at=now,
                job_id=job.id,
            )
        )
    db.add(
        KaipokeSourceSnapshot(
            kind=KIND_ACTUAL_CSV,
            period_start=month,
            rows=[],
            row_count=0,
            status="empty",
            fetched_at=now,
            job_id=job.id,
        )
    )
    await db.commit()
    await sources.finish_source_job(db, job.id, [])
    run = await lc.compare_month(db, job.id)
    assert run is not None
    return run


def _diff_of(run: LeaveCompareRun, staff_id: uuid.UUID, day: date) -> dict[str, Any]:
    [diff] = [
        d for d in run.diffs if d["staff_id"] == str(staff_id) and d["date"] == day.isoformat()
    ]
    return diff


async def _sent(db, staff_id: uuid.UUID, day: date) -> StaffLeaveKaipoke | None:
    return await db.scalar(
        select(StaffLeaveKaipoke)
        .where(StaffLeaveKaipoke.staff_id == staff_id, StaffLeaveKaipoke.leave_date == day)
        .execution_options(populate_existing=True)
    )


# --- 全通りの表の「合わせる」の行 ------------------------------------------------------

# (#, R, S, 照合前, K, ボタン, 後の R, 後の S〔None = 行が無い〕)
APPLY_TABLE = [
    (2, None, None, False, X, lc.ACTION_ADD_TO_RAKUSUKE, X, X),
    (5, None, X, False, Y, lc.ACTION_ADD_TO_RAKUSUKE, Y, Y),
    (6, X, None, True, None, lc.ACTION_CANCEL_RAKUSUKE, None, None),
    (8, X, None, False, Y, lc.ACTION_MATCH_KAIPOKE_KIND, Y, Y),
    (9, X, X, False, None, lc.ACTION_CANCEL_RAKUSUKE, None, None),
    (11, X, X, False, Y, lc.ACTION_MATCH_KAIPOKE_KIND, Y, Y),
    (15, X, Y, False, Z, lc.ACTION_MATCH_KAIPOKE_KIND, Z, Z),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "r", "s", "baseline", "k", "action", "r_after", "s_after"), APPLY_TABLE
)
async def test_apply_each_row_writes_override_and_sent_type(
    db, case, r, s, baseline, k, action, r_after, s_after
) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    day = _weekday_in(FUTURE, 2)
    if r is not None:
        db.add(_override(staff.id, day, r))
    if s is not None or baseline:
        db.add(
            StaffLeaveKaipoke(
                staff_id=staff.id, leave_date=day, sent_type=s, baseline_pending=baseline
            )
        )
    await db.commit()
    run = await _compare(db, FUTURE, [_task(day, k)] if k else [])
    diff = _diff_of(run, staff.id, day)
    assert (diff["case"], diff["action"]) == (case, action)

    result = await lc.apply_compare_actions(
        db, items=[lc.CompareAction(staff.id, day, action)], actor=admin
    )
    assert result.recompare_needed is True
    [applied] = result.applied
    assert applied["case"] == case
    assert (applied["rakusuke_before"], applied["sent_before"]) == (r, s)
    assert (applied["rakusuke_after"], applied["sent_after"]) == (r_after, s_after)

    assert await leave_type_on(db, staff.id, day) == r_after
    row = await _sent(db, staff.id, day)
    if s_after is None:
        assert row is None  # S = NULL (行を消す)
    else:
        assert row is not None and row.sent_type == s_after and row.baseline_pending is False
    if r_after is not None:
        y, w, wd = iso_key(day)
        ov = await db.scalar(
            select(StaffWeeklyOverride)
            .where(
                StaffWeeklyOverride.staff_id == staff.id,
                StaffWeeklyOverride.iso_year == y,
                StaffWeeklyOverride.iso_week == w,
                StaffWeeklyOverride.weekday == wd,
            )
            .execution_options(populate_existing=True)
        )
        assert ov is not None and ov.source == "kaipoke"

    # 照合の結果に「誰が・いつ・どう合わせたか」が残る。
    saved = await db.get(LeaveCompareRun, run.id, populate_existing=True)
    assert saved is not None
    resolved = _diff_of(saved, staff.id, day)["resolved"]
    assert resolved["action"] == action and resolved["by"] == str(admin.id)

    # 同じ差分をもう一度押すと 409 (合わせ済み)。
    with pytest.raises(StaffLeaveError) as exc:
        await lc.apply_compare_actions(
            db, items=[lc.CompareAction(staff.id, day, action)], actor=admin
        )
    assert exc.value.status_code == 409

    # 比べ直すと差分は消える (カイポケと一致)。
    rerun = await _compare(db, FUTURE, [_task(day, k)] if k else [])
    assert [d for d in rerun.diffs if d["staff_id"] == str(staff.id)] == []


@pytest.mark.asyncio
async def test_choose_kind_records_judgement_and_recompare_uses_it(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    day = _weekday_in(FUTURE, 3)
    task = _task(day, title="休み", start="10:00", end="11:00")
    run = await _compare(db, FUTURE, [task])
    diff = _diff_of(run, staff.id, day)
    assert diff["kind"] == lc.DIFF_UNKNOWN_KIND
    content_hash = diff["detail"]["content_hash"]
    assert content_hash == lc.leave_content_hash(staff.id, day, "休み", time(10), time(11))

    result = await lc.apply_compare_actions(
        db,
        items=[
            lc.CompareAction(
                staff.id, day, lc.ACTION_CHOOSE_KIND, content_hash=content_hash, chosen_kind=Z
            )
        ],
        actor=admin,
    )
    assert result.recompare_needed is True
    judgement = await db.scalar(
        select(KaipokeLeaveJudgement).where(KaipokeLeaveJudgement.content_hash == content_hash)
    )
    assert judgement is not None
    assert (judgement.chosen_kind, judgement.subject, judgement.starts, judgement.ends) == (
        Z,
        "休み",
        time(10),
        time(11),
    )
    assert judgement.decided_by == admin.id
    # R・S は動かさない。
    assert await leave_type_on(db, staff.id, day) is None
    assert await _sent(db, staff.id, day) is None

    rerun = await _compare(db, FUTURE, [task])
    again = _diff_of(rerun, staff.id, day)
    assert (again["case"], again["kaipoke"]) == (2, Z)

    assert again["kind"] == lc.DIFF_KAIPOKE_ONLY
    # 最新の照合にはもう「種類を選ぶ」差分が無いので 409 (古い照合の差分は押せない)。
    with pytest.raises(StaffLeaveError) as exc:
        await lc.apply_compare_actions(
            db,
            items=[
                lc.CompareAction(
                    staff.id,
                    day,
                    lc.ACTION_CHOOSE_KIND,
                    content_hash=content_hash,
                    chosen_kind=lc.JUDGED_NOT_LEAVE,
                )
            ],
            actor=admin,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_choose_kind_again_rewrites_same_judgement(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    day = _weekday_in(FUTURE, 3)
    task = _task(day, title="休み", start="10:00", end="11:00")
    content_hash = lc.leave_content_hash(staff.id, day, "休み", time(10), time(11))
    for chosen in (Z, lc.JUDGED_NOT_LEAVE):
        # 選んだ記録を消して比べ直す → 種類がわからない差分がまた出る。
        await db.execute(delete(KaipokeLeaveJudgement))
        await db.commit()
        run = await _compare(db, FUTURE, [task])
        assert _diff_of(run, staff.id, day)["kind"] == lc.DIFF_UNKNOWN_KIND
        await lc.apply_compare_actions(
            db,
            items=[lc.CompareAction(staff.id, day, lc.ACTION_CHOOSE_KIND, content_hash, chosen)],
            actor=admin,
        )
    rows = (await db.scalars(select(KaipokeLeaveJudgement))).all()
    assert [r.chosen_kind for r in rows] == [lc.JUDGED_NOT_LEAVE]
    rerun = await _compare(db, FUTURE, [task])
    assert [d for d in rerun.diffs if d["staff_id"] == str(staff.id)] == []


@pytest.mark.asyncio
async def test_failure_midway_leaves_neither_override_nor_sent_type(db, monkeypatch) -> None:
    """override の変更と sent_type の書き込みは同じトランザクション (途中で失敗したら残らない)。"""
    admin = await _admin(db)
    staff = await _staff(db)
    d1, d2 = _weekday_in(FUTURE, 1), _weekday_in(FUTURE, 2)
    run = await _compare(db, FUTURE, [_task(d1, X), _task(d2, Y)])
    run_id, staff_id = run.id, staff.id  # rollback の後は行が期限切れになる

    async def broken(*args, **kwargs):
        raise RuntimeError("通知で失敗")

    monkeypatch.setattr(lc, "notify_leave_results", broken)
    with pytest.raises(RuntimeError):
        await lc.apply_compare_actions(
            db,
            items=[
                lc.CompareAction(staff.id, d1, lc.ACTION_ADD_TO_RAKUSUKE),
                lc.CompareAction(staff.id, d2, lc.ACTION_ADD_TO_RAKUSUKE),
            ],
            actor=admin,
        )
    assert await leave_type_on(db, staff_id, d1) is None
    assert await leave_type_on(db, staff_id, d2) is None
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []
    saved = await db.get(LeaveCompareRun, run_id, populate_existing=True)
    assert saved is not None and all("resolved" not in d for d in saved.diffs)
    month = await db.get(LeaveMonth, FUTURE, populate_existing=True)
    assert month is not None and month.last_compare_run_id == run_id


@pytest.mark.asyncio
async def test_second_item_conflict_rolls_back_first(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    d1, d2 = _weekday_in(FUTURE, 1), _weekday_in(FUTURE, 2)
    await _compare(db, FUTURE, [_task(d1, X), _task(d2, Y)])
    staff_id = staff.id  # rollback の後は行が期限切れになる
    # 照合の後に d2 をらく助で変えた → 409・d1 も残らない。
    db.add(_override(staff.id, d2, "am_off"))
    await db.commit()
    with pytest.raises(StaffLeaveError) as exc:
        await lc.apply_compare_actions(
            db,
            items=[
                lc.CompareAction(staff.id, d1, lc.ACTION_ADD_TO_RAKUSUKE),
                lc.CompareAction(staff.id, d2, lc.ACTION_ADD_TO_RAKUSUKE),
            ],
            actor=admin,
        )
    assert exc.value.status_code == 409
    assert "比べ直して" in str(exc.value.detail)
    assert await leave_type_on(db, staff_id, d1) is None
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []


async def _patient(db) -> Patient:
    p = Patient(
        code=f"P-{uuid.uuid4().hex[:8]}", name="患者", status="active", special_week_active=[]
    )
    db.add(p)
    await db.flush()
    return p


async def _visit(db, staff: Staff, day: date) -> Visit:
    v = Visit(
        patient_id=(await _patient(db)).id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add(v)
    await db.commit()
    return v


@pytest.mark.asyncio
async def test_batch_apply_notifies_admins_once_and_not_the_staff(db) -> None:
    admin = await _admin(db)
    await _admin(db)
    staff = await _staff(db)
    db.add(
        User(
            email=f"st-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("x"),
            role="staff",
            staff_id=staff.id,
        )
    )
    d1, d2, d3 = _weekday_in(FUTURE, 1), _weekday_in(FUTURE, 2), _weekday_in(FUTURE, 3)
    v1 = await _visit(db, staff, d1)
    await _visit(db, staff, d2)
    db.add(StaffLeaveKaipoke(staff_id=staff.id, leave_date=d3, baseline_pending=True))
    db.add(_override(staff.id, d3, X))
    await db.commit()
    await _compare(db, FUTURE, [_task(d1, X), _task(d2, X)])

    result = await lc.apply_compare_actions(
        db,
        items=[
            lc.CompareAction(staff.id, d1, lc.ACTION_ADD_TO_RAKUSUKE),
            lc.CompareAction(staff.id, d2, lc.ACTION_ADD_TO_RAKUSUKE),
            lc.CompareAction(staff.id, d3, lc.ACTION_CANCEL_RAKUSUKE),
        ],
        actor=admin,
    )
    assert result.moved_visit_count == 2
    notices = (await db.scalars(select(Notification))).all()
    unassigned = [n for n in notices if n.type == "leave_unassigned"]
    assert len(unassigned) == 2  # 管理者 2 人へ 1 通ずつ = 1 回
    assert "カイポケに合わせて休みを2件入れ" in (unassigned[0].body or "")
    # 本人へは送らない (承認・取り消しの通知なし)。
    assert [n for n in notices if n.type != "leave_unassigned"] == []
    refreshed = await db.get(Visit, v1.id, populate_existing=True)
    assert refreshed is not None and refreshed.primary_staff_id is None


@pytest.mark.asyncio
async def test_past_day_records_override_only(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    day = _weekday_in(PAST, 2)
    visit = await _visit(db, staff, day)
    await _compare(db, PAST, [_task(day, X)])
    result = await lc.apply_compare_actions(
        db, items=[lc.CompareAction(staff.id, day, lc.ACTION_ADD_TO_RAKUSUKE)], actor=admin
    )
    assert result.moved_visit_count == 0
    assert await leave_type_on(db, staff.id, day) == X
    row = await _sent(db, staff.id, day)
    assert row is not None and row.sent_type == X
    refreshed = await db.get(Visit, visit.id, populate_existing=True)
    assert refreshed is not None and refreshed.primary_staff_id == staff.id  # 実績は触らない
    assert (await db.scalars(select(Notification))).all() == []


@pytest.mark.asyncio
async def test_input_errors(db) -> None:
    admin = await _admin(db)
    staff = await _staff(db)
    day = _weekday_in(FUTURE, 1)
    cases = [
        ([], 422),
        ([lc.CompareAction(staff.id, day, lc.ACTION_REFETCH_WEEK)], 422),
        ([lc.CompareAction(staff.id, day, lc.ACTION_CHOOSE_KIND, "h", "unknown")], 422),
        (
            [
                lc.CompareAction(staff.id, day, lc.ACTION_ADD_TO_RAKUSUKE),
                lc.CompareAction(staff.id, day, lc.ACTION_CANCEL_RAKUSUKE),
            ],
            422,
        ),
        ([lc.CompareAction(staff.id, day, lc.ACTION_ADD_TO_RAKUSUKE)], 409),  # 照合がまだ
    ]
    for items, code in cases:
        with pytest.raises(StaffLeaveError) as exc:
            await lc.apply_compare_actions(db, items=items, actor=admin)
        assert exc.value.status_code == code, items

    # 照合はあるが、その差分が無い → 409。
    await _compare(db, FUTURE, [])
    with pytest.raises(StaffLeaveError) as exc:
        await lc.apply_compare_actions(
            db, items=[lc.CompareAction(staff.id, day, lc.ACTION_ADD_TO_RAKUSUKE)], actor=admin
        )
    assert exc.value.status_code == 409
