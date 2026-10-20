"""受け入れ枠 (数週続けて空いているか) — 材料を読む部分と API の応答づくり.

正典: ``docs/plans/acceptance-strict-design-2026-10-20.md`` §3・§4-1 (改訂 3)。

* カイポケの材料は段階 0 の ``kaipoke_source_snapshots`` (``plan_csv`` / ``actual_csv`` /
  ``tasks``) だけを読む。``kaipoke_csv_snapshots`` は読まない・書かない。
* 行は拠点で絞らない (他の拠点での訪問の時間も塞ぐ)。拠点で絞るのは担い手の候補だけ。
* 終わった日 = 実績 (入力待ちの日は予定)、今日から先 = 予定 (今日は JST)。
* 個別業務は段階 0 の表にその週・その日があればそれを、無ければ ``staff_events`` の
  ``source='kaipoke'`` 行で代える。``manual`` / ``fixed`` の予定は時間を塞ぐだけ。
* 読むだけ (DB に書かない)。計算の結果はプロセス内に覚える (材料の指紋がキー)。
"""

from __future__ import annotations

import copy
import time as _time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import leave
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.kaipoke_job import KaipokeJob
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.office import Office
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.services.kaipoke.inbound import load_staff_name_index
from app.services.kaipoke.name_match import match_name, normalize_name_key
from app.services.kaipoke.plan_actual_compare import (
    NO_STAFF_TOKENS,
    VISIT_BUSINESS_TYPES,
    norm_time,
)
from app.services.kaipoke.sources import (
    KIND_ACTUAL_CSV,
    KIND_PLAN_CSV,
    KIND_TASKS,
    OP_ACCEPTANCE_REFRESH,
    STALE_JOB_MINUTES,
    STATUS_EMPTY,
    STATUS_OK,
)
from app.services.scheduling.acceptance_strict import (
    CAP,
    MAX_WEEKS,
    WHY_FULL,
    WHY_HALF,
    WHY_TIME,
    KaipokeLeave,
    StaffDay,
    StrictRules,
    compute_strict,
    leave_breadth,
    minutes_of,
    time_of,
)
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config
from app.services.scheduling.jp_holidays import holidays_between

JST: Final = ZoneInfo("Asia/Tokyo")
_DAY_MINUTES: Final = 24 * 60
_STAFF_COLUMNS: Final = ("職員名１", "職員名２", "職員名３")
_USABLE: Final = (STATUS_OK, STATUS_EMPTY)
_ACTIVE_JOB_STATUSES: Final = ("pending", "running")
_EVENT_SOURCE_KAIPOKE: Final = "kaipoke"
#: 実績がこの割合 (予定の件数に対して) 未満なら、その日は予定で見る。
_ACTUAL_MIN_RATIO_NUM: Final = 1
_ACTUAL_MIN_RATIO_DEN: Final = 2
#: 計算の結果を覚えておく数と、念のための長さ (秒)。
_CACHE_MAX: Final = 16
_CACHE_TTL_SECONDS: Final = 600.0

#: 職員の権限の応答で理由を丸める (M9)。
_ROUNDED_WHY: Final = {WHY_FULL: "休み", WHY_HALF: "半休", WHY_TIME: "時間変更"}
_ROUNDED_UNKNOWN: Final = "休み（種類未確認）"

SRC_ACTUAL: Final = "actual"
SRC_PLAN: Final = "plan"
SRC_PLAN_FALLBACK: Final = "plan_fallback"
DAY_OK: Final = "ok"
DAY_HOLIDAY: Final = "holiday"
DAY_NO_DATA: Final = "no_data"
TASKS_FROM_SNAPSHOT: Final = "snapshot"
TASKS_FROM_STAFF_EVENTS: Final = "staff_events"


class OfficeNotFoundError(LookupError):
    """拠点が無い (API は 404)。"""


def jst_today(now: datetime | None = None) -> date:
    """今日 (JST)。VPS は UTC なので必ず JST に直してから日付にする。"""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(JST).date()


def view_mondays(today: date, weeks: int = MAX_WEEKS) -> list[date]:
    """今週を含む直近 ``weeks`` 週の月曜 (新しい週が先)。"""
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=i) for i in range(weeks)]


def months_between(first: date, last: date) -> list[date]:
    """``first``〜``last`` の日を含む月の 1 日 (古い順)。"""
    out: list[date] = []
    cur = first.replace(day=1)
    while cur <= last:
        out.append(cur)
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _parse_hm(value: Any) -> int | None:
    """``HH:MM`` → 分。``24:00`` まで読む。読めなければ None。"""
    text = norm_time(str(value or ""))
    head, sep, tail = text.partition(":")
    if not sep:
        return None
    try:
        hour, minute = int(head), int(tail[:2])
    except ValueError:
        return None
    if not (0 <= minute < 60) or hour < 0 or hour * 60 + minute > _DAY_MINUTES:
        return None
    return hour * 60 + minute


@dataclass
class _VisitRow:
    day: date
    start: int
    end: int
    names: tuple[str, str, str]
    patient: str = ""


def parse_visit_rows(rows: list[Any], period_start: date, *, dedupe: bool) -> list[_VisitRow]:
    """CSV の行 (ヘッダ名のオブジェクト) → 訪問の行。業務種別が訪問の行だけ。

    ``dedupe`` (実績) のとき、同じ職員名１・利用者・日・開始・終了の行は 1 件にまとめる。
    """
    out: list[_VisitRow] = []
    seen: set[tuple] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("業務種別") or "").strip() not in VISIT_BUSINESS_TYPES:
            continue
        try:
            day = period_start.replace(day=int(str(raw.get("日付") or "").strip()))
        except ValueError:
            continue
        start, end = _parse_hm(raw.get("開始時間")), _parse_hm(raw.get("終了時間"))
        if start is None or end is None or end <= start:
            continue
        n1, n2, n3 = (
            "" if (v := str(raw.get(col) or "").strip()) in NO_STAFF_TOKENS else v
            for col in _STAFF_COLUMNS
        )
        patient = str(raw.get("利用者") or "").strip()
        if dedupe:
            key = (normalize_name_key(n1), patient, day, start, end)
            if key in seen:
                continue
            seen.add(key)
        out.append(_VisitRow(day=day, start=start, end=end, names=(n1, n2, n3), patient=patient))
    return out


def _count_main(rows: list[_VisitRow]) -> int:
    """主担当 (職員名１) の入った行の数。同じ職員名１・利用者・時刻の行は 1 件に数える。"""
    return len(
        {(normalize_name_key(r.names[0]), r.patient, r.start, r.end) for r in rows if r.names[0]}
    )


@dataclass
class _Candidate:
    id: uuid.UUID
    name: str
    kind: str
    updated_at: datetime | None


def staff_kind(role: str | None, is_trainee: bool) -> str:
    """担い手の札。``role`` が manager / admin → manager (決定 Q11)、新人 → trainee。"""
    if role in ("manager", "admin"):
        return "manager"
    if is_trainee:
        return "trainee"
    return "staff"


async def load_candidates(db: AsyncSession, office_id: uuid.UUID) -> list[_Candidate]:
    """拠点に属する (主担当拠点か兼務) 在籍中の職員。名前の順。"""
    secondary = select(StaffSecondaryOffice.staff_id).where(
        StaffSecondaryOffice.office_id == office_id
    )
    rows = (
        await db.scalars(
            select(Staff)
            .where(
                Staff.deleted_at.is_(None),
                Staff.status == "active",
                or_(Staff.primary_office_id == office_id, Staff.id.in_(secondary)),
            )
            .order_by(Staff.name, Staff.id)
        )
    ).all()
    return [
        _Candidate(
            id=s.id,
            name=s.name,
            kind=staff_kind(s.role, bool(s.is_trainee)),
            updated_at=s.updated_at,
        )
        for s in rows
    ]


@dataclass
class _Material:
    """1 回の計算に使う材料 (DB から読んだもの)。"""

    today: date
    mondays: list[date]
    days: list[date]
    rules: StrictRules
    config: SchedulingConfig
    candidates: list[_Candidate]
    shifts: dict[tuple[uuid.UUID, int], StaffShift] = field(default_factory=dict)
    snapshots: list[KaipokeSourceSnapshot] = field(default_factory=list)


def _shift_window(shift: StaffShift, config: SchedulingConfig) -> tuple[int, int]:
    start = shift.start_time or config.business_start
    end = shift.end_time or config.business_end
    return minutes_of(start), minutes_of(end)


def _event_interval(starts_at: datetime, ends_at: datetime) -> tuple[date, int, int]:
    """``staff_events`` の時刻 (壁時計 JST のまま保存) → (日, 開始, 終了)。日をまたげば 24:00 まで。"""
    s, e = starts_at.replace(tzinfo=None), ends_at.replace(tzinfo=None)
    day = s.date()
    end = (e.hour * 60 + e.minute) if e.date() == day else _DAY_MINUTES
    return day, s.hour * 60 + s.minute, end


@dataclass
class _EventLine:
    staff_id: uuid.UUID
    day: date
    start: int
    end: int
    title: str
    kaipoke: bool


class _Builder:
    """材料 → 職員 × 日 (``StaffDay``) と日の情報。"""

    def __init__(self, material: _Material, *, plan_only: bool) -> None:
        self.m = material
        self.plan_only = plan_only
        self.candidate_ids = {c.id for c in material.candidates}
        self.unmatched: dict[str, int] = defaultdict(int)
        self.dropped_events = 0
        self.first_seen: dict[uuid.UUID, date] = {}

    def _see(self, staff_id: uuid.UUID, day: date) -> None:
        if staff_id not in self.first_seen or day < self.first_seen[staff_id]:
            self.first_seen[staff_id] = day

    def _match(self, name: str, name_index: dict[str, list[str]]) -> uuid.UUID | None:
        hit = match_name(name, name_index)
        if hit is None:
            self.unmatched[name] += 1
            return None
        return uuid.UUID(hit)

    def build(
        self,
        *,
        name_index: dict[str, list[str]],
        overrides: list[StaffWeeklyOverride],
        staff_events: list[StaffEvent],
    ) -> tuple[list[dict[str, Any]], list[StaffDay]]:
        m = self.m
        first, last = m.days[0], m.days[-1]
        snaps = {(s.kind, s.period_start): s for s in m.snapshots if s.status in _USABLE}

        # --- カイポケの訪問 (予定・実績) ---
        visits: dict[tuple[str, date], list[_VisitRow]] = defaultdict(list)
        months_ok: dict[str, set[date]] = {KIND_PLAN_CSV: set(), KIND_ACTUAL_CSV: set()}
        for kind in (KIND_PLAN_CSV, KIND_ACTUAL_CSV):
            for month in months_between(first, last):
                snap = snaps.get((kind, month))
                if snap is None:
                    continue
                months_ok[kind].add(month)
                for row in parse_visit_rows(
                    list(snap.rows or []), month, dedupe=kind == KIND_ACTUAL_CSV
                ):
                    if first <= row.day <= last:
                        visits[(kind, row.day)].append(row)

        # --- 日ごとの使い分け ---
        holidays = dict(holidays_between(first, last))
        day_info: dict[date, dict[str, Any]] = {}
        for d in m.days:
            plan_ok = d.replace(day=1) in months_ok[KIND_PLAN_CSV]
            actual_ok = d.replace(day=1) in months_ok[KIND_ACTUAL_CSV]
            info: dict[str, Any] = {
                "date": d.isoformat(),
                "state": DAY_OK,
                "holiday_name": None,
                "src": None,
                "tasks_from": None,
            }
            if self.plan_only:
                info["src"] = SRC_PLAN if plan_ok else None
                info["state"] = DAY_OK if plan_ok else DAY_NO_DATA
            elif d in holidays:
                info["state"] = DAY_HOLIDAY
                info["holiday_name"] = holidays[d]
            elif d >= m.today:
                info["src"] = SRC_PLAN if plan_ok else None
                info["state"] = DAY_OK if plan_ok else DAY_NO_DATA
            else:
                n_plan = _count_main(visits.get((KIND_PLAN_CSV, d), []))
                n_actual = _count_main(visits.get((KIND_ACTUAL_CSV, d), []))
                waiting = n_plan >= 1 and (
                    n_actual * _ACTUAL_MIN_RATIO_DEN < n_plan * _ACTUAL_MIN_RATIO_NUM
                )
                if actual_ok and not waiting:
                    info["src"] = SRC_ACTUAL
                elif plan_ok:
                    info["src"] = SRC_PLAN_FALLBACK
                else:
                    info["state"] = DAY_NO_DATA
            day_info[d] = info

        # 職員 × 日の訪問 (行は拠点で絞らない)。入職前の判定には全部の行を使う。
        staff_visits: dict[tuple[uuid.UUID, date], list[tuple[int, int]]] = defaultdict(list)
        staff_count: dict[tuple[uuid.UUID, date], int] = defaultdict(int)
        for (kind, d), rows in visits.items():
            day_rec = day_info.get(d)
            use = day_rec is not None and (
                (kind == KIND_ACTUAL_CSV and day_rec["src"] == SRC_ACTUAL)
                or (kind == KIND_PLAN_CSV and day_rec["src"] in (SRC_PLAN, SRC_PLAN_FALLBACK))
            )
            for row in rows:
                for i, name in enumerate(row.names):
                    if not name:
                        continue
                    sid = self._match(name, name_index) if use else _quiet(name, name_index)
                    if sid is None:
                        continue
                    self._see(sid, d)
                    if not use or sid not in self.candidate_ids:
                        continue
                    staff_visits[(sid, d)].append((row.start, row.end))
                    if i == 0:
                        staff_count[(sid, d)] += 1

        # --- 個別業務 (段階 0 の表 → 無い週・日は staff_events の kaipoke 行) ---
        covered: set[date] = set()
        lines: list[_EventLine] = []
        for monday in m.mondays:
            snap = snaps.get((KIND_TASKS, monday))
            if snap is None:
                continue
            week = {monday + timedelta(days=i) for i in range(6)}
            if snap.covered_dates is None:
                # 画面に出た日が分からない (RPA が week_dates を返さなかった) 週は、週の全部の
                # 日を表に出たとみなして使う。休みの照合 (leave_compare) は同じ週を「未確認」に
                # するが、あちらは実績の記録・送信を止める安全側の判定で、受け入れ枠は読むだけの
                # 見込み (間違えても書き込まない) なので、staff_events より新しい材料を優先する。
                week_covered = week
            else:
                week_covered = {
                    date.fromisoformat(str(v)) for v in snap.covered_dates if str(v)
                } & week
            covered |= week_covered
            for task in snap.rows or []:
                if not isinstance(task, dict):
                    continue
                try:
                    d = date.fromisoformat(str(task.get("date") or ""))
                except ValueError:
                    continue
                if d.weekday() == 6 or d not in week_covered:
                    continue
                start, end = _parse_hm(task.get("start")), _parse_hm(task.get("end"))
                if start is None or end is None or end <= start:
                    self.dropped_events += 1
                    continue
                staff_name = str(task.get("staff_name") or "").strip()
                if not staff_name:
                    continue
                sid = self._match(staff_name, name_index)
                if sid is None:
                    continue
                self._see(sid, d)
                lines.append(
                    _EventLine(sid, d, start, end, str(task.get("title") or "").strip(), True)
                )
        for ev in staff_events:
            d, start, end = _event_interval(ev.starts_at, ev.ends_at)
            is_kaipoke = ev.source == _EVENT_SOURCE_KAIPOKE
            if is_kaipoke and d in covered:
                continue  # 段階 0 の表にある日は読まない (二重を避ける)
            if end <= start:
                self.dropped_events += 1
                continue
            lines.append(_EventLine(ev.staff_id, d, start, end, ev.title or "", is_kaipoke))
        for d, info in day_info.items():
            info["tasks_from"] = TASKS_FROM_SNAPSHOT if d in covered else TASKS_FROM_STAFF_EVENTS

        ov_map = {(o.staff_id, o.iso_year, o.iso_week, o.weekday): o for o in overrides}
        events_by: dict[tuple[uuid.UUID, date], list[_EventLine]] = defaultdict(list)
        for line in lines:
            if line.staff_id in self.candidate_ids:
                events_by[(line.staff_id, line.day)].append(line)

        biz = (m.config.business_start, m.config.business_end)
        staff_days: list[StaffDay] = []
        for d in m.days:
            if day_info[d]["state"] != DAY_OK:
                continue
            iso = d.isocalendar()
            for c in m.candidates:
                shift = m.shifts.get((c.id, d.weekday()))
                if shift is None or not shift.is_on:
                    continue
                win = _shift_window(shift, m.config)
                sd = StaffDay(
                    staff_id=str(c.id),
                    day=d,
                    shift=win,
                    n=staff_count.get((c.id, d), 0),
                    visits=list(staff_visits.get((c.id, d), [])),
                    not_employed=(not self.plan_only)
                    and (c.id not in self.first_seen or d < self.first_seen[c.id]),
                )
                ov = ov_map.get((c.id, iso.year, iso.week, d.weekday()))
                if ov is not None:
                    sd.rk_type = ov.override_type
                    if ov.start_time is not None and ov.end_time is not None:
                        sd.rk_time = (minutes_of(ov.start_time), minutes_of(ov.end_time))
                for line in events_by.get((c.id, d), []):
                    leave_kind = (
                        leave.classify_kaipoke_leave(
                            line.title,
                            time_of(line.start),
                            time_of(line.end),
                            time_of(win[0]),
                            time_of(win[1]),
                            business_hours=biz,
                        )
                        if line.kaipoke
                        else None
                    )
                    if leave_kind in leave.LEAVE_TYPES:
                        if sd.kp_off is None or leave_breadth(leave_kind) > leave_breadth(
                            sd.kp_off.kind
                        ):
                            sd.kp_off = KaipokeLeave(kind=leave_kind, subject=line.title)
                    elif leave_kind is not None:
                        sd.kp_unknown.append((line.start, line.end, line.title))
                    else:
                        sd.events.append((line.start, line.end))
                staff_days.append(sd)
        return [day_info[d] for d in m.days], staff_days


def _quiet(name: str, name_index: dict[str, list[str]]) -> uuid.UUID | None:
    """入職前の判定だけに使う名前の結び付け (結び付かない名前は数えない)。"""
    hit = match_name(name, name_index)
    return uuid.UUID(hit) if hit is not None else None


# --- キャッシュ -------------------------------------------------------------------

_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _CACHE.clear()


async def _fingerprint(
    db: AsyncSession, model: Any, *conditions: Any
) -> tuple[int, datetime | None]:
    row = (
        await db.execute(select(func.count(), func.max(model.updated_at)).where(*conditions))
    ).one()
    return int(row[0] or 0), row[1]


async def build_strict_core(
    db: AsyncSession,
    office_id: uuid.UUID,
    *,
    today: date | None = None,
    plan_only: bool = False,
    cap: int = CAP,
) -> dict[str, Any]:
    """§4-1 の応答のうち、材料から計算する部分 (担い手・日・マス・材料の鮮度)。"""
    office = await db.get(Office, office_id)
    if office is None or office.deleted_at is not None:
        raise OfficeNotFoundError(str(office_id))
    today = today or jst_today()
    mondays = view_mondays(today)
    weekdays = sorted({int(w) for w in (office.operating_weekdays or []) if 0 <= int(w) <= 6})
    first_day, last_day = mondays[-1], mondays[0] + timedelta(days=6)
    days = [
        mondays[-1] + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
        if (mondays[-1] + timedelta(days=i)).weekday() in weekdays
    ]
    config = await load_scheduling_config(db)
    rules = StrictRules.from_config(config, cap=cap)
    candidates = await load_candidates(db, office_id)
    candidate_ids = [c.id for c in candidates]
    shifts = (
        (await db.scalars(select(StaffShift).where(StaffShift.staff_id.in_(candidate_ids)))).all()
        if candidate_ids
        else []
    )
    months = months_between(first_day, last_day)
    snap_meta = (
        await db.execute(
            select(
                KaipokeSourceSnapshot.id,
                KaipokeSourceSnapshot.kind,
                KaipokeSourceSnapshot.period_start,
                KaipokeSourceSnapshot.status,
                KaipokeSourceSnapshot.fetched_at,
            ).where(
                or_(
                    KaipokeSourceSnapshot.kind.in_((KIND_PLAN_CSV, KIND_ACTUAL_CSV))
                    & KaipokeSourceSnapshot.period_start.in_(months),
                    (KaipokeSourceSnapshot.kind == KIND_TASKS)
                    & KaipokeSourceSnapshot.period_start.in_(mondays),
                )
            )
        )
    ).all()
    weeks = {(m.isocalendar().year, m.isocalendar().week) for m in mondays}
    range_start = datetime.combine(first_day, time.min)
    range_end = datetime.combine(last_day + timedelta(days=1), time.min)
    ev_conditions = (
        StaffEvent.staff_id.in_(candidate_ids),
        StaffEvent.cancelled_at.is_(None),
        StaffEvent.starts_at >= range_start,
        StaffEvent.starts_at < range_end,
    )
    ov_conditions = (
        StaffWeeklyOverride.staff_id.in_(candidate_ids),
        StaffWeeklyOverride.iso_year.in_({y for y, _ in weeks}),
    )
    key = (
        office_id,
        today,
        plan_only,
        cap,
        tuple(weekdays),
        config,
        tuple((c.id, c.name, c.kind, c.updated_at) for c in candidates),
        tuple(sorted((s.staff_id, s.weekday, s.is_on, s.start_time, s.end_time) for s in shifts)),
        tuple(
            sorted((str(r.id), r.kind, r.period_start, r.status, r.fetched_at) for r in snap_meta)
        ),
        await _fingerprint(db, Staff),
        await _fingerprint(db, StaffWeeklyOverride, *ov_conditions),
        await _fingerprint(db, StaffEvent, *ev_conditions),
    )
    hit = _CACHE.get(key)
    if hit is not None and _time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]

    snapshots = (
        (
            await db.scalars(
                select(KaipokeSourceSnapshot).where(
                    KaipokeSourceSnapshot.id.in_([r.id for r in snap_meta])
                )
            )
        ).all()
        if snap_meta
        else []
    )
    overrides = (
        (await db.scalars(select(StaffWeeklyOverride).where(*ov_conditions))).all()
        if candidate_ids
        else []
    )
    staff_events = (
        (await db.scalars(select(StaffEvent).where(*ev_conditions))).all() if candidate_ids else []
    )
    name_index, _ = await load_staff_name_index(db)
    material = _Material(
        today=today,
        mondays=mondays,
        days=days,
        rules=rules,
        config=config,
        candidates=candidates,
        shifts={(s.staff_id, s.weekday): s for s in shifts},
        snapshots=list(snapshots),
    )
    builder = _Builder(material, plan_only=plan_only)
    day_rows, staff_days = (
        builder.build(
            name_index=name_index, overrides=list(overrides), staff_events=list(staff_events)
        )
        if days
        else ([], [])
    )
    usable = [r for r in snap_meta if r.status in _USABLE]
    core: dict[str, Any] = {
        "office_id": str(office_id),
        "today": today.isoformat(),
        "plan_only": plan_only,
        "weekdays": weekdays,
        "weeks": [
            {
                "iso_year": mo.isocalendar().year,
                "iso_week": mo.isocalendar().week,
                "monday": mo.isoformat(),
            }
            for mo in mondays
        ],
        "rules": rules.as_dict(),
        "staff": [
            {
                "id": str(c.id),
                "name": c.name,
                "kind": c.kind,
                "default_selected": c.kind == "staff",
            }
            for c in candidates
        ],
        "days": day_rows,
        "cells": compute_strict(staff_days, rules),
        "sources": {
            "oldest_fetched_at": (
                min(r.fetched_at for r in usable).isoformat() if usable else None
            ),
            "snapshots": [
                {
                    "kind": r.kind,
                    "period_start": r.period_start.isoformat(),
                    "status": r.status,
                    "fetched_at": r.fetched_at.isoformat(),
                }
                for r in sorted(usable, key=lambda r: (r.kind, r.period_start))
            ],
            "unmatched_staff_names": dict(sorted(builder.unmatched.items())),
            "unmatched_staff_count": sum(builder.unmatched.values()),
            "dropped_events": builder.dropped_events,
        },
    }
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (_time.monotonic(), core)
    return core


def _op_filter(db: AsyncSession, op: str) -> Any:
    """``params.op`` で絞る条件 (PG は JSONB の ``->>``、SQLite は ``json_extract``)。"""
    if db.get_bind().dialect.name == "postgresql":
        return KaipokeJob.params["op"].astext == op
    return func.json_extract(KaipokeJob.params, "$.op") == op


def _job_touched(job: KaipokeJob) -> datetime | None:
    touched = job.updated_at or job.created_at
    if touched is not None and touched.tzinfo is None:
        touched = touched.replace(tzinfo=UTC)
    return touched


def is_stale(job: KaipokeJob, now: datetime | None = None) -> bool:
    """動いていることになっているが、段階 0 の残骸の時間を超えて更新が無い (止まったまま)。"""
    if job.status not in _ACTIVE_JOB_STATUSES:
        return False
    touched = _job_touched(job)
    limit = (now or datetime.now(UTC)) - timedelta(minutes=STALE_JOB_MINUTES)
    return touched is not None and touched < limit


async def latest_refresh_job(db: AsyncSession, *, active_only: bool = False) -> KaipokeJob | None:
    """受け入れ枠の取り直しのジョブのうち、いちばん新しいもの。

    ``active_only`` のときは動いているものだけ (残骸は返さない)。
    """
    query = (
        select(KaipokeJob)
        .where(_op_filter(db, OP_ACCEPTANCE_REFRESH))
        .order_by(KaipokeJob.created_at.desc())
    )
    if active_only:
        query = query.where(KaipokeJob.status.in_(_ACTIVE_JOB_STATUSES))
    for job in (await db.scalars(query.limit(20))).all():
        if active_only and is_stale(job):
            continue
        return job
    return None


def job_view(job: KaipokeJob) -> dict[str, Any]:
    """取り直しのジョブの進み具合 (画面に出すもの)。"""
    summary = job.result_summary or {}
    done = int(summary.get("done") or 0)
    targets = list((job.params or {}).get("targets") or [])
    # 進み具合 (done・failed・最後に終わった本) を書くのは段階 0 の record_progress だけ。
    # 受け入れ枠は書かず、立てたときの本の一覧 (params.targets) の done 番目を
    # 「いま取っている本」として読むだけ (終わったら段階 0 の current = 最後に終わった本)。
    current = (
        targets[done]
        if job.status in _ACTIVE_JOB_STATUSES and done < len(targets)
        else summary.get("current")
    )
    return {
        "id": str(job.id),
        "status": job.status,
        "scope": (job.params or {}).get("scope"),
        "done": done,
        "total": summary.get("total"),
        "current": current,
        "failed": list(summary.get("failed") or []),
        "error": summary.get("error"),
        "updated_at": touched.isoformat() if (touched := _job_touched(job)) else None,
        # 残骸 (更新が止まったまま): 画面は動いていないとみなす (ボタンを押せる・読みに行かない)。
        "stale": is_stale(job),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


async def load_standing_overrides(db: AsyncSession, office_id: uuid.UUID) -> list[dict[str, Any]]:
    """毎週の上書き (``acceptance_calendar``)。「10:00」の上書き = 10 時台のマス。"""
    rows = (
        await db.scalars(
            select(AcceptanceCalendar)
            .where(AcceptanceCalendar.office_id == office_id)
            .order_by(AcceptanceCalendar.weekday, AcceptanceCalendar.time_slot)
        )
    ).all()
    return [
        {
            "weekday": r.weekday,
            "hour": r.time_slot.hour,
            "time_slot": r.time_slot.isoformat(),
            "status": r.status,
            "note": r.notes,
        }
        for r in rows
    ]


async def build_strict_payload(
    db: AsyncSession,
    office_id: uuid.UUID,
    *,
    today: date | None = None,
    plan_only: bool = False,
    cap: int = CAP,
) -> dict[str, Any]:
    """``GET /acceptance-matrix/strict`` の応答 (管理者の形。職員には ``round_for_staff``)。"""
    core = await build_strict_core(db, office_id, today=today, plan_only=plan_only, cap=cap)
    job = await latest_refresh_job(db)
    payload = dict(core)
    payload["overrides"] = await load_standing_overrides(db, office_id)
    payload["sources"] = {**core["sources"], "last_job": job_view(job) if job else None}
    return payload


def round_for_staff(payload: dict[str, Any]) -> dict[str, Any]:
    """職員の権限の応答: 休みの理由を「休み」「半休」に丸め、結び付かない職員名は件数だけにする (M9)。"""
    out = dict(payload)
    cells = copy.deepcopy(payload["cells"])
    for by_day in cells.values():
        for cell in by_day.values():
            for mode in ("real", "ignore"):
                rec = cell.get(mode)
                if not rec:
                    continue
                if rec.get("why"):
                    rec["why"] = _ROUNDED_WHY.get(rec.get("why_kind") or "", "")
                if rec.get("unknown_leave"):
                    rec["unknown_leave"] = _ROUNDED_UNKNOWN
    out["cells"] = cells
    # らく助に無い職員名は件数だけ (名前は出さない)。
    out["sources"] = {**payload["sources"], "unmatched_staff_names": {}}
    return out


__all__ = [
    "DAY_HOLIDAY",
    "DAY_NO_DATA",
    "DAY_OK",
    "JST",
    "SRC_ACTUAL",
    "SRC_PLAN",
    "SRC_PLAN_FALLBACK",
    "TASKS_FROM_SNAPSHOT",
    "TASKS_FROM_STAFF_EVENTS",
    "OfficeNotFoundError",
    "build_strict_core",
    "build_strict_payload",
    "clear_cache",
    "job_view",
    "jst_today",
    "latest_refresh_job",
    "load_candidates",
    "load_standing_overrides",
    "months_between",
    "parse_visit_rows",
    "round_for_staff",
    "staff_kind",
    "view_mondays",
]
