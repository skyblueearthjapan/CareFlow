"""事務所からのやること — 出す訪問の決め方・訪問ごとの一括計算・通知.

正典 = ``docs/plans/patient-tasks-design-2026-11-01.md`` §2 (PO 決定 2026-11-01)。

出す訪問は保存しない。ここの関数で見るたびに決める:

* **出す訪問** (:func:`first_visit_on_or_after`) = その患者の、ある日以降の、取消・削除で
  ない最初の訪問 (日付 → 開始時刻 → id の順)。一覧・作成・通知では「ある日」を
  ``max(日付, 今日)`` にする (日付が過ぎた未完了 = 持ち越しは今日以降の訪問に出す)。
* **訪問に出るやること** (:func:`tasks_for_visits`):
    - 未完了: 患者が同じで、日付 (この日から) ≦ 訪問日。日付以降の最初の訪問より後の訪問
      では ``carried_over`` (持ち越し) を立てる。ただし今日より後の訪問には
      ``max(日付, 今日)`` 以降の最初の訪問 (= 一覧の「出す訪問」) にだけ出す。
    - 完了: 完了した訪問 (``done_visit_id``) にだけ出る。訪問を指さずに完了したもの
      (管理者が PC から) は、完了した日 (JST) の訪問に出る。
    - 取消: 出ない。
  訪問一覧から呼んでも N+1 にならないよう、やること・訪問・担当をまとめて引く。
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.patient_task import PatientTask, TaskItemType
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.accompaniment import resolve_accompaniment_by_visit

JST: Final = ZoneInfo("Asia/Tokyo")

#: やることを付けたときの通知 (担当の職員向け)。
NOTIFY_TASK_ASSIGNED: Final = "task_assigned"
#: 通知の reference_type (reference_id = 作ったやることの最初の 1 件の id)。
REFERENCE_PATIENT_TASK: Final = "patient_task"

_WEEKDAYS_JP: Final = ("月", "火", "水", "木", "金", "土", "日")


def jst_today() -> date:
    return datetime.now(JST).date()


def jst_date(dt: datetime) -> date:
    """保存された時刻 → JST の日付 (SQLite はタイムゾーン無しで返すので UTC とみなす)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).date()


def fmt_hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _visit_key(v: Visit) -> tuple[date, time, str]:
    return (v.visit_date, v.start_time, str(v.id))


def eligible_visit_clause():
    """出す訪問になれる訪問 (削除されていない・取消でない)."""
    return and_(Visit.deleted_at.is_(None), Visit.status != VISIT_STATUS_CANCELLED)


def task_status(task: PatientTask) -> str:
    if task.cancelled_at is not None:
        return "cancelled"
    if task.done_at is not None:
        return "done"
    return "open"


def overdue_clause(today: date):
    """期日を過ぎた = 未完了・取消でなく、日付以降の出す訪問の日がもう過ぎた (今日より前).

    日付 (この日から) が過ぎただけでは出さない。その日以降の最初の訪問が終わっても
    未完了のとき (= 持ち越し) だけ。訪問がまだ無い・先の訪問しか無いものは出さない。
    日の単位で見る (PO 2026-11-04)。同じ日に訪問が 2 回あり朝の分で未完了のとき、
    午後の訪問には持ち越し (carried_over) が立つが、ここはその日が終わるまで立てない。
    """
    passed_visit = (
        select(Visit.id)
        .where(
            Visit.patient_id == PatientTask.patient_id,
            Visit.visit_date >= PatientTask.start_date,
            Visit.visit_date < today,
            eligible_visit_clause(),
        )
        .exists()
    )
    return and_(PatientTask.done_at.is_(None), PatientTask.cancelled_at.is_(None), passed_visit)


async def overdue_task_ids(db: AsyncSession, tasks: list[PatientTask], today: date) -> set[UUID]:
    """渡したやることのうち、期日を過ぎたものの id (問い合わせ 1 回)."""
    ids = [t.id for t in tasks if task_status(t) == "open"]
    if not ids:
        return set()
    return set(
        (
            await db.scalars(
                select(PatientTask.id).where(PatientTask.id.in_(ids), overdue_clause(today))
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 訪問の読み込みと「出す訪問」
# ---------------------------------------------------------------------------


async def load_patient_visits(
    db: AsyncSession,
    patient_ids: set[UUID],
    date_from: date,
    date_to: date | None = None,
) -> dict[UUID, list[Visit]]:
    """患者ごとの出せる訪問 (``date_from`` 以降・並びは日付 → 開始時刻 → id)."""
    if not patient_ids:
        return {}
    stmt = select(Visit).where(
        Visit.patient_id.in_(patient_ids),
        Visit.visit_date >= date_from,
        eligible_visit_clause(),
    )
    if date_to is not None:
        stmt = stmt.where(Visit.visit_date <= date_to)
    out: dict[UUID, list[Visit]] = defaultdict(list)
    for v in (await db.scalars(stmt)).all():
        out[v.patient_id].append(v)
    for vs in out.values():
        vs.sort(key=_visit_key)
    return out


def first_visit_on_or_after(visits: list[Visit], d: date) -> Visit | None:
    """並べた訪問のうち、``d`` 以降の最初の訪問 (無ければ None)."""
    dates = [v.visit_date for v in visits]
    i = bisect.bisect_left(dates, d)
    return visits[i] if i < len(visits) else None


async def next_visits_for_tasks(
    db: AsyncSession, tasks: list[PatientTask], today: date
) -> dict[UUID, Visit]:
    """未完了のやること id → 出す訪問 (今日以降・日付以降の最初の訪問)."""
    open_tasks = [t for t in tasks if task_status(t) == "open"]
    if not open_tasks:
        return {}
    by_patient = await load_patient_visits(db, {t.patient_id for t in open_tasks}, today)
    out: dict[UUID, Visit] = {}
    for t in open_tasks:
        v = first_visit_on_or_after(by_patient.get(t.patient_id, []), max(t.start_date, today))
        if v is not None:
            out[t.id] = v
    return out


# ---------------------------------------------------------------------------
# 訪問の担当 (主・2 人目・同行・コース担当)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffRef:
    staff_id: UUID
    name: str | None
    role: str  # primary / secondary / accompaniment

    def to_payload(self) -> dict:
        return {"staff_id": self.staff_id, "name": self.name, "role": self.role}


async def visit_staff_map(db: AsyncSession, visits: list[Visit]) -> dict[UUID, list[StaffRef]]:
    """訪問 id → 担当 (主担当・2 人目・指導役・VSA・コース担当・同行) をまとめて解決する.

    コース担当は ``visits.py`` の可視性 (``_course_fallback_condition``) と同じ規則:
    主担当が空・手動差替えでない・コース担当が在籍中のときだけ主担当として扱う。
    """
    if not visits:
        return {}
    visit_ids = [v.id for v in visits]
    roles: dict[UUID, dict[UUID, str]] = {v.id: {} for v in visits}

    def _add(vid: UUID, sid: UUID | None, role: str) -> None:
        if sid is not None and sid not in roles[vid]:
            roles[vid][sid] = role

    for v in visits:
        _add(v.id, v.primary_staff_id, "primary")
        _add(v.id, v.secondary_staff_id, "secondary")
        # 見える範囲 (``_staff_visibility_filter``) と同じく指導役も担当に含める。
        _add(v.id, v.mentor_staff_id, "accompaniment")

    asn_rows = (
        await db.execute(
            select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                VisitStaffAssignment.visit_id.in_(visit_ids)
            )
        )
    ).all()
    for vid, sid in asn_rows:
        _add(vid, sid, "primary" if not roles[vid] else "secondary")

    fallback_courses = {
        v.course_id
        for v in visits
        if v.primary_staff_id is None and not v.manual_staff_override and v.course_id is not None
    }
    if fallback_courses:
        course_rows = (
            await db.execute(
                select(Course.id, Course.assigned_staff_id)
                .join(Staff, Staff.id == Course.assigned_staff_id)
                .where(
                    Course.id.in_(fallback_courses),
                    Course.deleted_at.is_(None),
                    Course.assigned_staff_id.is_not(None),
                    Staff.deleted_at.is_(None),
                    Staff.status == "active",
                )
            )
        ).all()
        course_staff = dict(course_rows)
        for v in visits:
            if (
                v.primary_staff_id is None
                and not v.manual_staff_override
                and v.course_id in course_staff
            ):
                _add(v.id, course_staff[v.course_id], "primary")

    acc = await resolve_accompaniment_by_visit(db, visits)
    for vid, entries in acc.items():
        for e in entries:
            _add(vid, e.staff_id, "accompaniment")

    staff_ids = {sid for m in roles.values() for sid in m}
    names: dict[UUID, str] = {}
    if staff_ids:
        names = dict(
            (await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))).all()
        )
    return {
        vid: [StaffRef(sid, names.get(sid), role) for sid, role in m.items()]
        for vid, m in roles.items()
    }


def visit_brief(v: Visit, staff: list[StaffRef]) -> dict:
    return {
        "visit_id": v.id,
        "visit_date": v.visit_date,
        "start_time": fmt_hhmm(v.start_time),
        "end_time": fmt_hhmm(v.end_time),
        "staff": [s.to_payload() for s in staff],
    }


# ---------------------------------------------------------------------------
# 訪問ごとの「出るやること」
# ---------------------------------------------------------------------------


@dataclass
class VisitTaskEntry:
    task: PatientTask
    carried_over: bool


def _done_shows_on(task: PatientTask, v: Visit) -> bool:
    if task.done_visit_id is not None:
        return task.done_visit_id == v.id
    assert task.done_at is not None
    return task.start_date <= v.visit_date and jst_date(task.done_at) == v.visit_date


async def tasks_for_visits(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, list[VisitTaskEntry]]:
    """訪問 id → その訪問に出るやること (取消・削除の訪問には出さない).

    クエリはやること 1 回・訪問 1 回 (持ち越しの判定用) で済ませる。
    """
    targets = [v for v in visits if v.deleted_at is None and v.status != VISIT_STATUS_CANCELLED]
    if not targets:
        return {}
    patient_ids = {v.patient_id for v in targets}
    visit_ids = {v.id for v in targets}
    min_d = min(v.visit_date for v in targets)
    max_d = max(v.visit_date for v in targets)
    # 完了した時刻は UTC で持つので、JST の日付の前後 1 日まで広く取って Python で絞る。
    lo = datetime.combine(min_d - timedelta(days=1), time.min, tzinfo=UTC)
    hi = datetime.combine(max_d + timedelta(days=2), time.min, tzinfo=UTC)
    tasks = (
        await db.scalars(
            select(PatientTask).where(
                PatientTask.patient_id.in_(patient_ids),
                PatientTask.cancelled_at.is_(None),
                PatientTask.start_date <= max_d,
                or_(
                    PatientTask.done_at.is_(None),
                    PatientTask.done_visit_id.in_(visit_ids),
                    and_(
                        PatientTask.done_visit_id.is_(None),
                        PatientTask.done_at >= lo,
                        PatientTask.done_at < hi,
                    ),
                ),
            )
        )
    ).all()
    if not tasks:
        return {}

    today = jst_today()
    open_tasks = [t for t in tasks if t.done_at is None]
    first_by_task: dict[UUID, tuple[date, time, str]] = {}
    # 今日より後の訪問には、max(日付, 今日) 以降の最初の訪問にだけ出す
    # (まだ来ていない先の訪問へ持ち越しで重ねて出さない)。
    next_by_task: dict[UUID, UUID] = {}
    if open_tasks:
        by_patient = await load_patient_visits(
            db,
            {t.patient_id for t in open_tasks},
            min(t.start_date for t in open_tasks),
            max_d,
        )
        for t in open_tasks:
            pvs = by_patient.get(t.patient_id, [])
            fv = first_visit_on_or_after(pvs, t.start_date)
            if fv is not None:
                first_by_task[t.id] = _visit_key(fv)
            nv = first_visit_on_or_after(pvs, max(t.start_date, today))
            if nv is not None:
                next_by_task[t.id] = nv.id

    tasks_by_patient: dict[UUID, list[PatientTask]] = defaultdict(list)
    for t in tasks:
        tasks_by_patient[t.patient_id].append(t)

    out: dict[UUID, list[VisitTaskEntry]] = {}
    for v in targets:
        entries: list[VisitTaskEntry] = []
        for t in tasks_by_patient.get(v.patient_id, []):
            if t.done_at is None:
                if v.visit_date > today and next_by_task.get(t.id) != v.id:
                    continue
                if t.start_date <= v.visit_date:
                    first = first_by_task.get(t.id)
                    entries.append(VisitTaskEntry(t, first is not None and first < _visit_key(v)))
            elif _done_shows_on(t, v):
                entries.append(VisitTaskEntry(t, False))
        if entries:
            out[v.id] = entries
    return out


# ---------------------------------------------------------------------------
# 読み出し用の名前
# ---------------------------------------------------------------------------


@dataclass
class TaskNames:
    patients: dict[UUID, str]
    item_types: dict[UUID, tuple[str, int]]
    users: dict[UUID, str]
    staff: dict[UUID, str]


async def load_task_names(db: AsyncSession, tasks: list[PatientTask]) -> TaskNames:
    patient_ids = {t.patient_id for t in tasks}
    item_ids = {t.item_type_id for t in tasks if t.item_type_id is not None}
    user_ids = {uid for t in tasks for uid in (t.created_by, t.done_by_user_id) if uid is not None}
    staff_ids = {t.done_by_staff_id for t in tasks if t.done_by_staff_id is not None}

    patients: dict[UUID, str] = {}
    if patient_ids:
        patients = dict(
            (await db.execute(select(Patient.id, Patient.name).where(Patient.id.in_(patient_ids))))
            .tuples()
            .all()
        )
    item_types: dict[UUID, tuple[str, int]] = {}
    if item_ids:
        for iid, name, order in (
            await db.execute(
                select(TaskItemType.id, TaskItemType.name, TaskItemType.sort_order).where(
                    TaskItemType.id.in_(item_ids)
                )
            )
        ).all():
            item_types[iid] = (name, order)
    users: dict[UUID, str] = {}
    if user_ids:
        for uid, display, username, email, sname in (
            await db.execute(
                select(User.id, User.display_name, User.username, User.email, Staff.name)
                .outerjoin(Staff, Staff.id == User.staff_id)
                .where(User.id.in_(user_ids))
            )
        ).all():
            users[uid] = display or sname or username or email or ""
    staff: dict[UUID, str] = {}
    if staff_ids:
        staff = dict(
            (await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids))))
            .tuples()
            .all()
        )
    return TaskNames(patients=patients, item_types=item_types, users=users, staff=staff)


def task_sort_key(task: PatientTask, names: TaskNames) -> tuple:
    """項目 (並び順) → 伝言、同じなら作った順."""
    item = names.item_types.get(task.item_type_id) if task.item_type_id else None
    return (
        task.item_type_id is None,
        item[1] if item else 0,
        task.created_at or datetime.min.replace(tzinfo=UTC),
        str(task.id),
    )


def serialize_task(task: PatientTask, names: TaskNames, *, overdue: bool) -> dict:
    item = names.item_types.get(task.item_type_id) if task.item_type_id else None
    done_by_name = None
    if task.done_by_staff_id is not None:
        done_by_name = names.staff.get(task.done_by_staff_id)
    if done_by_name is None and task.done_by_user_id is not None:
        done_by_name = names.users.get(task.done_by_user_id)
    return {
        "id": task.id,
        "patient_id": task.patient_id,
        "patient_name": names.patients.get(task.patient_id),
        "start_date": task.start_date,
        "item_type_id": task.item_type_id,
        "item_type_name": item[0] if item else None,
        "is_message": task.item_type_id is None,
        "memo": task.memo,
        "created_by": task.created_by,
        "created_by_name": names.users.get(task.created_by) if task.created_by else None,
        "created_at": task.created_at,
        "status": task_status(task),
        "overdue": overdue,
        "done_at": task.done_at,
        "done_by_user_id": task.done_by_user_id,
        "done_by_staff_id": task.done_by_staff_id,
        "done_by_name": done_by_name,
        "done_visit_id": task.done_visit_id,
        "cancelled_at": task.cancelled_at,
        "recurring_rule_id": task.recurring_rule_id,
        "recurring_month": task.recurring_month,
    }


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------


def _fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}（{_WEEKDAYS_JP[d.weekday()]}）"


async def notify_task_assigned(
    db: AsyncSession,
    *,
    tasks: list[PatientTask],
    visit: Visit | None,
    staff: list[StaffRef],
    patient_name: str | None,
    item_names: list[str],
) -> int:
    """付けたやることを、その時点で出す訪問の担当 (アカウントのある職員) へ知らせる.

    1 回の登録の中で 1 人 1 通 (項目が複数でもまとめる・担当の役が重なっても 1 通)。
    reference は最初のやることで、同じ ``tasks`` でもう一度呼んだときだけ 2 通目を作らない。
    登録をもう一度送ると別のやることになり通知も別に出るので、二度押しは画面側で防ぐ前提。
    **commit しない** (呼び出し側のトランザクションに乗せる)。
    """
    if visit is None or not tasks or not staff:
        return 0
    staff_ids = {s.staff_id for s in staff}
    users = (
        await db.scalars(
            select(User).where(User.staff_id.in_(staff_ids), User.deleted_at.is_(None))
        )
    ).all()
    if not users:
        return 0
    ref_id = tasks[0].id
    already = set(
        (
            await db.scalars(
                select(Notification.user_id).where(
                    Notification.user_id.in_([u.id for u in users]),
                    Notification.reference_type == REFERENCE_PATIENT_TASK,
                    Notification.reference_id == ref_id,
                )
            )
        ).all()
    )
    what = "・".join(item_names) if item_names else "伝言"
    who = f"{patient_name} 様" if patient_name else "患者様"
    when = f"{_fmt_md(visit.visit_date)} {fmt_hhmm(visit.start_time)}"
    count = 0
    for u in users:
        if u.id in already:
            continue
        db.add(
            Notification(
                user_id=u.id,
                type=NOTIFY_TASK_ASSIGNED,
                title="事務所からのやることが届きました",
                body=f"{who}（{when} の訪問）: {what}",
                reference_type=REFERENCE_PATIENT_TASK,
                reference_id=ref_id,
                data={
                    "patient_id": str(tasks[0].patient_id),
                    "visit_id": str(visit.id),
                    "visit_date": visit.visit_date.isoformat(),
                    "task_ids": [str(t.id) for t in tasks],
                },
            )
        )
        count += 1
    return count
