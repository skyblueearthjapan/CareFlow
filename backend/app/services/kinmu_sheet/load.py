"""勤務表の入力を DB から集める (設計 §7-1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.leave import DEFAULT_BUSINESS_END, DEFAULT_BUSINESS_START
from app.models.leave import LeaveMonth
from app.models.office import Office
from app.models.staff import Staff, StaffSecondaryOffice, StaffShift
from app.services.kinmu_sheet.build import DayOverride, KinmuStaffInput, week_sunday
from app.services.leave_calendar import (
    load_closed_days,
    load_overrides_between,
    month_days,
    staff_holiday_counts,
)
from app.services.office_labels import sorted_offices


@dataclass(frozen=True)
class KinmuInputs:
    """``build_kinmu_month`` に渡す材料."""

    month: date
    required_hours: float | None
    closed_days: dict[date, str]
    staff: list[KinmuStaffInput]


async def _kinmu_staff(db: AsyncSession) -> list[Staff]:
    """勤務表に載せる職員 (消していない・退職でない)。並びは登録ナンバー → 登録順."""
    return list(
        (
            await db.scalars(
                select(Staff)
                .where(Staff.deleted_at.is_(None), Staff.status != "retired")
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
        ).all()
    )


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


async def load_kinmu_inputs(
    db: AsyncSession, month: date, *, with_actual: bool = False
) -> KinmuInputs:
    """その月の勤務表の材料を集める.

    休みは月の 1 日を含む週の日曜から読む (週の番号に前の月の休みを数える)。
    ``with_actual=True`` (確定版) のときは、いまの休み (照合を済ませたもの) を実績にも渡す。
    """
    days = month_days(month)
    staff = await _kinmu_staff(db)
    ids = [s.id for s in staff]
    labels = await _office_labels(db, staff)
    shifts = await _shifts(db, ids)
    rows = await load_overrides_between(db, week_sunday(days[0]), days[-1], ids)
    lm = await db.get(LeaveMonth, days[0])

    inputs: list[KinmuStaffInput] = []
    for s in staff:
        overrides = {
            d: DayOverride(r.override_type, r.start_time, r.end_time)
            for d, r in rows.get(s.id, {}).items()
        }
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
                shifts=shifts.get(s.id, {}),
                overrides=overrides,
                actual_overrides=overrides if with_actual else None,
            )
        )
    return KinmuInputs(
        month=days[0],
        required_hours=lm.required_hours if lm is not None else None,
        closed_days=await load_closed_days(db, month),
        staff=inputs,
    )


async def load_holiday_counts_by_month(
    db: AsyncSession, month: date
) -> list[tuple[str, dict[date, float]]]:
    """「集計」の材料: 勤務表の職員ごとの、その年の 1 月〜その月の休日数 (有給は数えない)."""
    staff = await _kinmu_staff(db)
    ids = [s.id for s in staff]
    upto = month.replace(day=1)
    per_month: dict[UUID, dict[date, float]] = {sid: {} for sid in ids}
    for m in range(1, upto.month + 1):
        first = date(upto.year, m, 1)
        for sid, count in (await staff_holiday_counts(db, first, ids)).items():
            per_month[sid][first] = count.holidays
    return [(s.name, per_month[s.id]) for s in staff]


__all__ = ["KinmuInputs", "load_holiday_counts_by_month", "load_kinmu_inputs"]
