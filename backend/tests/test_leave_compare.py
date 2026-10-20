"""休みの「カイポケと比べる」(段階 L #9) — 差分の計算と保存.

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-4-3 の全通りの表と付帯の行。
R = らく助の休み・S = sent_type・K = カイポケの休み (種類の判定と管理者の選択のあと)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.kaipoke_job import KaipokeJob
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
from app.services.kaipoke.name_match import build_name_index
from app.services.kaipoke.sources import (
    KIND_ACTUAL_CSV,
    KIND_TASKS,
    OP_LEAVE_COMPARE,
    RpaJobConflictError,
    SourceSpec,
)

MONTH = date(2026, 10, 1)
JOB_START = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
FRESH = JOB_START + timedelta(minutes=5)
OLD = JOB_START - timedelta(minutes=5)
DAY = date(2026, 10, 14)  # 水曜
DAY_MONDAY = date(2026, 10, 12)  # スポーツの日 (祝日・月曜)
SUNDAY = date(2026, 10, 18)
STAFF = uuid.uuid4()
NAME = "山田　花子"
SHIFT = (time(9, 0), time(18, 0))
MONDAYS = [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)] + [
    date(2026, 10, 26)
]

#: K の種類 → カイポケの件名と時刻 (勤務 9:00〜18:00 の人)。
KAIPOKE_TASK = {
    "off": ("休み", "09:00", "18:00"),
    "am_off": ("AM休み", "09:00", "13:00"),
    "pm_off": ("PM休み", "12:30", "18:00"),
    "paid_off": ("有給", "09:00", "18:00"),
}


def _task(
    day: date, title: str, start: str = "09:00", end: str = "18:00", name: str = NAME
) -> dict[str, Any]:
    return {"staff_name": name, "date": day.isoformat(), "start": start, "end": end, "title": title}


def _leave_task(day: date, kind: str) -> dict[str, Any]:
    title, start, end = KAIPOKE_TASK[kind]
    return _task(day, title, start, end)


def _weeks(rows: list[dict[str, Any]], **overrides: lc.SourceRows) -> dict[date, lc.SourceRows]:
    out: dict[date, lc.SourceRows] = {}
    for monday in MONDAYS:
        week = {(monday + timedelta(days=i)).isoformat() for i in range(7)}
        out[monday] = lc.SourceRows(
            status="ok" if rows else "empty",
            fetched_at=FRESH,
            rows=[r for r in rows if r["date"] in week],
            covered_dates=sorted(week - {(monday + timedelta(days=6)).isoformat()}),
        )
    for key, value in overrides.items():
        out[date.fromisoformat(key)] = value
    return out


def _actual(
    rows: list[dict[str, Any]] | None = None, fetched_at: datetime = FRESH
) -> lc.SourceRows:
    return lc.SourceRows(status="ok" if rows else "empty", fetched_at=fetched_at, rows=rows or [])


def _visit(day: date, start: str, end: str, name: str = NAME) -> dict[str, str]:
    return {
        "業務種別": "医療保険",
        "日付": str(day.day),
        "開始時間": start,
        "終了時間": end,
        "職員名１": name,
        "職員名２": "-",
        "職員名３": "-",
        "利用者": "患者　一郎",
    }


def _build(
    *,
    rakusuke: dict[tuple[uuid.UUID, date], str] | None = None,
    sent: dict[tuple[uuid.UUID, date], lc.SentRecord] | None = None,
    tasks: dict[date, lc.SourceRows] | None = None,
    actual: lc.SourceRows | None = None,
    judgements: dict[str, str] | None = None,
    task_rows: list[dict[str, Any]] | None = None,
) -> lc.LeaveCompareResult:
    return lc.build_leave_diffs(
        month=MONTH,
        job_started_at=JOB_START,
        staff_names={STAFF: NAME},
        name_index=build_name_index({str(STAFF): NAME}),
        rakusuke=rakusuke or {},
        sent=sent or {},
        tasks=tasks if tasks is not None else _weeks(task_rows or []),
        actual=actual if actual is not None else _actual(),
        judgements=judgements or {},
        shifts={(STAFF, wd): SHIFT for wd in range(6)},
    )


def _row_diffs(result: lc.LeaveCompareResult) -> list[lc.LeaveDiff]:
    return [d for d in result.diffs if d.staff_id == STAFF]


# --- 全通りの表 (§7-4-3 の 15 行) -------------------------------------------------

X, Y, Z = "off", "pm_off", "paid_off"

# (#, R, S, K, 差分の種類 or None, ボタン, 送る予定の数, 自動の S の書き込み)
#   自動: None = 書かない / ("set", 種類) / ("clear",) S=NULL で行は残す / ("delete",)
TABLE = [
    (1, None, None, None, None, None, 0, None),
    (2, None, None, X, lc.DIFF_KAIPOKE_ONLY, lc.ACTION_ADD_TO_RAKUSUKE, 0, None),
    (3, None, X, None, None, None, 0, ("delete",)),
    (4, None, X, X, lc.DIFF_KAIPOKE_DELETE_NEEDED, None, 0, None),
    (5, None, X, Y, lc.DIFF_KAIPOKE_OTHER_AFTER_CANCEL, lc.ACTION_ADD_TO_RAKUSUKE, 0, None),
    (6, X, None, None, None, None, 1, None),
    (7, X, None, X, None, None, 0, ("set", X)),
    (8, X, None, Y, lc.DIFF_TYPE_MISMATCH, lc.ACTION_MATCH_KAIPOKE_KIND, 0, None),
    (9, X, X, None, lc.DIFF_DELETED_IN_KAIPOKE, lc.ACTION_CANCEL_RAKUSUKE, 0, None),
    (10, X, X, X, None, None, 0, None),
    (11, X, X, Y, lc.DIFF_CHANGED_IN_KAIPOKE, lc.ACTION_MATCH_KAIPOKE_KIND, 0, None),
    (12, X, Y, None, None, None, 1, ("clear",)),
    (13, X, Y, X, None, None, 0, ("set", X)),
    (14, X, Y, Y, lc.DIFF_KAIPOKE_RESEND_NEEDED, None, 0, None),
    (15, X, Y, Z, lc.DIFF_KAIPOKE_OTHER, lc.ACTION_MATCH_KAIPOKE_KIND, 0, None),
]


@pytest.mark.parametrize(("case", "r", "s", "k", "kind", "action", "planned", "auto"), TABLE)
def test_full_table_row(
    case: int,
    r: str | None,
    s: str | None,
    k: str | None,
    kind: str | None,
    action: str | None,
    planned: int,
    auto: tuple | None,
) -> None:
    result = _build(
        rakusuke={(STAFF, DAY): r} if r else {},
        sent={(STAFF, DAY): lc.SentRecord(s)} if s else {},
        task_rows=[_leave_task(DAY, k)] if k else [],
    )

    diffs = _row_diffs(result)
    if kind is None:
        assert diffs == []
    else:
        assert len(diffs) == 1
        diff = diffs[0]
        assert (diff.kind, diff.case, diff.action) == (kind, case, action)
        assert (diff.rakusuke, diff.sent, diff.kaipoke) == (r, s, k)
        assert diff.day == DAY and diff.staff_name == NAME
        assert diff.message
    assert result.diff_count == (0 if kind is None else 1)
    assert result.planned_send_count == planned
    if planned:
        assert result.planned_sends[0].leave_type == r
        assert result.planned_sends[0].case == case
    assert result.unconfirmed_days == 0

    updates = result.sent_type_updates
    if auto is None:
        assert updates == []
    else:
        assert len(updates) == 1
        update = updates[0]
        assert update.case == case and (update.staff_id, update.day) == (STAFF, DAY)
        if auto[0] == "set":
            assert (update.sent_type, update.delete_row) == (auto[1], False)
        elif auto[0] == "clear":
            assert (update.sent_type, update.delete_row) == (None, False)
        else:
            assert update.delete_row is True


def test_every_leave_type_is_read_from_kaipoke() -> None:
    for kind in ("off", "am_off", "pm_off", "paid_off"):
        result = _build(
            rakusuke={(STAFF, DAY): kind},
            task_rows=[_leave_task(DAY, kind)],
        )
        assert result.diffs == []
        assert [(u.sent_type, u.case) for u in result.sent_type_updates] == [(kind, 7)]


# --- 照合前 (baseline_pending) ----------------------------------------------------


def test_baseline_rakusuke_only_is_a_diff_not_a_send() -> None:
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord(None, baseline_pending=True)},
    )
    [diff] = result.diffs
    assert (diff.kind, diff.case, diff.action) == (
        lc.DIFF_RAKUSUKE_ONLY_BASELINE,
        6,
        lc.ACTION_CANCEL_RAKUSUKE,
    )
    assert diff.baseline_pending is True
    assert result.planned_sends == []
    assert result.sent_type_updates == []


def test_baseline_matching_kaipoke_becomes_sent() -> None:
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord(None, baseline_pending=True)},
        task_rows=[_leave_task(DAY, "off")],
    )
    assert result.diffs == []
    [update] = result.sent_type_updates
    assert (update.sent_type, update.delete_row, update.case) == ("off", False, 7)


def test_baseline_row_without_any_leave_is_cleaned_up() -> None:
    # 照合前のまま、らく助で休みが消えた (R 無・S 無) → 行を片付ける。
    result = _build(sent={(STAFF, DAY): lc.SentRecord(None, baseline_pending=True)})
    assert result.diffs == []
    [update] = result.sent_type_updates
    assert update.delete_row is True and update.case == 1

    # カイポケにだけ休みがあれば差分「カイポケだけ休み」も出す。
    result = _build(
        sent={(STAFF, DAY): lc.SentRecord(None, baseline_pending=True)},
        task_rows=[_leave_task(DAY, "off")],
    )
    assert [d.kind for d in result.diffs] == [lc.DIFF_KAIPOKE_ONLY]
    assert [u.delete_row for u in result.sent_type_updates] == [True]


# --- 未確認 -----------------------------------------------------------------------


def test_stale_week_is_unconfirmed_and_nothing_moves() -> None:
    stale = lc.SourceRows(status="ok", fetched_at=OLD, rows=[_leave_task(DAY, "off")])
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        tasks=_weeks([], **{"2026-10-12": stale}),
    )
    week = [date(2026, 10, 12) + timedelta(days=i) for i in range(6)]
    assert result.unconfirmed_dates == week
    assert result.unconfirmed_days == 6
    assert {d.kind for d in result.diffs} == {lc.DIFF_UNCONFIRMED}
    assert result.diff_count == 6
    assert all(d.detail["reason"] == lc.UNCONFIRMED_STALE for d in result.diffs)
    assert all(d.action == lc.ACTION_REFETCH_WEEK for d in result.diffs)
    assert result.sent_type_updates == []  # #7 にしない
    assert result.planned_sends == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (None, lc.UNCONFIRMED_NO_SNAPSHOT),
        (lc.SourceRows(status="failed", fetched_at=FRESH), lc.UNCONFIRMED_FAILED),
    ],
)
def test_missing_or_failed_week_is_unconfirmed(source: lc.SourceRows | None, reason: str) -> None:
    tasks = _weeks([])
    if source is None:
        del tasks[date(2026, 10, 5)]
    else:
        tasks[date(2026, 10, 5)] = source
    result = _build(rakusuke={(STAFF, date(2026, 10, 7)): "off"}, tasks=tasks)
    assert result.unconfirmed_days == 6
    assert {d.detail["reason"] for d in result.diffs} == {reason}
    assert result.planned_sends == []


def test_day_not_in_covered_dates_is_unconfirmed() -> None:
    # 月をまたぐ週 (9/28〜10/3): 画面に 10/1 が出なかった。9/28〜9/30 はこの月ではない。
    monday = date(2026, 9, 28)
    covered = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-02", "2026-10-03"]
    week = lc.SourceRows(
        status="ok",
        fetched_at=FRESH,
        rows=[_leave_task(date(2026, 10, 2), "off")],
        covered_dates=covered,
    )
    result = _build(
        rakusuke={(STAFF, date(2026, 10, 1)): "off", (STAFF, date(2026, 10, 2)): "off"},
        tasks=_weeks([], **{monday.isoformat(): week}),
    )
    assert result.unconfirmed_dates == [date(2026, 10, 1)]
    [diff] = result.diffs
    assert diff.kind == lc.DIFF_UNCONFIRMED
    assert diff.detail == {"reason": lc.UNCONFIRMED_NOT_COVERED, "week_start": "2026-09-28"}
    # 10/2 は表示されたので #7。10/1 は未確認なので送る予定にもしない。
    assert [(u.day, u.case) for u in result.sent_type_updates] == [(date(2026, 10, 2), 7)]
    assert result.planned_sends == []


def test_month_crossing_week_only_counts_days_in_month() -> None:
    result = _build(tasks=_weeks([], **{"2026-09-28": lc.SourceRows("ok", OLD)}))
    assert result.unconfirmed_dates == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]


@pytest.mark.parametrize("covered", [None, []])
@pytest.mark.parametrize("monday", [date(2026, 9, 28), date(2026, 10, 12)])
def test_week_without_covered_dates_is_all_unconfirmed(
    covered: list[str] | None, monday: date
) -> None:
    """covered_dates が NULL・空の週は、画面に出た日を確かめられないので全部の日が未確認。"""
    day = max(monday, MONTH) + timedelta(days=1)
    week = lc.SourceRows(
        status="ok", fetched_at=FRESH, rows=[_leave_task(day, "off")], covered_dates=covered
    )
    result = _build(rakusuke={(STAFF, day): "off"}, tasks=_weeks([], **{monday.isoformat(): week}))
    expected = [
        monday + timedelta(days=i)
        for i in range(6)
        if (monday + timedelta(days=i)).month == MONTH.month
    ]
    assert result.unconfirmed_dates == expected
    assert {d.detail["reason"] for d in result.diffs} == {lc.UNCONFIRMED_NOT_COVERED}
    assert result.sent_type_updates == [] and result.planned_sends == []


def test_compare_plan_and_month_weeks() -> None:
    assert lc.month_mondays(MONTH) == MONDAYS
    plan = lc.compare_source_plan(MONTH)
    assert plan == [
        *(SourceSpec(KIND_TASKS, m) for m in MONDAYS),
        SourceSpec(KIND_ACTUAL_CSV, MONTH),
    ]
    # 2026-11-01 は日曜: 10/26 の週は 11 月の日 (月〜土) を含まない。
    assert lc.month_mondays(date(2026, 11, 1))[0] == date(2026, 11, 2)
    assert SUNDAY not in lc.month_days(MONTH)


def test_fetched_at_is_recorded_per_week() -> None:
    result = _build()
    assert result.fetched_at["tasks"] == {m.isoformat(): FRESH.isoformat() for m in MONDAYS}
    assert result.fetched_at["actual_csv"] == FRESH.isoformat()


# --- 種類がわからない・判定の記録 -------------------------------------------------


def test_unknown_kind_needs_a_choice() -> None:
    task = _task(DAY, "休み", "10:00", "11:00")
    result = _build(rakusuke={(STAFF, DAY): "off"}, task_rows=[task])
    [diff] = result.diffs
    assert (diff.kind, diff.action, diff.case) == (
        lc.DIFF_UNKNOWN_KIND,
        lc.ACTION_CHOOSE_KIND,
        None,
    )
    expected = lc.leave_content_hash(STAFF, DAY, "休み", time(10, 0), time(11, 0))
    assert diff.detail["content_hash"] == expected
    assert diff.detail["subject"] == "休み" and diff.detail["start"] == "10:00"
    assert result.sent_type_updates == [] and result.planned_sends == []


def test_judgement_resolves_unknown_and_new_content_shows_again() -> None:
    task = _task(DAY, "休み", "10:00", "11:00")
    content_hash = lc.leave_content_hash(STAFF, DAY, "休み", time(10, 0), time(11, 0))

    chosen = _build(
        rakusuke={(STAFF, DAY): "off"}, task_rows=[task], judgements={content_hash: "off"}
    )
    assert chosen.diffs == []
    assert [(u.sent_type, u.case) for u in chosen.sent_type_updates] == [("off", 7)]

    not_leave = _build(
        rakusuke={(STAFF, DAY): "off"},
        task_rows=[task],
        judgements={content_hash: lc.JUDGED_NOT_LEAVE},
    )
    assert not_leave.diffs == []
    assert [p.case for p in not_leave.planned_sends] == [6]  # K = 無 → 送る予定

    # 時刻が変わった → content_hash が変わり、また選ぶ。
    moved = _task(DAY, "休み", "10:00", "11:30")
    again = _build(
        rakusuke={(STAFF, DAY): "off"}, task_rows=[moved], judgements={content_hash: "off"}
    )
    assert [d.kind for d in again.diffs] == [lc.DIFF_UNKNOWN_KIND]
    # 件名が変わった → また選ぶ。
    renamed = _task(DAY, "お休み", "10:00", "11:00")
    again = _build(
        rakusuke={(STAFF, DAY): "off"}, task_rows=[renamed], judgements={content_hash: "off"}
    )
    assert [d.kind for d in again.diffs] == [lc.DIFF_UNKNOWN_KIND]


def test_content_hash_material() -> None:
    a = lc.leave_content_hash(STAFF, DAY, " 休み ", time(10, 0), time(11, 0))
    b = lc.leave_content_hash(STAFF, DAY, "休み", time(10, 0), time(11, 0))
    assert a == b and len(a) == 64
    assert a != lc.leave_content_hash(uuid.uuid4(), DAY, "休み", time(10, 0), time(11, 0))
    assert a != lc.leave_content_hash(STAFF, DAY + timedelta(days=1), "休み", time(10), time(11))
    assert lc.leave_content_hash(STAFF, DAY, "休み", None, None) != a


@pytest.mark.parametrize(
    ("title", "start", "end", "k"),
    [
        ("休み", "09:00", "13:00", "am_off"),
        ("休み", "12:30", "17:30", "pm_off"),
        ("年休", "09:00", "18:00", "paid_off"),
        ("休日当番", "09:00", "18:00", None),
    ],
)
def test_classification_examples_in_compare(
    title: str, start: str, end: str, k: str | None
) -> None:
    # 勤務 9:00〜18:00 の人が、らく助では休み無し → K があれば #2、無ければ何もない。
    result = _build(task_rows=[_task(DAY, title, start, end)])
    if k is None:
        assert result.diffs == []
    else:
        [diff] = result.diffs
        assert (diff.kind, diff.kaipoke) == (lc.DIFF_KAIPOKE_ONLY, k)


# --- 付帯の行 ---------------------------------------------------------------------


def test_two_leave_events_on_one_day() -> None:
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        task_rows=[_leave_task(DAY, "off"), _leave_task(DAY, "am_off")],
    )
    [diff] = result.diffs
    assert diff.kind == lc.DIFF_DUPLICATE_LEAVE and diff.action is None
    assert len(diff.detail["items"]) == 2
    assert result.sent_type_updates == []


def test_unknown_staff_name_with_leave_subject() -> None:
    result = _build(
        task_rows=[
            _task(DAY, "休み", name="菅野　頼子"),
            _task(DAY + timedelta(days=1), "休み", name="菅野　頼子"),
            _task(DAY, "会議", name="知らない　人"),
        ]
    )
    [diff] = result.diffs
    assert diff.kind == lc.DIFF_STAFF_NOT_FOUND
    assert diff.detail == {"name": "菅野　頼子", "dates": ["2026-10-14", "2026-10-15"]}
    assert "菅野　頼子" in diff.message


@pytest.mark.parametrize(
    ("kind", "start", "end", "conflict"),
    [
        ("off", "10:00", "11:00", True),
        ("paid_off", "16:00", "17:00", True),
        ("am_off", "11:59", "12:59", True),
        ("am_off", "13:00", "14:00", False),
        ("pm_off", "11:30", "12:30", False),
        ("pm_off", "11:31", "12:31", True),
    ],
)
def test_actual_visit_on_leave(kind: str, start: str, end: str, conflict: bool) -> None:
    result = _build(
        rakusuke={(STAFF, DAY): kind},
        sent={(STAFF, DAY): lc.SentRecord(kind)},
        task_rows=[_leave_task(DAY, kind)],
        actual=_actual([_visit(DAY, start, end)]),
    )
    kinds = [d.kind for d in result.diffs]
    if conflict:
        assert kinds == [lc.DIFF_ACTUAL_CONFLICT]
        [diff] = result.diffs
        assert diff.detail["visits"] == [{"patient": "患者　一郎", "start": start, "end": end}]
        assert diff.action is None
    else:
        assert kinds == []


def test_actual_visit_on_other_day_or_other_staff_is_ignored() -> None:
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord("off")},
        task_rows=[_leave_task(DAY, "off")],
        actual=_actual(
            [
                _visit(DAY + timedelta(days=1), "10:00", "11:00"),
                _visit(DAY, "10:00", "11:00", name="他の　人"),
            ]
        ),
    )
    assert result.diffs == []


@pytest.mark.parametrize(
    ("kind", "start", "conflict"),
    [
        ("pm_off", "11:00", True),  # 終了が空 → 午後休は当たり (見落とさない向き)
        ("am_off", "12:59", True),  # 開始だけで判定
        ("am_off", "13:00", False),
        ("off", "15:00", True),
    ],
)
def test_actual_visit_without_end_is_judged_by_start(kind: str, start: str, conflict: bool) -> None:
    visit = _visit(DAY, start, "")
    result = _build(
        rakusuke={(STAFF, DAY): kind},
        sent={(STAFF, DAY): lc.SentRecord(kind)},
        task_rows=[_leave_task(DAY, kind)],
        actual=_actual([visit]),
    )
    if conflict:
        [diff] = result.diffs
        assert diff.kind == lc.DIFF_ACTUAL_CONFLICT
        assert diff.detail["visits"] == [{"patient": "患者　一郎", "start": start, "end": None}]
    else:
        assert result.diffs == []


def test_actual_rows_are_not_deduped_across_second_staff() -> None:
    """同じ職員名１・利用者・時刻の行が 2 つあり、職員名２ だけ違う → 2 行目の同行も見る。"""
    first_row = {**_visit(DAY, "10:00", "11:00", name="他の　人"), "職員名２": "別の　人"}
    second_row = {**_visit(DAY, "10:00", "11:00", name="他の　人"), "職員名２": NAME}
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord("off")},
        task_rows=[_leave_task(DAY, "off")],
        actual=_actual([first_row, second_row, second_row]),
    )
    [diff] = result.diffs
    assert diff.kind == lc.DIFF_ACTUAL_CONFLICT
    assert len(diff.detail["visits"]) == 1  # (職員, 日) の中の同じ行は 1 件


def test_stale_actual_csv_makes_leave_days_unconfirmed() -> None:
    result = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord("off")},
        task_rows=[_leave_task(DAY, "off")],
        actual=_actual([_visit(DAY, "10:00", "11:00")], fetched_at=OLD),
    )
    [diff] = result.diffs
    assert diff.kind == lc.DIFF_UNCONFIRMED
    assert diff.detail["reason"] == lc.UNCONFIRMED_ACTUAL
    assert result.unconfirmed_dates == [DAY]


def test_failed_actual_csv_is_unconfirmed_but_fresh_empty_is_zero_visits() -> None:
    failed = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord("off")},
        task_rows=[_leave_task(DAY, "off")],
        actual=lc.SourceRows(status="failed", fetched_at=FRESH),
    )
    assert [d.detail.get("reason") for d in failed.diffs] == [lc.UNCONFIRMED_ACTUAL]

    empty = _build(
        rakusuke={(STAFF, DAY): "off"},
        sent={(STAFF, DAY): lc.SentRecord("off")},
        task_rows=[_leave_task(DAY, "off")],
        actual=_actual([]),
    )
    assert empty.diffs == [] and empty.unconfirmed_days == 0


def test_holiday_is_a_normal_working_day_and_sunday_is_skipped() -> None:
    result = _build(
        rakusuke={(STAFF, DAY_MONDAY): "off", (STAFF, SUNDAY): "off"},
        task_rows=[_leave_task(DAY_MONDAY, "off"), _leave_task(SUNDAY, "off")],
    )
    assert result.diffs == []
    assert [(u.day, u.case) for u in result.sent_type_updates] == [(DAY_MONDAY, 7)]


def test_diff_as_dict_shape() -> None:
    result = _build(task_rows=[_leave_task(DAY, "pm_off")])
    [diff] = result.diffs
    assert diff.as_dict() == {
        "kind": lc.DIFF_KAIPOKE_ONLY,
        "case": 2,
        "staff_id": str(STAFF),
        "staff_name": NAME,
        "date": "2026-10-14",
        "rakusuke": None,
        "sent": None,
        "kaipoke": "pm_off",
        "baseline_pending": False,
        "action": lc.ACTION_ADD_TO_RAKUSUKE,
        "message": "カイポケだけ休み（午後休）",
        "detail": {},
    }


# --- DB: 読む・保存する・ジョブ -----------------------------------------------------


async def _seed_staff(db) -> Staff:
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
    iso = day.isocalendar()
    return StaffWeeklyOverride(
        staff_id=staff_id,
        iso_year=iso.year,
        iso_week=iso.week,
        weekday=day.weekday(),
        override_type=kind,
    )


async def _seed_snapshots(db, job_id: uuid.UUID, task_rows: list[dict[str, Any]]) -> None:
    for monday in MONDAYS:
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
                fetched_at=datetime.now(UTC),
                job_id=job_id,
            )
        )
    db.add(
        KaipokeSourceSnapshot(
            kind=KIND_ACTUAL_CSV,
            period_start=MONTH,
            rows=[],
            row_count=0,
            status="empty",
            fetched_at=datetime.now(UTC),
            job_id=job_id,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_compare_month_saves_run_and_aligns_sent_type(db) -> None:
    staff = await _seed_staff(db)
    d7, d6, d3, d2 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)
    unknown_day = date(2026, 10, 9)
    db.add_all(
        [
            _override(staff.id, d7, "off"),  # #7 (照合前)
            _override(staff.id, d6, "am_off"),  # #6 (照合前) → 差分
            _override(staff.id, date(2026, 10, 10), "pm_off"),  # #6 → 送る予定
            _override(staff.id, date(2026, 10, 13), "custom_time"),  # 休みではない
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d7, baseline_pending=True),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d6, baseline_pending=True),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=d3, sent_type="off"),  # #3
        ]
    )
    job = await sources.start_source_job(
        db,
        op=OP_LEAVE_COMPARE,
        week_start=MONDAYS[0],
        user_id=None,
        params={"month": "2026-10"},
    )
    unknown = _task(unknown_day, "休み", "10:00", "11:00")
    content_hash = lc.leave_content_hash(staff.id, unknown_day, "休み", time(10), time(11))
    db.add(
        KaipokeLeaveJudgement(
            staff_id=staff.id,
            event_date=unknown_day,
            content_hash=content_hash,
            subject="休み",
            starts=time(10),
            ends=time(11),
            chosen_kind="paid_off",
        )
    )
    await _seed_snapshots(db, job.id, [_leave_task(d7, "off"), _leave_task(d2, "pm_off"), unknown])
    await sources.finish_source_job(db, job.id, [])

    run = await lc.compare_month(db, job.id)
    assert run is not None

    saved = await db.scalar(select(LeaveCompareRun).where(LeaveCompareRun.id == run.id))
    assert saved is not None and saved.kaipoke_job_id == job.id
    kinds = sorted((d["date"], d["kind"]) for d in saved.diffs)
    assert kinds == [
        (d6.isoformat(), lc.DIFF_RAKUSUKE_ONLY_BASELINE),
        (d2.isoformat(), lc.DIFF_KAIPOKE_ONLY),
        (unknown_day.isoformat(), lc.DIFF_KAIPOKE_ONLY),  # 選んだ種類 (有給) で #2
    ]
    assert saved.diff_count == 3
    assert saved.planned_send_count == 1
    assert saved.unconfirmed_days == 0
    assert set(saved.fetched_at["tasks"]) == {m.isoformat() for m in MONDAYS}
    # 自動でそろえた sent_type も残す。
    assert sorted(
        (u["date"], u["case"], u["sent_type"], u["delete_row"]) for u in saved.auto_updates or []
    ) == [(d7.isoformat(), 7, "off", False), (d3.isoformat(), 3, None, True)]

    rows = {
        r.leave_date: r
        for r in (
            await db.scalars(
                select(StaffLeaveKaipoke).where(StaffLeaveKaipoke.staff_id == staff.id)
            )
        ).all()
    }
    assert rows[d7].sent_type == "off" and rows[d7].baseline_pending is False  # #7
    assert rows[d6].sent_type is None and rows[d6].baseline_pending is True  # 差分のまま
    assert d3 not in rows  # #3 で行を消した

    month = await db.get(LeaveMonth, MONTH)
    assert month is not None and month.last_compare_run_id == run.id
    assert month.last_compared_at is not None


@pytest.mark.asyncio
async def test_last_compared_at_is_taken_before_reading_leaves(db) -> None:
    before = datetime.now(UTC)
    result = await lc.build_month_diffs(db, month=MONTH, job_started_at=JOB_START)
    assert result.compared_at is not None and before <= result.compared_at <= datetime.now(UTC)

    # 保存は差分を作った時刻ではなく、読む前の時刻を書く。
    result.compared_at = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
    await lc.save_compare_run(db, result=result, job_id=None, user_id=None)
    month = await db.get(LeaveMonth, MONTH)
    assert month is not None and month.last_compared_at is not None
    assert month.last_compared_at.replace(tzinfo=UTC) == result.compared_at


@pytest.mark.asyncio
async def test_compare_month_failure_is_recorded_on_job(db, monkeypatch) -> None:
    job = await sources.start_source_job(
        db, op=OP_LEAVE_COMPARE, week_start=MONDAYS[0], user_id=None, params={"month": "2026-10"}
    )
    await sources.finish_source_job(db, job.id, [])

    async def broken(*args, **kwargs):
        raise RuntimeError("こわれた")

    job_id = job.id  # compare_month の rollback で job は期限切れになる
    monkeypatch.setattr(lc, "build_month_diffs", broken)
    assert await lc.compare_month(db, job_id) is None
    refreshed = await db.get(KaipokeJob, job_id, populate_existing=True)
    assert refreshed is not None and refreshed.status == "completed"
    assert "こわれた" in refreshed.result_summary["compare_error"]
    assert refreshed.result_summary["done"] == 0  # 段階 0 の記録は残す
    assert (await db.scalars(select(LeaveCompareRun))).all() == []


@pytest.mark.asyncio
async def test_compare_month_ignores_unfinished_job(db) -> None:
    job = await sources.start_source_job(
        db, op=OP_LEAVE_COMPARE, week_start=MONDAYS[0], user_id=None, params={"month": "2026-10"}
    )
    assert await lc.compare_month(db, job.id) is None
    assert (await db.scalars(select(LeaveCompareRun))).all() == []


@pytest.mark.asyncio
async def test_snapshot_older_than_job_start_is_unconfirmed(db) -> None:
    staff = await _seed_staff(db)
    db.add(_override(staff.id, DAY, "off"))
    # 受け入れ枠などが先に取った材料 (このジョブより前)。
    old = datetime.now(UTC) - timedelta(minutes=10)
    db.add(
        KaipokeSourceSnapshot(
            kind=KIND_TASKS,
            period_start=date(2026, 10, 12),
            rows=[_leave_task(DAY, "off")],
            row_count=1,
            status="ok",
            fetched_at=old,
        )
    )
    await db.commit()
    job = await sources.start_source_job(
        db, op=OP_LEAVE_COMPARE, week_start=MONDAYS[0], user_id=None, params={"month": "2026-10"}
    )
    await sources.finish_source_job(db, job.id, [])

    run = await lc.compare_month(db, job.id)
    assert run is not None
    # どの週も今回取れていない → 月〜土の全部の日が未確認。
    assert run.unconfirmed_days == len(lc.month_days(MONTH))
    assert run.diff_count == run.unconfirmed_days
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []


@pytest.mark.asyncio
async def test_start_leave_compare_creates_fetch_job_and_plan(db) -> None:
    job, plan = await lc.start_leave_compare(db, month="2026-10", user_id=None)
    assert job.job_type == "fetch" and job.status == "running"
    assert job.params["op"] == OP_LEAVE_COMPARE and job.params["month"] == "2026-10"
    assert job.week_start == MONDAYS[0]
    assert job.result_summary["total"] == len(plan) == len(MONDAYS) + 1
    assert job.params["targets"][-1] == {"kind": KIND_ACTUAL_CSV, "period_start": "2026-10-01"}

    with pytest.raises(RpaJobConflictError):
        await lc.start_leave_compare(db, month="2026-10", user_id=None)


@pytest.mark.asyncio
async def test_run_leave_compare_job_compares_after_fetch(db, monkeypatch) -> None:
    staff = await _seed_staff(db)
    db.add(_override(staff.id, DAY, "off"))
    await db.commit()
    job, plan = await lc.start_leave_compare(db, month=MONTH, user_id=None)

    async def fake_run(session, client, job_id, specs):
        assert specs == plan
        await _seed_snapshots(session, job_id, [_leave_task(DAY, "off")])
        return await sources.finish_source_job(session, job_id, [])

    monkeypatch.setattr(sources, "run_source_job", fake_run)
    run = await lc.run_leave_compare_job(job_id=job.id, plan=plan, client=object())  # type: ignore[arg-type]
    assert run is not None and run.diff_count == 0

    row = await db.scalar(select(StaffLeaveKaipoke).where(StaffLeaveKaipoke.staff_id == staff.id))
    await db.refresh(row)
    assert row is not None and row.sent_type == "off"


@pytest.mark.asyncio
async def test_run_leave_compare_job_does_not_compare_failed_job(db, monkeypatch) -> None:
    job, plan = await lc.start_leave_compare(db, month=MONTH, user_id=None)

    async def fake_run(session, client, job_id, specs):
        return await sources.finish_source_job(session, job_id, [], error="壊れた")

    monkeypatch.setattr(sources, "run_source_job", fake_run)
    assert await lc.run_leave_compare_job(job_id=job.id, plan=plan, client=object()) is None  # type: ignore[arg-type]
    assert (await db.scalars(select(LeaveCompareRun))).all() == []
    refreshed = await db.get(KaipokeJob, job.id, populate_existing=True)
    assert refreshed is not None and refreshed.status == "failed"
