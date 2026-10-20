"""勤務表の入力を DB から集める (設計 §7-1・§7-2).

* 型 (``staff_shifts``) は履歴を持たないので、過去の月もいまの型で計算する。
* 確定版 (``with_actual=True``) の「予定」は、予定版を作ったときの写し
  (``kinmu_sheets.plan_snapshot``・最新の版) から作り、「実績」はいまの (照合を済ませた) 休みと
  いまの型で作る。写しが無い月はいまの予定を使い、``plan_source='current'`` で知らせる。
  予定版を作る側 (#13) は ``snapshot_plan(inputs.staff)`` を ``plan_snapshot`` に置く。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.leave import DEFAULT_BUSINESS_END, DEFAULT_BUSINESS_START, LEAVE_TYPES
from app.models.leave import KinmuSheet, LeaveMonth
from app.models.office import Office
from app.models.staff import Staff, StaffSecondaryOffice, StaffShift
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.kinmu_sheet.build import (
    DayOverride,
    KinmuRules,
    KinmuStaffInput,
    plan_from_snapshot,
    week_sunday,
)
from app.services.leave_calendar import (
    count_holidays,
    load_closed_days,
    load_overrides_between,
    load_working_weekdays,
    month_days,
)
from app.services.office_labels import sorted_offices

PlanSource = Literal["current", "snapshot"]


@dataclass(frozen=True)
class KinmuInputs:
    """``build_kinmu_month`` に渡す材料.

    ``plan_source``: 予定の行の元。``snapshot`` = 予定版を作ったときの写し・``current`` = いまの休み
    (予定版・または写しが無い月の確定版)。
    """

    month: date
    required_hours: float | None
    closed_days: dict[date, str]
    staff: list[KinmuStaffInput]
    plan_source: PlanSource = "current"


async def _kinmu_staff(db: AsyncSession, month: date, rules: KinmuRules) -> list[Staff]:
    """勤務表に載せる職員 (消していない・退職でない。退職者もその月に訪問か休みの登録があれば載せる)。

    並びは登録ナンバー → 登録順。
    """
    rows = list(
        (
            await db.scalars(
                select(Staff)
                .where(Staff.deleted_at.is_(None))
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
        ).all()
    )
    retired = [s.id for s in rows if s.status == "retired"]
    active: set[UUID] = set()
    if retired and rules.include_retired_with_activity:
        days = month_days(month)
        for col in (Visit.primary_staff_id, Visit.secondary_staff_id):
            active.update(
                sid
                for sid in (
                    await db.scalars(
                        select(col).where(
                            col.in_(retired),
                            Visit.visit_date >= days[0],
                            Visit.visit_date <= days[-1],
                            Visit.status != VISIT_STATUS_CANCELLED,
                        )
                    )
                ).all()
                if sid is not None
            )
        leaves = await load_overrides_between(db, days[0], days[-1], retired)
        active.update(
            sid
            for sid, by_day in leaves.items()
            if any(r.override_type in LEAVE_TYPES for r in by_day.values())
        )
    return [s for s in rows if s.status != "retired" or s.id in active]


async def _office_labels(db: AsyncSession, staff: list[Staff]) -> dict[UUID, str]:
    """職員 → 所属拠点の名前 (応援拠点があれば「稲毛・都賀」のように続ける)."""
    ids = [s.id for s in staff]
    secondary: dict[UUID, list[UUID]] = {}
    if ids:
        for row in (
            await db.execute(
                select(StaffSecondaryOffice.staff_id, StaffSecondaryOffice.office_id).where(
                    StaffSecondaryOffice.staff_id.in_(ids)
                )
            )
        ).all():
            secondary.setdefault(row.staff_id, []).append(row.office_id)
    offices = {o.id: o for o in (await db.scalars(select(Office))).all()}
    out: dict[UUID, str] = {}
    for s in staff:
        names: list[str] = []
        if s.primary_office_id in offices:
            names.append(offices[s.primary_office_id].name)
        others = [
            offices[oid]
            for oid in secondary.get(s.id, [])
            if oid in offices and oid != s.primary_office_id
        ]
        names.extend(o.name for o in sorted_offices(others))
        out[s.id] = "・".join(names)
    return out


async def _shifts(db: AsyncSession, ids: list[UUID]) -> dict[UUID, dict[int, tuple[time, time]]]:
    """職員 → 型で勤務する曜日の (始業, 終業)。時刻が空なら営業時間の既定."""
    out: dict[UUID, dict[int, tuple[time, time]]] = {sid: {} for sid in ids}
    if not ids:
        return out
    for sh in (await db.scalars(select(StaffShift).where(StaffShift.staff_id.in_(ids)))).all():
        if sh.is_on:
            out.setdefault(sh.staff_id, {})[sh.weekday] = (
                sh.start_time or DEFAULT_BUSINESS_START,
                sh.end_time or DEFAULT_BUSINESS_END,
            )
    return out


async def _latest_plan_snapshot(db: AsyncSession, month: date) -> dict | None:
    """その月の最新の予定版の写し (無ければ None)."""
    return await db.scalar(
        select(KinmuSheet.plan_snapshot)
        .where(
            KinmuSheet.month == month,
            KinmuSheet.kind == "plan",
            KinmuSheet.plan_snapshot.is_not(None),
        )
        .order_by(KinmuSheet.version.desc())
        .limit(1)
    )


async def load_kinmu_inputs(
    db: AsyncSession,
    month: date,
    *,
    with_actual: bool = False,
    rules: KinmuRules | None = None,
) -> KinmuInputs:
    """その月の勤務表の材料を集める.

    休みは月の 1 日を含む週の日曜から読む (週の番号に前の月の休みを数える)。
    ``with_actual=True`` (確定版) は予定に写しを、実績にいまの休みを渡す (モジュールの説明)。
    """
    rules = rules or KinmuRules()
    days = month_days(month)
    staff = await _kinmu_staff(db, days[0], rules)
    ids = [s.id for s in staff]
    labels = await _office_labels(db, staff)
    shifts = await _shifts(db, ids)
    rows = await load_overrides_between(db, week_sunday(days[0]), days[-1], ids)
    lm = await db.get(LeaveMonth, days[0])
    snapshot = await _latest_plan_snapshot(db, days[0]) if with_actual else None

    inputs: list[KinmuStaffInput] = []
    for s in staff:
        current = {
            d: DayOverride(r.override_type, r.start_time, r.end_time)
            for d, r in rows.get(s.id, {}).items()
        }
        current_shifts = shifts.get(s.id, {})
        plan_shifts, plan = current_shifts, current
        if snapshot is not None:
            snap = plan_from_snapshot(snapshot, str(s.id))
            if snap is not None:
                plan_shifts, plan = snap
        inputs.append(
            KinmuStaffInput(
                key=str(s.id),
                name=s.name,
                job=s.qualification or "",
                title=s.kinmu_title,
                work_form=s.work_form,
                office_label=labels.get(s.id, ""),
                full_time=s.employment_type == "full_time",
                admin_hours_per_day=s.admin_hours_per_day,
                shifts=plan_shifts,
                overrides=plan,
                actual_overrides=current if with_actual else None,
                actual_shifts=current_shifts if with_actual else None,
                on_leave=s.status == "on_leave",
            )
        )
    return KinmuInputs(
        month=days[0],
        required_hours=lm.required_hours if lm is not None else None,
        closed_days=await load_closed_days(db, month),
        staff=inputs,
        plan_source="snapshot" if snapshot is not None else "current",
    )


async def load_holiday_counts_by_month(
    db: AsyncSession, month: date, *, rules: KinmuRules | None = None
) -> list[tuple[str, dict[date, float] | None]]:
    """「集計」の材料: 勤務表の職員ごとの、その年の 1 月〜その月の休日数 (有給は数えない).

    休みは 1 月 1 日〜その月の末日を 1 回で読む。非常勤は None (「対象外」)。
    """
    rules = rules or KinmuRules()
    upto = month_days(month)
    staff = await _kinmu_staff(db, upto[0], rules)
    ids = [s.id for s in staff]
    first = date(upto[0].year, 1, 1)
    working = await load_working_weekdays(db, ids)
    rows = await load_overrides_between(db, first, upto[-1], ids)
    months = [date(first.year, m, 1) for m in range(1, upto[0].month + 1)]
    out: list[tuple[str, dict[date, float] | None]] = []
    for s in staff:
        if s.employment_type != "full_time" and not rules.part_time_marks:
            out.append((s.name, None))
            continue
        types = {d: r.override_type for d, r in rows.get(s.id, {}).items()}
        out.append(
            (
                s.name,
                {
                    m: count_holidays(month_days(m), working.get(s.id, set()), types).holidays
                    for m in months
                },
            )
        )
    return out


__all__ = ["KinmuInputs", "PlanSource", "load_holiday_counts_by_month", "load_kinmu_inputs"]
