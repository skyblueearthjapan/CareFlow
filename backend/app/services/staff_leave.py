"""休みを入れる・取り消す共通の処理と、休みの時間帯の後始末 (休み設計 §3・§4-2).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §3-1〜§3-4・§4-2。

* ``apply_staff_leave`` — 休み (``LEAVE_TYPES``) を入れる・種類を変える。override は
  ``set_staff_override`` (唯一の書き口) で upsert し、今日 (JST) 以降なら休みの時間帯の
  本人の訪問を担当なしへ (または引き受け先へ) 移す。
* ``cancel_staff_leave`` — 休みを取り消す。担当なしへ移した訪問・外したコース・取消の印は
  戻さない。
* ``settle_leave_unassign`` — 訪問を作る・担当を書く処理の最後に必ず呼ぶ後始末。
* ``find_visits_on_leave`` / ``count_visits_on_leave`` — 漏れの検知
  (今日以降・休みの時間帯に本人が担当の planned の訪問)。

どの関数も **commit しない** (呼び出し側のトランザクションに乗せる)。

担当なしの付け方 (§3-2):
  * **半休**: コースは本人のまま。休みの時間帯にかかる訪問だけ ``primary=NULL・manual=TRUE``
    (明示の担当なし)・本人の VSA を外す。
  * **終日 (公休・有給)**: その日のコース担当が本人なら外し (その週のその曜日だけ)、訪問は
    ``primary=NULL・manual=FALSE`` (コースごと担当なし)。

「本人の訪問」= primary が本人／primary が空・manual でない・コース担当が本人／
2 名体制の 2 人目 (VSA に本人)。打刻済み・実施中・完了・取消済みは動かさない。
同行 (``accompaniments``) は書き換えず、件数だけ返す (§3-3 の 6)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.leave import FULL_DAY_OFF, HALF_DAY_OFF, LEAVE_TYPES, visit_hits_leave
from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.pending_request import PendingRequest
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings_for_staff,
    constraint_confirmation_detail,
    notify_constraint_override,
)
from app.services.leave_calendar import iso_key, load_overrides_between
from app.services.leave_notify import (
    UnassignedNotice,
    leave_type_label,
    notify_leave_approved,
    notify_leave_cancelled,
    notify_leave_unassigned,
)
from app.services.op_log_service import (
    clear_redo_branch,
    fmt_time,
    fmt_weekday,
    record_op,
    set_staff_events_cancelled,
    set_staff_override,
    set_visit_staff_slot,
    visit_staff_ids,
)
from app.services.staff_event_defaults import EVENT_SOURCE_FIXED

_JST: Final = ZoneInfo("Asia/Tokyo")

#: ``staff_weekly_overrides.source`` の値。
LEAVE_SOURCES: Final = frozenset({"request", "admin", "kaipoke"})
#: どこから入れた休みか (op_log の payload に残す・§3-3)。
LEAVE_ORIGINS: Final = frozenset(
    {"leave_page", "cockpit", "request_list", "staff_detail", "agent", "kaipoke_compare", "excel"}
)

#: 同じ (職員, 日) の休みが同時に書かれて UNIQUE がぶつかったときの 409 の文言 (入口で使う)。
LEAVE_WRITE_CONFLICT_DETAIL: Final = "同じ日の休みが同時に入れられました。読み込み直してください"

#: op_log へ 1 行書く関数 (``record_op`` の op_group を束ねたもの)。
Journal = Callable[..., Awaitable[None]]


class StaffLeaveError(Exception):
    """休みの処理を止める理由 (API は ``status_code`` と ``detail`` をそのまま返す)."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class DayUnassignResult:
    """1 人 1 日の担当なしへの移し替えの結果."""

    moved_visit_ids: list[UUID] = field(default_factory=list)
    moved_course_ids: list[UUID] = field(default_factory=list)
    skipped_visit_ids: list[UUID] = field(default_factory=list)
    moved_patient_ids: set[UUID] = field(default_factory=set)


@dataclass
class LeaveApplyResult:
    """``apply_staff_leave`` の結果."""

    override_id: UUID | None
    staff_id: UUID
    day: date
    leave_type: str
    #: 今日 (JST) より前の日 = 休みの記録だけ (訪問・コース・固定イベントは不変)。
    past_day: bool
    op_group_id: UUID
    to_staff_id: UUID | None = None
    moved_visit_ids: list[UUID] = field(default_factory=list)
    moved_course_ids: list[UUID] = field(default_factory=list)
    skipped_visit_ids: list[UUID] = field(default_factory=list)
    cancelled_event_ids: list[UUID] = field(default_factory=list)
    #: 本人が同行者に入っている休みの時間帯の訪問の数 (同行は書き換えない)。
    accompaniment_count: int = 0
    #: 移した訪問の患者の数 (通知の「N 名」)。
    moved_patient_count: int = 0


@dataclass(frozen=True)
class VisitOnLeave:
    """休みの時間帯に本人が担当している planned の訪問 (漏れの検知)."""

    visit_id: UUID
    staff_id: UUID
    visit_date: date
    start_time: time
    end_time: time
    patient_id: UUID
    leave_type: str


def today_jst() -> date:
    """今日 (JST)."""
    return datetime.now(UTC).astimezone(_JST).date()


def leave_request_deadline(target: date) -> datetime:
    """休みの申請の締め切り = 対象日の月の前の月の 20 日 23:59 (JST) の終わり (§2-3).

    戻り値は「この時刻より後は締め切り後」の境目 (前の月の 21 日 0:00 JST・aware)。
    """
    prev_month = target.replace(day=1) - timedelta(days=1)
    return datetime(prev_month.year, prev_month.month, 21, tzinfo=_JST)


def is_past_leave_deadline(target: date, now: datetime | None = None) -> bool:
    """今 (``now``・既定は現在) が対象日の申請の締め切りを過ぎているか."""
    current = now or datetime.now(UTC)
    return current >= leave_request_deadline(target)


def _date_label(day: date) -> str:
    return f"{day.month}/{day.day}({fmt_weekday(day.weekday())})"


# ---------------------------------------------------------------------------
# 本人の訪問と担当なしへの移し替え (apply と後始末で共通)
# ---------------------------------------------------------------------------


def _owns_primary(visit: Visit, course: Course | None, staff_id: UUID) -> bool:
    """primary の枠を本人が握っているか (盤面の行決定と同じ・明示の担当なしは除く)."""
    if visit.primary_staff_id is not None:
        return visit.primary_staff_id == staff_id
    if visit.manual_staff_override:
        return False
    return course is not None and course.assigned_staff_id == staff_id


@dataclass
class _DayData:
    """その日の訪問 (コースつき) と VSA {visit_id: {staff_id}} (1 日 1 回だけ読む)."""

    rows: list[tuple[Visit, Course | None]]
    vsa: dict[UUID, set[UUID]]


async def _load_day(db: AsyncSession, day: date) -> _DayData:
    rows = [
        (v, c)
        for v, c in (
            await db.execute(
                select(Visit, Course)
                .outerjoin(Course, (Course.id == Visit.course_id) & (Course.deleted_at.is_(None)))
                .where(Visit.visit_date == day, Visit.deleted_at.is_(None))
            )
        ).all()
    ]
    vsa: dict[UUID, set[UUID]] = {}
    if rows:
        for vid, sid in (
            await db.execute(
                select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id.in_([v.id for v, _c in rows])
                )
            )
        ).all():
            vsa.setdefault(vid, set()).add(sid)
    return _DayData(rows=rows, vsa=vsa)


#: (訪問, primary を握るか, コース)。
_Target = tuple[Visit, bool, "Course | None"]


async def _owned_hit_visits(
    db: AsyncSession,
    staff_id: UUID,
    day: date,
    leave_type: str,
    day_data: _DayData | None = None,
) -> tuple[list[_Target], list[Visit]]:
    """本人の訪問のうち休みの時間帯にかかるもの → (planned の対象, それ以外).

    本人の訪問 = primary が本人／primary が空・manual でない・コース担当が本人／
    2 名体制の 2 人目 (VSA に本人・古い行は ``secondary_staff_id`` が本人)。
    ``day_data`` を渡せばその日の読み込みを使い回す (後始末で 1 日 1 回)。
    """
    data = day_data or await _load_day(db, day)
    targets: list[_Target] = []
    skipped: list[Visit] = []
    for visit, course in data.rows:
        on_primary = _owns_primary(visit, course, staff_id)
        second = staff_id in data.vsa.get(visit.id, set()) or visit.secondary_staff_id == staff_id
        if not on_primary and not second:
            continue
        if not visit_hits_leave(leave_type, visit.start_time, visit.end_time):
            continue
        if visit.status != VISIT_STATUS_PLANNED:
            skipped.append(visit)
            continue
        targets.append((visit, on_primary, course))
    targets.sort(key=lambda item: (item[0].start_time, str(item[0].id)))
    skipped.sort(key=lambda v: (v.start_time, str(v.id)))
    return targets, skipped


async def _unassign_day(
    db: AsyncSession,
    *,
    staff_id: UUID,
    day: date,
    leave_type: str,
    targets: list[_Target],
    to_staff_id: UUID | None,
    day_data: _DayData | None = None,
    journal: Journal | None,
    to_label: str,
) -> DayUnassignResult:
    """休みの時間帯の本人の訪問を担当なし (または引き受け先) へ移し、終日ならコースも外す."""
    result = DayUnassignResult()
    full_day = leave_type in FULL_DAY_OFF
    label_day = _date_label(day)
    for visit, on_primary, course in targets:
        # 半休はコースを本人のまま残すので、訪問は「明示の担当なし」(manual=True)。
        # 終日で本人のコースの訪問はコースごと担当なし (manual=False・あとでコースに人を
        # 付ければ一緒に付く)。終日でも他人のコース (やコース無し) の訪問は、コース担当に
        # 読み替えないよう明示の担当なし (manual=True)。引き受け先があれば True。
        new_manual = (
            to_staff_id is not None
            or not full_day
            or course is None
            or course.assigned_staff_id != staff_id
        )
        if visit.secondary_staff_id == staff_id:
            # 古い行の 2 人目の列 (VSA に居ない場合もある)。本人の枠だけ空ける。
            visit.secondary_staff_id = None
        old_primary = visit.primary_staff_id
        old_manual = bool(visit.manual_staff_override)
        current_vsa = await visit_staff_ids(db, visit.id)
        vsa_remove = [staff_id] if staff_id in current_vsa else []
        vsa_add = (
            [to_staff_id] if to_staff_id is not None and to_staff_id not in current_vsa else []
        )
        await set_visit_staff_slot(
            db,
            visit_id=visit.id,
            set_primary=on_primary,
            primary_staff_id=to_staff_id,
            manual=new_manual,
            vsa_add=vsa_add,
            vsa_remove=vsa_remove,
        )
        if day_data is not None:
            members = day_data.vsa.setdefault(visit.id, set())
            members.difference_update(vsa_remove)
            members.update(vsa_add)
        result.moved_visit_ids.append(visit.id)
        result.moved_patient_ids.add(visit.patient_id)
        if journal is not None:
            await journal(
                op_kind="set_visit_staff_slot",
                label=f"{label_day}の訪問担当を{to_label}に変更",
                forward_payload={
                    "op": "set_visit_staff_slot",
                    "visit_id": str(visit.id),
                    "set_primary": on_primary,
                    "primary_staff_id": str(to_staff_id) if to_staff_id else None,
                    "manual": new_manual,
                    "vsa_add": [str(s) for s in vsa_add],
                    "vsa_remove": [str(s) for s in vsa_remove],
                },
                inverse_payload={
                    "op": "set_visit_staff_slot",
                    "visit_id": str(visit.id),
                    "set_primary": on_primary,
                    "primary_staff_id": str(old_primary) if old_primary else None,
                    "manual": old_manual,
                    "vsa_add": [str(s) for s in vsa_remove],
                    "vsa_remove": [str(s) for s in vsa_add],
                },
            )

    if full_day:
        y, w, wd = iso_key(day)
        courses = (
            await db.scalars(
                select(Course).where(
                    Course.iso_year == y,
                    Course.iso_week == w,
                    Course.weekday == wd,
                    Course.assigned_staff_id == staff_id,
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
        for course in courses:
            old_course_staff = course.assigned_staff_id
            course.assigned_staff_id = to_staff_id
            result.moved_course_ids.append(course.id)
            if journal is not None:
                await journal(
                    op_kind="set_course_staff",
                    label=f"コース{course.code}の担当を{to_label}に変更",
                    forward_payload={
                        "op": "set_course_staff",
                        "course_id": str(course.id),
                        "staff_id": str(to_staff_id) if to_staff_id else None,
                    },
                    inverse_payload={
                        "op": "set_course_staff",
                        "course_id": str(course.id),
                        "staff_id": str(old_course_staff) if old_course_staff else None,
                    },
                )
        await db.flush()
    return result


async def _cancel_fixed_events(
    db: AsyncSession, *, staff_id: UUID, day: date, leave_type: str, journal: Journal | None
) -> list[UUID]:
    """その日の固定イベント (source='fixed') のうち休みの時間帯にかかるものに取消の印."""
    day_start = datetime.combine(day, time.min)
    events = (
        await db.scalars(
            select(StaffEvent)
            .where(
                StaffEvent.staff_id == staff_id,
                StaffEvent.starts_at >= day_start,
                StaffEvent.starts_at < day_start + timedelta(days=1),
                StaffEvent.source == EVENT_SOURCE_FIXED,
                StaffEvent.cancelled_at.is_(None),
            )
            .order_by(StaffEvent.starts_at, StaffEvent.id)
        )
    ).all()
    # 予定の時刻は JST の壁時計をそのまま入れる決まり (.time() がそのまま壁時計)。
    ids = [
        e.id for e in events if visit_hits_leave(leave_type, e.starts_at.time(), e.ends_at.time())
    ]
    if ids:
        await set_staff_events_cancelled(db, ids, True)
        if journal is not None:
            ids_json = [str(i) for i in ids]
            await journal(
                op_kind="cancel_staff_event",
                label=f"{_date_label(day)}の固定イベント{len(ids)}件を取消",
                forward_payload={"op": "cancel_staff_event", "event_ids": ids_json, "cancel": True},
                inverse_payload={
                    "op": "cancel_staff_event",
                    "event_ids": ids_json,
                    "cancel": False,
                },
            )
    return ids


async def _count_accompaniments(
    db: AsyncSession, *, staff_id: UUID, day: date, leave_type: str
) -> int:
    """本人が同行者に入っている、その日の休みの時間帯の訪問の数 (取消・削除は除く).

    その日の訪問から引く (訪問への同行と、訪問のコースへの同行)。全期間の同行は読まない。
    """
    visit_link = select(Accompaniment.visit_id).where(
        Accompaniment.accompanying_staff_id == staff_id,
        Accompaniment.target_type == "visit",
    )
    course_link = select(Accompaniment.course_id).where(
        Accompaniment.accompanying_staff_id == staff_id,
        Accompaniment.target_type == "course",
    )
    visits = (
        await db.scalars(
            select(Visit).where(
                Visit.visit_date == day,
                Visit.deleted_at.is_(None),
                Visit.status != "cancelled",
                or_(Visit.id.in_(visit_link), Visit.course_id.in_(course_link)),
            )
        )
    ).all()
    return sum(1 for v in visits if visit_hits_leave(leave_type, v.start_time, v.end_time))


# ---------------------------------------------------------------------------
# 入れる・取り消す
# ---------------------------------------------------------------------------


def _journal_for(
    db: AsyncSession, actor: User | None, day: date, op_group_id: UUID
) -> Journal | None:
    if actor is None:
        return None
    y, w, _wd = iso_key(day)
    return partial(
        record_op,
        db,
        user_id=actor.id,
        iso_year=y,
        iso_week=w,
        op_group_id=op_group_id,
        strict=True,
        clear_redo=False,
    )


def _override_payload(
    staff_id: UUID, day: date, row: StaffWeeklyOverride | None, origin: str
) -> dict[str, Any]:
    """``set_staff_off`` の payload (行の今の状態・無ければ type=None)."""
    y, w, wd = iso_key(day)
    return {
        "op": "set_staff_off",
        "staff_id": str(staff_id),
        "iso_year": y,
        "iso_week": w,
        "weekday": wd,
        "type": row.override_type if row is not None else None,
        "reason": row.reason if row is not None else None,
        "start_time": fmt_time(row.start_time) if row is not None and row.start_time else None,
        "end_time": fmt_time(row.end_time) if row is not None and row.end_time else None,
        "source": row.source if row is not None else None,
        "pending_request_id": (
            str(row.pending_request_id) if row is not None and row.pending_request_id else None
        ),
        "origin": origin,
    }


async def _load_override(db: AsyncSession, staff_id: UUID, day: date) -> StaffWeeklyOverride | None:
    y, w, wd = iso_key(day)
    return await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == y,
            StaffWeeklyOverride.iso_week == w,
            StaffWeeklyOverride.weekday == wd,
        )
    )


async def _check_to_staff(
    db: AsyncSession,
    *,
    staff_id: UUID,
    to_staff_id: UUID,
    day: date,
) -> tuple[Staff, str | None]:
    """引き受け先の検査 (新人・退職・本人・終日休み) → (職員, その日の休みの種類)."""
    if to_staff_id == staff_id:
        raise StaffLeaveError(422, "休む本人には引き受けられません")
    to_staff = await db.scalar(
        select(Staff).where(Staff.id == to_staff_id, Staff.deleted_at.is_(None))
    )
    if to_staff is None:
        raise StaffLeaveError(404, "Staff not found")
    if to_staff.is_trainee:
        raise StaffLeaveError(422, "新人は担当にできません（同行で割り当ててください）")
    if to_staff.status != "active":
        raise StaffLeaveError(422, "退職された方には引き受けられません")
    row = await _load_override(db, to_staff_id, day)
    to_leave = row.override_type if row is not None and row.override_type in LEAVE_TYPES else None
    if to_leave in FULL_DAY_OFF:
        raise StaffLeaveError(422, f"{to_staff.name}さんはこの日休みのため引き受けられません")
    return to_staff, to_leave


async def apply_staff_leave(
    db: AsyncSession,
    *,
    staff_id: UUID,
    day: date,
    leave_type: str,
    source: str,
    note: str | None,
    actor: User | None,
    op_group_id: UUID | None,
    origin: str,
    request: PendingRequest | None = None,
    to_staff_id: UUID | None = None,
    acknowledge_constraint_warnings: bool = False,
    notify: bool = True,
    pending_request_id: UUID | None = None,
) -> LeaveApplyResult:
    """休みを入れる・種類を変える (§3-3)。1 トランザクション・同じ op_group (commit しない).

    1. override を upsert (``source``・``pending_request_id``・``reason=note``)。
    2. 今日 (JST) より前の日なら、ここで終わり (休みの記録だけ・通知は本人への承認だけ)。
    3. 休みの時間帯の本人の planned の訪問を担当なし (``to_staff_id`` があれば引き受け先) へ。
    4. 終日ならその日のコース担当を外す (半休ではコースは動かさない)。
    5. 休みの時間帯の固定イベントに取消の印。
    6. 同行は書き換えず件数だけ返す。
    7. 通知 (``notify=False`` なら呼び出し側がまとめて出す)。

    Raises:
        StaffLeaveError: 種類・職員・引き受け先の検査 (404 / 422)。
    """
    if leave_type not in LEAVE_TYPES:
        raise StaffLeaveError(422, f"休みの種類ではありません: {leave_type}")
    if source not in LEAVE_SOURCES:
        raise StaffLeaveError(422, f"休みの出どころが不正です: {source}")
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise StaffLeaveError(404, "Staff not found")

    group = op_group_id or uuid.uuid4()
    past = day < today_jst()
    result = LeaveApplyResult(
        override_id=None,
        staff_id=staff_id,
        day=day,
        leave_type=leave_type,
        past_day=past,
        op_group_id=group,
        to_staff_id=to_staff_id,
    )

    to_staff: Staff | None = None
    to_leave: str | None = None
    if to_staff_id is not None and not past:
        to_staff, to_leave = await _check_to_staff(
            db, staff_id=staff_id, to_staff_id=to_staff_id, day=day
        )

    targets: list[_Target] = []
    skipped: list[Visit] = []
    warnings: list[ConstraintWarning] = []
    if not past:
        targets, skipped = await _owned_hit_visits(db, staff_id, day, leave_type)
        if to_staff is not None and to_leave in HALF_DAY_OFF:
            for visit, _p, _c in targets:
                if visit_hits_leave(to_leave, visit.start_time, visit.end_time):
                    raise StaffLeaveError(
                        422, f"{to_staff.name}さんはこの時間は休みのため引き受けられません"
                    )
        if to_staff is not None and targets:
            warnings = await collect_constraint_warnings_for_staff(
                db, staff_id=to_staff.id, patient_ids=list({v.patient_id for v, _p, _c in targets})
            )
            if warnings and not acknowledge_constraint_warnings:
                raise StaffLeaveError(422, constraint_confirmation_detail(warnings))

    journal = _journal_for(db, actor, day, group)
    if actor is not None:
        y, w, _wd = iso_key(day)
        await clear_redo_branch(db, user_id=actor.id, iso_year=y, iso_week=w)

    # (1) override の upsert
    prev = await _load_override(db, staff_id, day)
    inverse = _override_payload(staff_id, day, prev, origin)
    y, w, wd = iso_key(day)
    # 承認元の申請 (``request`` が無いときは引数の値＝今の行の値を引き継ぐ経路用)。
    if request is not None:
        pending_request_id = request.id
    override_id = await set_staff_override(
        db,
        staff_id=staff_id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type=leave_type,
        reason=note,
        source=source,
        pending_request_id=pending_request_id,
    )
    result.override_id = override_id
    if journal is not None:
        forward = {
            **inverse,
            "type": leave_type,
            "reason": note,
            "start_time": None,
            "end_time": None,
            "source": source,
            "pending_request_id": str(pending_request_id) if pending_request_id else None,
            "override_id": str(override_id),
        }
        inverse["override_id"] = str(override_id)
        await journal(
            op_kind="set_staff_off",
            label=f"{staff.name}さんを{_date_label(day)}{leave_type_label(leave_type)}に",
            forward_payload=forward,
            inverse_payload=inverse,
        )

    if past:
        # (2) 過去の日は休みの記録だけ (実績には触れない・H3)。
        if notify and request is not None:
            await notify_leave_approved(db, staff_id=staff_id, target=day, leave_type=leave_type)
        await db.flush()
        return result

    # (3)(4) 訪問とコース
    to_label = (to_staff.name if to_staff is not None else None) or "担当なし"
    moved = await _unassign_day(
        db,
        staff_id=staff_id,
        day=day,
        leave_type=leave_type,
        targets=targets,
        to_staff_id=to_staff_id,
        journal=journal,
        to_label=to_label,
    )
    result.moved_visit_ids = moved.moved_visit_ids
    result.moved_course_ids = moved.moved_course_ids
    result.moved_patient_count = len(moved.moved_patient_ids)
    result.skipped_visit_ids = [v.id for v in skipped]

    # (5) 固定イベント
    result.cancelled_event_ids = await _cancel_fixed_events(
        db, staff_id=staff_id, day=day, leave_type=leave_type, journal=journal
    )
    # (6) 同行はそのまま・件数だけ
    result.accompaniment_count = await _count_accompaniments(
        db, staff_id=staff_id, day=day, leave_type=leave_type
    )

    # (7) 通知
    if warnings and to_staff is not None:
        await notify_constraint_override(
            db,
            kind_summary={x.kind for x in warnings},
            course=None,
            staff=to_staff,
            patient_warnings=warnings,
            actor=actor,
            op_group_id=group,
        )
    if notify:
        if to_staff_id is None and result.moved_visit_ids:
            await notify_leave_unassigned(
                db,
                [
                    UnassignedNotice(
                        staff_name=staff.name,
                        day=day,
                        patient_count=result.moved_patient_count,
                        visit_count=len(result.moved_visit_ids),
                        accompaniment_count=result.accompaniment_count,
                    )
                ],
            )
        if request is not None:
            await notify_leave_approved(db, staff_id=staff_id, target=day, leave_type=leave_type)
    await db.flush()
    return result


async def cancel_staff_leave(
    db: AsyncSession,
    *,
    staff_id: UUID,
    day: date,
    actor: User | None,
    op_group_id: UUID | None,
    origin: str,
    notify: bool = True,
) -> str:
    """休みを取り消す (§3-3)。担当なしへ移した訪問・外したコース・取消の印は戻さない.

    Returns:
        取り消した休みの種類。

    Raises:
        StaffLeaveError: その日に休みの登録が無い (404)。
    """
    row = await _load_override(db, staff_id, day)
    if row is None or row.override_type not in LEAVE_TYPES:
        raise StaffLeaveError(404, "この日に休みの登録はありません")
    leave_type = row.override_type
    staff_name = await db.scalar(select(Staff.name).where(Staff.id == staff_id))
    group = op_group_id or uuid.uuid4()
    inverse = _override_payload(staff_id, day, row, origin)
    inverse["override_id"] = str(row.id)
    journal = _journal_for(db, actor, day, group)
    if actor is not None:
        y, w, _wd = iso_key(day)
        await clear_redo_branch(db, user_id=actor.id, iso_year=y, iso_week=w)

    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=None
    )
    if journal is not None:
        forward = {
            **inverse,
            "type": None,
            "reason": None,
            "start_time": None,
            "end_time": None,
            "source": None,
            "pending_request_id": None,
        }
        await journal(
            op_kind="set_staff_off",
            label=f"{staff_name or '職員'}さんの{_date_label(day)}の{leave_type_label(leave_type)}を取り消し",
            forward_payload=forward,
            inverse_payload=inverse,
        )
    if notify:
        await notify_leave_cancelled(
            db, staff_id=staff_id, target=day, type_label=leave_type_label(leave_type)
        )
    await db.flush()
    return leave_type


async def write_override_day(
    db: AsyncSession,
    *,
    staff_id: UUID,
    day: date,
    override_type: str | None,
    start_time: time | None,
    end_time: time | None,
    reason: str | None,
    actor: User | None,
    origin: str,
) -> LeaveApplyResult | None:
    """1 日ぶんの休み・時間変更を「あるべき姿」にそろえる (職員 Excel の取込・全置換・§4-3).

    * 同じ種類・同じ時刻の行があれば種類は触らない (``source``・``pending_request_id`` を
      残す。理由だけ違えば理由だけ書く)。
    * 休みの種類へ変わる・増える → ``apply_staff_leave(source='admin', notify=False)``
      (今日以降なら休みの時間帯の訪問を担当なしへ。通知は呼び出し側がまとめて 1 通)。
    * 消える → 休みなら ``cancel_staff_leave(notify=False)``、時間変更なら行を消す。
    * 時間変更 → ``set_staff_override`` で行だけ。

    Returns:
        休みを入れた・変えたときの結果 (通知の材料)。それ以外は None。
    """
    row = await _load_override(db, staff_id, day)
    y, w, wd = iso_key(day)
    if override_type is None:
        if row is None:
            return None
        if row.override_type in LEAVE_TYPES:
            await cancel_staff_leave(
                db,
                staff_id=staff_id,
                day=day,
                actor=actor,
                op_group_id=None,
                origin=origin,
                notify=False,
            )
        else:
            await set_staff_override(
                db, staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=None
            )
        return None
    if (
        row is not None
        and row.override_type == override_type
        and row.start_time == start_time
        and row.end_time == end_time
    ):
        if (row.reason or None) != (reason or None):
            await set_staff_override(
                db,
                staff_id=staff_id,
                iso_year=y,
                iso_week=w,
                weekday=wd,
                override_type=override_type,
                reason=reason,
                start_time=fmt_time(start_time) if start_time else None,
                end_time=fmt_time(end_time) if end_time else None,
                source=row.source,
                pending_request_id=row.pending_request_id,
            )
        return None
    if override_type in LEAVE_TYPES:
        return await apply_staff_leave(
            db,
            staff_id=staff_id,
            day=day,
            leave_type=override_type,
            source="admin",
            note=reason,
            actor=actor,
            op_group_id=None,
            origin=origin,
            notify=False,
        )
    if row is not None and row.override_type in LEAVE_TYPES:
        # 休み → 時間変更: 先に休みを取り消す (取り消しの記録・last_leave_changed_at)。
        await cancel_staff_leave(
            db,
            staff_id=staff_id,
            day=day,
            actor=actor,
            op_group_id=None,
            origin=origin,
            notify=False,
        )
    await set_staff_override(
        db,
        staff_id=staff_id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type=override_type,
        reason=reason,
        start_time=fmt_time(start_time) if start_time else None,
        end_time=fmt_time(end_time) if end_time else None,
    )
    return None


async def notify_leave_results(db: AsyncSession, results: Iterable[LeaveApplyResult | None]) -> int:
    """``notify=False`` で入れた休みの結果から、管理者への通知を 1 通にまとめて出す."""
    staff_ids = {r.staff_id for r in results if r is not None and r.moved_visit_ids}
    if not staff_ids:
        return 0
    name_rows = await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
    names: dict[UUID, str] = {sid: name for sid, name in name_rows.all()}
    notices = [
        UnassignedNotice(
            staff_name=names.get(r.staff_id) or "職員",
            day=r.day,
            patient_count=r.moved_patient_count,
            visit_count=len(r.moved_visit_ids),
            accompaniment_count=r.accompaniment_count,
        )
        for r in results
        if r is not None and r.moved_visit_ids
    ]
    return await notify_leave_unassigned(db, notices)


async def count_leave_moves(db: AsyncSession, entries: Iterable[tuple[UUID, date, str]]) -> int:
    """休みを入れたら担当なしへ移る訪問の数 (取込のプレビュー「N 件を担当なしへ移します」).

    ``entries`` は (職員, 日, 休みの種類)。今日 (JST) より前の日は記録だけなので数えない。
    今ある休みと同じ種類の日は ``write_override_day`` が触らないので数えない
    (プレビューの件数と実際の件数をそろえる)。
    """
    today = today_jst()
    wanted = {(sid, day, t) for sid, day, t in entries if day >= today and t in LEAVE_TYPES}
    if not wanted:
        return 0
    days = sorted({d for _s, d, _t in wanted})
    current = await load_overrides_between(db, days[0], days[-1], {s for s, _d, _t in wanted})
    loaded: dict[date, _DayData] = {}
    total = 0
    for sid, day, leave_type in sorted(wanted, key=lambda e: (e[1], str(e[0]))):
        row = current.get(sid, {}).get(day)
        if row is not None and row.override_type == leave_type:
            continue
        if day not in loaded:
            loaded[day] = await _load_day(db, day)
        targets, _skipped = await _owned_hit_visits(db, sid, day, leave_type, loaded[day])
        total += len(targets)
    return total


# ---------------------------------------------------------------------------
# 後始末と検知 (§4-2)
# ---------------------------------------------------------------------------


def _expand_dates(dates: Iterable[date] | None, iso_week: tuple[int, int] | None) -> list[date]:
    out = set(dates or ())
    if iso_week is not None:
        monday = date.fromisocalendar(iso_week[0], iso_week[1], 1)
        out.update(monday + timedelta(days=i) for i in range(7))
    return sorted(out)


async def _leave_entries(
    db: AsyncSession, days: list[date], staff_ids: Iterable[UUID] | None
) -> list[tuple[UUID, date, str]]:
    """その日々の休みの登録 (職員, 日, 種類) を日付・職員の順で."""
    if not days:
        return []
    by_staff = await load_overrides_between(db, days[0], days[-1], staff_ids)
    wanted = set(days)
    out = [
        (sid, d, row.override_type)
        for sid, per_day in by_staff.items()
        for d, row in per_day.items()
        if d in wanted and row.override_type in LEAVE_TYPES
    ]
    out.sort(key=lambda e: (e[1], str(e[0])))
    return out


async def settle_leave_unassign(
    db: AsyncSession,
    *,
    dates: Iterable[date] | None = None,
    iso_week: tuple[int, int] | None = None,
    staff_ids: Iterable[UUID] | None = None,
    journal: Journal | None = None,
    notify: bool = True,
) -> int:
    """訪問を作る・担当を書く処理の最後に呼ぶ共通の後始末 (§4-2・commit しない).

    受けた範囲 (日の集合・ISO 週 ``(iso_year, iso_week)``・職員) の **今日 (JST) 以降の日**
    だけを見て、休みの登録がある職員の、休みの時間帯にかかる本人の planned の訪問を
    担当なしにする (付け方は §3-2)。op_log を書く経路は ``journal`` を渡す。

    Returns:
        担当なしにした訪問の件数 (1 件以上なら管理者へ通知)。
    """
    today = today_jst()
    days = [d for d in _expand_dates(dates, iso_week) if d >= today]
    entries = await _leave_entries(db, days, list(staff_ids) if staff_ids is not None else None)
    if not entries:
        return 0
    name_rows = await db.execute(
        select(Staff.id, Staff.name).where(Staff.id.in_({sid for sid, _d, _t in entries}))
    )
    names: dict[UUID, str] = {sid: name for sid, name in name_rows.all()}
    total = 0
    notices: list[UnassignedNotice] = []
    loaded: dict[date, _DayData] = {}
    for sid, day, leave_type in entries:
        if day not in loaded:
            loaded[day] = await _load_day(db, day)
        targets, _skipped = await _owned_hit_visits(db, sid, day, leave_type, loaded[day])
        moved = await _unassign_day(
            db,
            staff_id=sid,
            day=day,
            leave_type=leave_type,
            targets=targets,
            to_staff_id=None,
            journal=journal,
            to_label="担当なし",
            day_data=loaded[day],
        )
        if moved.moved_visit_ids:
            total += len(moved.moved_visit_ids)
            notices.append(
                UnassignedNotice(
                    staff_name=names.get(sid) or "職員",
                    day=day,
                    patient_count=len(moved.moved_patient_ids),
                    visit_count=len(moved.moved_visit_ids),
                )
            )
    if notify and notices:
        await notify_leave_unassigned(db, notices)
    await db.flush()
    return total


async def find_visits_on_leave(
    db: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    staff_ids: Iterable[UUID] | None = None,
) -> list[VisitOnLeave]:
    """今日 (JST) 以降で、休みの時間帯に本人が担当 (§3-3 の判定) の planned の訪問."""
    start = max(date_from, today_jst())
    if start > date_to:
        return []
    days = [start + timedelta(days=i) for i in range((date_to - start).days + 1)]
    entries = await _leave_entries(db, days, list(staff_ids) if staff_ids is not None else None)
    out: list[VisitOnLeave] = []
    loaded: dict[date, _DayData] = {}
    for sid, day, leave_type in entries:
        if day not in loaded:
            loaded[day] = await _load_day(db, day)
        targets, _skipped = await _owned_hit_visits(db, sid, day, leave_type, loaded[day])
        out.extend(
            VisitOnLeave(
                visit_id=v.id,
                staff_id=sid,
                visit_date=day,
                start_time=v.start_time,
                end_time=v.end_time,
                patient_id=v.patient_id,
                leave_type=leave_type,
            )
            for v, _p, _c in targets
        )
    return out


@dataclass(frozen=True)
class LeaveConflict:
    """取込の計画で、休む人の休みの時間帯に担当が付く訪問 1 件 (取込のプレビューの件数)."""

    staff_id: UUID
    staff_name: str
    visit_date: date
    start_time: time
    end_time: time
    leave_type: str


#: (職員, 日, 開始, 終了) — 取込の計画で担当が付く訪問の枠。
LeaveSlot = tuple[UUID, date, time, time]


async def collect_leave_conflicts(
    db: AsyncSession, slots: Iterable[LeaveSlot]
) -> list[LeaveConflict]:
    """取込の計画 (カイポケが正・後始末を呼ばない) のうち、休みの時間帯に担当が付く枠.

    カイポケの取込・置換は休む人に担当を戻すことがある (休み設計 §4-2 H-b)。プレビューで
    「休みの時間帯に担当が付く訪問 N 件」を出すための材料。今日 (JST) 以降の日だけ。
    同じ (職員, 日, 開始) は 1 件にまとめ、日付・開始・職員名の順に並べる。
    """
    today = today_jst()
    wanted = {(sid, d, st, en) for sid, d, st, en in slots if d >= today}
    if not wanted:
        return []
    days = sorted({d for _sid, d, _st, _en in wanted})
    staff_ids = {sid for sid, _d, _st, _en in wanted}
    leaves = await load_overrides_between(db, days[0], days[-1], staff_ids)
    name_rows = await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
    names: dict[UUID, str] = {sid: name for sid, name in name_rows.all()}
    hits: dict[tuple[UUID, date, time], LeaveConflict] = {}
    for sid, d, st, en in wanted:
        row = leaves.get(sid, {}).get(d)
        if row is None or not visit_hits_leave(row.override_type, st, en):
            continue
        hits.setdefault(
            (sid, d, st),
            LeaveConflict(
                staff_id=sid,
                staff_name=names.get(sid) or "職員",
                visit_date=d,
                start_time=st,
                end_time=en,
                leave_type=row.override_type,
            ),
        )
    return sorted(hits.values(), key=lambda c: (c.visit_date, c.start_time, c.staff_name))


async def count_visits_on_leave(db: AsyncSession, date_from: date, date_to: date) -> int:
    """``find_visits_on_leave`` の件数 (「休みの時間帯に担当が付いている訪問 N 件」)."""
    return len(await find_visits_on_leave(db, date_from, date_to))


__all__ = [
    "LEAVE_ORIGINS",
    "LEAVE_WRITE_CONFLICT_DETAIL",
    "LEAVE_SOURCES",
    "DayUnassignResult",
    "Journal",
    "LeaveApplyResult",
    "LeaveConflict",
    "LeaveSlot",
    "StaffLeaveError",
    "VisitOnLeave",
    "apply_staff_leave",
    "cancel_staff_leave",
    "collect_leave_conflicts",
    "count_leave_moves",
    "count_visits_on_leave",
    "find_visits_on_leave",
    "is_past_leave_deadline",
    "leave_request_deadline",
    "notify_leave_results",
    "settle_leave_unassign",
    "today_jst",
    "write_override_day",
]
