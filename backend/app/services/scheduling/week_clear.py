"""「この週を空にする」— 青ピン廃止 BP-3 / BP-4 (PO 2026-10-12).

設計: ``docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md``

* 残す訪問 = 打刻・実績・サインのあるもの (``keep_checked_in``) と取消の記録
  (``keep_cancelled``)。**それ以外は出所・状態を問わずすべて消す**
  (カイポケ由来・送信済みも消す)。
* 消し方は週のコピーの置き換えと同じ論理削除 (``deleted_at``)。残す訪問の無い
  コースは担当を外す (盤面とコース担当を食い違わせない)。
* 「週を生成」は **空の週でだけ** 押せる。空 = ここで消す対象が 0 件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.models.course import COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.patient import Patient
from app.models.visit import (
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_signature import VisitSignature

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: スナップショットの種別 (inbound_snapshots.kind・String(16))。
SNAPSHOT_KIND_CLEAR_WEEK = "clear_week"

#: 「週を生成」を断るときの理由 (BP-3)。
GENERATE_NEEDS_EMPTY_WEEK_MESSAGE = (
    "予定が入っている週では生成できません。先に『この週を空にする』を押してください"
)


@dataclass
class ClearPlan:
    monday: date
    to_delete: list[Visit] = field(default_factory=list)
    kept_checked_in: list[Visit] = field(default_factory=list)
    kept_cancelled: list[Visit] = field(default_factory=list)
    #: 担当を外すコース (残す訪問が無いもの)
    cleared_courses: list[Course] = field(default_factory=list)

    @property
    def kaipoke_linked(self) -> int:
        return sum(1 for v in self.to_delete if v.kaipoke_id)


async def plan_clear(db: AsyncSession, monday: date, office_id: UUID | None = None) -> ClearPlan:
    """消す・残すを決める (読むだけ・commit しない)."""
    stmt = select(Visit).where(
        Visit.visit_date >= monday,
        Visit.visit_date <= monday + timedelta(days=6),
        Visit.deleted_at.is_(None),
    )
    if office_id is not None:
        stmt = stmt.join(Patient, Patient.id == Visit.patient_id).where(
            Patient.primary_office_id == office_id
        )
    visits = list((await db.scalars(stmt.order_by(Visit.visit_date, Visit.start_time))).all())
    ids = [v.id for v in visits]
    checked: set[UUID] = set()
    if ids:
        checked |= set(
            (
                await db.scalars(
                    select(VisitCheckin.visit_id).where(VisitCheckin.visit_id.in_(ids))
                )
            ).all()
        )
        checked |= set(
            (
                await db.scalars(
                    select(VisitSignature.visit_id).where(VisitSignature.visit_id.in_(ids))
                )
            ).all()
        )
    plan = ClearPlan(monday=monday)
    for v in visits:
        if (
            v.id in checked
            or v.is_unplanned
            or v.status in (VISIT_STATUS_IN_PROGRESS, VISIT_STATUS_COMPLETED)
        ):
            plan.kept_checked_in.append(v)
        elif v.status == VISIT_STATUS_CANCELLED:
            plan.kept_cancelled.append(v)
        else:
            plan.to_delete.append(v)

    live_kept_courses = {v.course_id for v in plan.kept_checked_in if v.course_id is not None}
    iso = monday.isocalendar()
    course_stmt = select(Course).where(
        Course.iso_year == iso.year,
        Course.iso_week == iso.week,
        Course.deleted_at.is_(None),
        Course.assigned_staff_id.is_not(None),
    )
    if office_id is not None:
        course_stmt = course_stmt.where(Course.office_id == office_id)
    plan.cleared_courses = [
        c for c in (await db.scalars(course_stmt)).all() if c.id not in live_kept_courses
    ]
    return plan


async def week_is_empty(db: AsyncSession, monday: date, office_id: UUID | None = None) -> bool:
    """「週を生成」を押してよいか: 消す対象 (= 残す訪問・取消以外) が 1 件も無い."""
    return not (await plan_clear(db, monday, office_id)).to_delete


def apply_clear(plan: ClearPlan) -> int:
    """計画を書く (commit しない・スナップショットは呼び出し側で先に取る)."""
    now = datetime.now(UTC)
    for v in plan.to_delete:
        v.deleted_at = now
    for c in plan.cleared_courses:
        c.assigned_staff_id = None
        c.staff_assigned_at = None
        if c.course_status == COURSE_STATUS_STAFF_ASSIGNED:
            c.course_status = COURSE_STATUS_COURSE_FIXED
    return len(plan.to_delete)
