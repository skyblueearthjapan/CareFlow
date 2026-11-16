"""「この週を空にする」— 青ピン廃止 BP-3 / BP-4 (PO 2026-10-12).

設計: ``docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md``

* 残す訪問 = 打刻・実績・サインのあるもの (``keep_checked_in``) と取消の記録
  (``keep_cancelled``)。**それ以外は出所・状態を問わずすべて消す**
  (カイポケ由来・送信済みも消す)。
* 消し方は週のコピーの置き換えと同じ論理削除 (``deleted_at``)。残す訪問の無い
  コースは担当を外す (盤面とコース担当を食い違わせない)。
* 「週を生成」は **空の週でだけ** 押せる。空 = ここで消す対象が 0 件。
* 期間で組む (``agent-period-build-design-2026-11-16.md`` §3-1): ``date_from``・``date_to``
  を渡すと、その日だけを見る (範囲の外の日の訪問とコースの担当には触れない)。
  渡さなければ週全体 (今までどおり)。
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


def range_error(monday: date, date_from: date | None, date_to: date | None) -> str | None:
    """期間 (``date_from``・``date_to``) の検査。よければ None、だめなら断る理由 (422 用).

    どちらも無い (週全体) か、どちらもあってこの週の月曜〜日曜の中・from ≦ to のとき通す。
    """
    if date_from is None and date_to is None:
        return None
    if date_from is None or date_to is None:
        return "期間は date_from と date_to の両方を指定してください（週全体なら両方とも省きます）"
    sunday = monday + timedelta(days=6)
    for d in (date_from, date_to):
        if not (monday <= d <= sunday):
            return (
                f"期間の日 {d.isoformat()} がこの週（{monday.isoformat()}〜{sunday.isoformat()}）"
                "の外です。期間は同じ週の中で指定してください"
            )
    if date_from > date_to:
        return f"期間の始まり {date_from.isoformat()} が終わり {date_to.isoformat()} より後です"
    return None


def range_dates(date_from: date | None, date_to: date | None) -> set[date] | None:
    """期間の日の集合 (両端を含む)。範囲なし (どちらかが None) なら None = 週全体.

    「空にする」と「型から週を作る」の両方がこれで範囲を日に開く (二つがずれないように)。
    検査は ``range_error`` で先に済ませておくこと。
    """
    if date_from is None or date_to is None:
        return None
    return {date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)}


@dataclass
class ClearPlan:
    monday: date
    to_delete: list[Visit] = field(default_factory=list)
    kept_checked_in: list[Visit] = field(default_factory=list)
    kept_cancelled: list[Visit] = field(default_factory=list)
    #: 担当を外すコース (残す訪問が無いもの)
    cleared_courses: list[Course] = field(default_factory=list)
    #: 範囲つきのとき、範囲の外の日の (生きている) 訪問。触らずに残す。
    #: そのコース (範囲の中の曜日を指すことがある) の担当も外さない。
    outside_range: list[Visit] = field(default_factory=list)

    @property
    def kaipoke_linked(self) -> int:
        return sum(1 for v in self.to_delete if v.kaipoke_id)


async def plan_clear(
    db: AsyncSession,
    monday: date,
    office_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ClearPlan:
    """消す・残すを決める (読むだけ・commit しない).

    ``date_from``・``date_to`` (どちらもこの週の中・検査は呼び出し側) を渡すと、その日の
    訪問と、その曜日のコースだけを見る。無ければ週全体。
    """
    days = range_dates(date_from, date_to)
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
    outside: list[Visit] = []
    if days is not None:
        outside = [v for v in visits if v.visit_date not in days]
        visits = [v for v in visits if v.visit_date in days]
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
    plan = ClearPlan(monday=monday, outside_range=outside)
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
    # 範囲の外の訪問が範囲の中の曜日のコースを指すことがある (日付を変えた後の名残)。
    # その担当を外すと範囲の外の訪問の担当まで変わるので、そのコースも残す。
    live_kept_courses |= {v.course_id for v in outside if v.course_id is not None}
    iso = monday.isocalendar()
    course_stmt = select(Course).where(
        Course.iso_year == iso.year,
        Course.iso_week == iso.week,
        Course.deleted_at.is_(None),
        Course.assigned_staff_id.is_not(None),
    )
    if office_id is not None:
        course_stmt = course_stmt.where(Course.office_id == office_id)
    if days is not None:
        course_stmt = course_stmt.where(Course.weekday.in_(sorted(d.weekday() for d in days)))
    plan.cleared_courses = [
        c for c in (await db.scalars(course_stmt)).all() if c.id not in live_kept_courses
    ]
    return plan


async def week_is_empty(
    db: AsyncSession,
    monday: date,
    office_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bool:
    """「週を生成」を押してよいか: 消す対象 (= 残す訪問・取消以外) が 1 件も無い.

    範囲つきなら、範囲の中に消す対象が無ければよい (範囲の外の予定は問わない)。
    """
    return not (await plan_clear(db, monday, office_id, date_from, date_to)).to_delete


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
