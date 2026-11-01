"""受け入れ枠を「月で見る」— 材料を読む部分と API の応答づくり.

試算: ``docs/reports/agent/2026-11-受け入れ枠の月の埋まり.html`` (PO 決定 2026-11-01)。

* 訪問はらく助の ``visits`` (取消・削除を除く) だけを読む。先の月は予定、過去の月は訪問の記録で、
  どちらも同じ表。カイポケの材料 (段階 0 の表) は使わない (過去の分が残っていないため)。
* 勤務の型は今の ``staff_shifts`` (履歴が無い)。休みはらく助の週だけの休み
  (``staff_weekly_overrides``) と ``staff_events`` (カイポケから取り込んだ個別業務を含む)。
  休みの判定は今の受け入れ枠と同じ (``app.core.leave``)。
* 祝日は平日として数える (実際に訪問しているため)。訪問が ``OPEN_DAY_MIN_VISITS`` 件に
  満たない日は、休業か予定がまだ無い日として数えない。
* 行は拠点で絞らない (他の拠点での訪問の時間も塞ぐ)。拠点で絞るのは担い手の候補だけ。
* 読むだけ (DB に書かない)。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import leave
from app.models.patient import Patient
from app.models.staff import StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.scheduling.acceptance_month import (
    BUSY_DAY_VISITS,
    CROWDED_UTILIZATION,
    OPEN_DAY_MIN_VISITS,
    SLOT_MIN,
    TREND_POINTS,
    MonthStaffDay,
    MonthVisit,
    add_trend,
    month_rules,
    month_slots,
    summarize_month,
)
from app.services.scheduling.acceptance_strict import (
    KaipokeLeave,
    StaffDay,
    StrictRules,
    hhmm,
    leave_breadth,
    minutes_of,
    time_of,
)
from app.services.scheduling.acceptance_strict_data import (
    _Candidate,
    _load_offices,
    jst_today,
    load_candidates,
)
from app.services.scheduling.auto_allocator import _resolve_weekday
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config

_DAY_MINUTES: Final = 24 * 60
_EVENT_SOURCE_KAIPOKE: Final = "kaipoke"
_TIME_FIXED: Final = "固定"

#: 数え方の出どころ (画面の注記に使う)。
SRC_PLAN: Final = "plan"
SRC_RECORD: Final = "record"
SRC_MIXED: Final = "mixed"


class InvalidMonthError(ValueError):
    """月の書き方が違う (API は 422)。"""


def parse_month(value: str) -> date:
    """``YYYY-MM`` → その月の 1 日。"""
    try:
        head, tail = value.split("-")
        out = date(int(head), int(tail), 1)
    except (ValueError, TypeError) as exc:
        raise InvalidMonthError(value) from exc
    if not 2020 <= out.year <= 2100:
        raise InvalidMonthError(value)
    return out


def _next_month(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def _prev_month(first: date) -> date:
    return (first - timedelta(days=1)).replace(day=1)


def _fixed_weekdays(pattern: Any) -> set[int] | None:
    """利用者の登録で時間が「固定」の曜日。曜日の無い「固定」は全部の曜日 (None)。"""
    if not isinstance(pattern, dict):
        return set()
    out: set[int] = set()
    for entry in pattern.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("time_type") != _TIME_FIXED:
            continue
        wd = _resolve_weekday(entry.get("weekday"))
        if wd is None:
            return None
        out.add(wd)
    return out


def _event_interval(starts_at: datetime, ends_at: datetime) -> tuple[date, int, int]:
    """``staff_events`` の時刻 (壁時計 JST のまま保存) → (日, 開始, 終了)。日をまたげば 24:00 まで。"""
    s, e = starts_at.replace(tzinfo=None), ends_at.replace(tzinfo=None)
    day = s.date()
    end = (e.hour * 60 + e.minute) if e.date() == day else _DAY_MINUTES
    return day, s.hour * 60 + s.minute, end


@dataclass
class _MonthMaterial:
    month_days: list[date]
    open_days: list[date]
    staff_days: list[MonthStaffDay]
    visit_count: int


async def _load_month(
    db: AsyncSession,
    first: date,
    weekdays: list[int],
    candidates: list[_Candidate],
    shifts: dict[tuple[uuid.UUID, int], StaffShift],
    config: SchedulingConfig,
) -> _MonthMaterial:
    last = _next_month(first) - timedelta(days=1)
    month_days = [
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() in weekdays
    ]
    visits = (
        await db.scalars(
            select(Visit).where(
                Visit.visit_date >= first,
                Visit.visit_date <= last,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
            )
        )
    ).all()
    patient_ids = {v.patient_id for v in visits}
    patterns = (
        dict(
            (
                await db.execute(
                    select(Patient.id, Patient.weekly_pattern).where(Patient.id.in_(patient_ids))
                )
            ).all()
        )
        if patient_ids
        else {}
    )
    fixed_by_patient = {pid: _fixed_weekdays(p) for pid, p in patterns.items()}

    per_day: dict[date, int] = defaultdict(int)
    candidate_ids = {c.id for c in candidates}
    blocks: dict[tuple[uuid.UUID, date], list[tuple[int, int]]] = defaultdict(list)
    primary: dict[tuple[uuid.UUID, date], list[MonthVisit]] = defaultdict(list)
    for v in visits:
        per_day[v.visit_date] += 1
        start, end = minutes_of(v.start_time), minutes_of(v.end_time)
        if end <= start:
            continue
        for sid in dict.fromkeys(
            s for s in (v.primary_staff_id, v.secondary_staff_id, v.mentor_staff_id) if s
        ):
            if sid in candidate_ids:
                blocks[(sid, v.visit_date)].append((start, end))
        if v.primary_staff_id in candidate_ids:
            fixed = fixed_by_patient.get(v.patient_id, set())
            primary[(v.primary_staff_id, v.visit_date)].append(
                MonthVisit(
                    start=start,
                    end=end,
                    time_fixed=fixed is None or v.visit_date.weekday() in fixed,
                )
            )
    open_days = [d for d in month_days if per_day.get(d, 0) >= OPEN_DAY_MIN_VISITS]

    ids = list(candidate_ids)
    weeks = {(d.isocalendar().year, d.isocalendar().week) for d in month_days}
    overrides = (
        (
            await db.scalars(
                select(StaffWeeklyOverride).where(
                    StaffWeeklyOverride.staff_id.in_(ids),
                    StaffWeeklyOverride.iso_year.in_({y for y, _ in weeks}),
                    StaffWeeklyOverride.iso_week.in_({w for _, w in weeks}),
                )
            )
        ).all()
        if ids
        else []
    )
    ov_map = {(o.staff_id, o.iso_year, o.iso_week, o.weekday): o for o in overrides}
    events = (
        (
            await db.scalars(
                select(StaffEvent).where(
                    StaffEvent.staff_id.in_(ids),
                    StaffEvent.cancelled_at.is_(None),
                    StaffEvent.starts_at >= datetime.combine(first, time.min),
                    StaffEvent.starts_at < datetime.combine(last + timedelta(days=1), time.min),
                )
            )
        ).all()
        if ids
        else []
    )
    events_by: dict[tuple[uuid.UUID, date], list[tuple[int, int, str, bool]]] = defaultdict(list)
    for ev in events:
        d, start, end = _event_interval(ev.starts_at, ev.ends_at)
        if end > start:
            events_by[(ev.staff_id, d)].append(
                (start, end, ev.title or "", ev.source == _EVENT_SOURCE_KAIPOKE)
            )

    biz = (config.business_start, config.business_end)
    staff_days: list[MonthStaffDay] = []
    for d in open_days:
        iso = d.isocalendar()
        for c in candidates:
            shift = shifts.get((c.id, d.weekday()))
            if shift is None or not shift.is_on:
                continue
            win = (
                minutes_of(shift.start_time or config.business_start),
                minutes_of(shift.end_time or config.business_end),
            )
            mine = primary.get((c.id, d), [])
            sd = StaffDay(
                staff_id=str(c.id),
                day=d,
                shift=win,
                n=len(mine),
                visits=list(blocks.get((c.id, d), [])),
            )
            ov = ov_map.get((c.id, iso.year, iso.week, d.weekday()))
            if ov is not None:
                sd.rk_type = ov.override_type
                if ov.start_time is not None and ov.end_time is not None:
                    sd.rk_time = (minutes_of(ov.start_time), minutes_of(ov.end_time))
            for start, end, title, is_kaipoke in events_by.get((c.id, d), []):
                kind = (
                    leave.classify_kaipoke_leave(
                        title,
                        time_of(start),
                        time_of(end),
                        time_of(win[0]),
                        time_of(win[1]),
                        business_hours=biz,
                    )
                    if is_kaipoke
                    else None
                )
                if kind in leave.LEAVE_TYPES:
                    if sd.kp_off is None or leave_breadth(kind) > leave_breadth(sd.kp_off.kind):
                        sd.kp_off = KaipokeLeave(kind=kind, subject=title)
                elif kind is not None:
                    sd.kp_unknown.append((start, end, title))
                else:
                    sd.events.append((start, end))
            staff_days.append(MonthStaffDay(sd=sd, primary=mine))
    return _MonthMaterial(
        month_days=month_days, open_days=open_days, staff_days=staff_days, visit_count=len(visits)
    )


def _source_of(first: date, today: date) -> str:
    if first > today:
        return SRC_PLAN
    if _next_month(first) <= today:
        return SRC_RECORD
    return SRC_MIXED


async def build_month_view(
    db: AsyncSession,
    month: date,
    office_id: uuid.UUID | None,
    *,
    staff_ids: list[uuid.UUID] | None = None,
    mode: str = "real",
    lunch: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """「月で見る」の応答。``staff_ids`` が None なら既定の担い手 (職員だけ)。"""
    first = month.replace(day=1)
    today = today or jst_today()
    offices = await _load_offices(db, office_id)
    weekdays = sorted(
        {int(w) for o in offices for w in (o.operating_weekdays or []) if 0 <= int(w) <= 6}
    )
    config = await load_scheduling_config(db)
    base_rules = StrictRules.from_config(config)
    rules = month_rules(base_rules, lunch=lunch)
    candidates = await load_candidates(db, [o.id for o in offices]) if offices else []
    candidate_ids = [c.id for c in candidates]
    if staff_ids is None:
        selected = {str(c.id) for c in candidates if c.kind == "staff"}
    else:
        wanted = set(staff_ids)
        selected = {str(c.id) for c in candidates if c.id in wanted}
    shift_rows = (
        (await db.scalars(select(StaffShift).where(StaffShift.staff_id.in_(candidate_ids)))).all()
        if candidate_ids
        else []
    )
    shifts = {(s.staff_id, s.weekday): s for s in shift_rows}
    use_off = mode != "ignore"

    cur = await _load_month(db, first, weekdays, candidates, shifts, config)
    prev_first = _prev_month(first)
    prev = await _load_month(db, prev_first, weekdays, candidates, shifts, config)
    summary = summarize_month(
        cur.month_days, cur.open_days, cur.staff_days, selected, weekdays, rules, use_off=use_off
    )
    prev_summary = summarize_month(
        prev.month_days, prev.open_days, prev.staff_days, selected, weekdays, rules, use_off=use_off
    )
    add_trend(summary, prev_summary)
    open_set = set(cur.open_days)
    return {
        "month": first.strftime("%Y-%m"),
        "prev_month": prev_first.strftime("%Y-%m"),
        "office_id": str(office_id) if office_id is not None else None,
        "offices": [{"id": str(o.id), "name": o.name} for o in offices],
        "today": today.isoformat(),
        "source": _source_of(first, today),
        "mode": "real" if use_off else "ignore",
        "lunch": lunch,
        "weekdays": weekdays,
        "slots": [hhmm(s) for s in month_slots()],
        "rules": {
            **base_rules.as_dict(),
            "lunch_considered": lunch,
            "slot_min": SLOT_MIN,
            "crowded_utilization": CROWDED_UTILIZATION,
            "trend_points": TREND_POINTS,
            "busy_day_visits": BUSY_DAY_VISITS,
            "open_day_min_visits": OPEN_DAY_MIN_VISITS,
        },
        "staff": [
            {
                "id": str(c.id),
                "name": c.name,
                "kind": c.kind,
                "default_selected": c.kind == "staff",
                "office_id": str(c.office_id) if c.office_id else None,
            }
            for c in candidates
        ],
        "selected_staff_ids": sorted(selected),
        "days": [{"date": d.isoformat(), "open": d in open_set} for d in cur.month_days],
        "visit_count": cur.visit_count,
        "prev_utilization": prev_summary["utilization"],
        **summary,
    }


__all__ = [
    "SRC_MIXED",
    "SRC_PLAN",
    "SRC_RECORD",
    "InvalidMonthError",
    "build_month_view",
    "parse_month",
]
