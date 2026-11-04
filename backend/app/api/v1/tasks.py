"""事務所からのやること (看護師へのタスク) endpoints.

正典 = ``docs/plans/patient-tasks-design-2026-11-01.md`` (PO 決定は §2)。

    GET   /api/v1/task-item-types               — 全員 (項目の一覧。include_inactive は管理者だけ効く)
    POST  /api/v1/task-item-types               — 管理者
    PATCH /api/v1/task-item-types/{id}          — 管理者 (名前・並び順・使う/使わない)
    GET   /api/v1/tasks                         — 管理者 (状態・看護師・患者・日付で絞る)
    POST  /api/v1/tasks                         — 管理者 (項目ごとに 1 件・担当へ通知)
    GET   /api/v1/tasks/counts?date=YYYY-MM-DD  — 管理者・職員 (職員は自分の訪問だけ)
    POST  /api/v1/tasks/{id}/complete           — 管理者・その訪問の担当の職員
    POST  /api/v1/tasks/{id}/reopen             — 管理者・自分で完了にした職員
    POST  /api/v1/tasks/{id}/cancel             — 管理者
    GET   /api/v1/visits/{visit_id}/tasks       — 管理者・その訪問の担当の職員

職員の「自分の訪問」は ``visits.py`` の一覧と同じ規則 (主・2 人目・VSA・同行・コース担当)。
出す訪問の決め方は ``app.services.patient_tasks`` の 1 か所にある。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update

from app.api.v1.visits import _staff_visibility_filter
from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient import Patient
from app.models.patient_task import PatientTask, TaskItemType
from app.models.user import User, normalize_user_role
from app.models.visit import Visit
from app.schemas.patient_task import (
    PatientTaskComplete,
    PatientTaskCreate,
    PatientTaskCreateResult,
    PatientTaskRead,
    TaskItemTypeCreate,
    TaskItemTypeRead,
    TaskItemTypeUpdate,
    TaskListStatus,
    VisitTaskCounts,
)
from app.services.patient_tasks import (
    JST,
    eligible_visit_clause,
    jst_today,
    load_task_names,
    next_visits_for_tasks,
    notify_task_assigned,
    overdue_clause,
    overdue_task_ids,
    serialize_task,
    task_sort_key,
    task_status,
    tasks_for_visits,
    visit_brief,
    visit_staff_map,
)

router = APIRouter()

AdminUser = Annotated[User, Depends(require_role("admin"))]


def _is_admin(user: User) -> bool:
    return normalize_user_role(user.role) == "admin"


def _ensure_admin_or_staff(user: User) -> None:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


def _can_reopen(task: PatientTask, user: User) -> bool:
    if task_status(task) != "done":
        return False
    return _is_admin(user) or task.done_by_user_id == user.id


def _item_read(row: TaskItemType) -> TaskItemTypeRead:
    return TaskItemTypeRead(
        id=row.id, name=row.name, sort_order=row.sort_order, is_active=row.is_active
    )


async def _staff_sees_visit(db, staff_id: UUID | None, visit_id: UUID) -> bool:
    if staff_id is None:
        return False
    found = await db.scalar(
        select(Visit.id).where(Visit.id == visit_id, _staff_visibility_filter(staff_id))
    )
    return found is not None


async def _get_task(db, task_id: UUID) -> PatientTask:
    task = await db.scalar(select(PatientTask).where(PatientTask.id == task_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return task


async def _read_one(db, task: PatientTask, user: User) -> dict:
    names = await load_task_names(db, [task])
    overdue = task.id in await overdue_task_ids(db, [task], jst_today())
    return {
        **serialize_task(task, names, overdue=overdue),
        "can_reopen": _can_reopen(task, user),
    }


# ---------------------------------------------------------------------------
# 項目
# ---------------------------------------------------------------------------


@router.get("/task-item-types", response_model=list[TaskItemTypeRead])
async def list_task_item_types(
    db: DbDep,
    user: CurrentActiveUser,
    include_inactive: bool = False,
) -> list[TaskItemTypeRead]:
    _ensure_admin_or_staff(user)
    stmt = select(TaskItemType)
    if not (include_inactive and _is_admin(user)):
        stmt = stmt.where(TaskItemType.is_active.is_(True))
    rows = (await db.scalars(stmt.order_by(TaskItemType.sort_order, TaskItemType.name))).all()
    return [_item_read(r) for r in rows]


async def _ensure_name_free(db, name: str, exclude_id: UUID | None = None) -> None:
    stmt = select(TaskItemType.id).where(TaskItemType.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TaskItemType.id != exclude_id)
    if await db.scalar(stmt.limit(1)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同じ名前の項目があります")


@router.post(
    "/task-item-types", response_model=TaskItemTypeRead, status_code=status.HTTP_201_CREATED
)
async def create_task_item_type(
    payload: TaskItemTypeCreate, db: DbDep, user: AdminUser
) -> TaskItemTypeRead:
    await _ensure_name_free(db, payload.name)
    sort_order = payload.sort_order
    if sort_order is None:
        current_max = await db.scalar(select(func.max(TaskItemType.sort_order)))
        sort_order = (current_max + 1) if current_max is not None else 0
    now = datetime.now(UTC)
    row = TaskItemType(
        name=payload.name,
        sort_order=sort_order,
        is_active=True,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    return _item_read(row)


@router.patch("/task-item-types/{item_type_id}", response_model=TaskItemTypeRead)
async def update_task_item_type(
    item_type_id: UUID, payload: TaskItemTypeUpdate, db: DbDep, _user: AdminUser
) -> TaskItemTypeRead:
    row = await db.scalar(select(TaskItemType).where(TaskItemType.id == item_type_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if payload.name is not None and payload.name != row.name:
        await _ensure_name_free(db, payload.name, exclude_id=row.id)
        row.name = payload.name
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_active is not None:
        row.is_active = payload.is_active
    row.updated_at = datetime.now(UTC)
    await db.commit()
    return _item_read(row)


# ---------------------------------------------------------------------------
# やること
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[PatientTaskRead])
async def list_tasks(
    db: DbDep,
    user: AdminUser,
    status_: Annotated[TaskListStatus, Query(alias="status")] = "open",
    staff_id: Annotated[
        UUID | None,
        Query(description="看護師: 出す訪問 (完了は完了した訪問・完了した人) の担当で絞る"),
    ] = None,
    patient_id: UUID | None = None,
    date_from: Annotated[date | None, Query(description="日付 (この日から) の下限")] = None,
    date_to: Annotated[date | None, Query(description="日付 (この日から) の上限")] = None,
    done_from: Annotated[
        date | None, Query(description="完了した日 (JST) の下限。完了していないものは外れる")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[dict]:
    today = jst_today()
    stmt = (
        select(PatientTask)
        .join(Patient, Patient.id == PatientTask.patient_id)
        .where(PatientTask.cancelled_at.is_(None), Patient.deleted_at.is_(None))
    )
    if status_ == "open":
        stmt = stmt.where(PatientTask.done_at.is_(None))
    elif status_ == "overdue":
        stmt = stmt.where(overdue_clause(today))
    elif status_ == "done":
        stmt = stmt.where(PatientTask.done_at.is_not(None))
    if patient_id is not None:
        stmt = stmt.where(PatientTask.patient_id == patient_id)
    if date_from is not None:
        stmt = stmt.where(PatientTask.start_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(PatientTask.start_date <= date_to)
    if done_from is not None:
        # 完了した時刻は UTC で持つので、JST のその日の 0 時を UTC にして比べる。
        stmt = stmt.where(
            PatientTask.done_at >= datetime.combine(done_from, time.min, tzinfo=JST).astimezone(UTC)
        )
    if staff_id is not None:
        # 完了は「完了した訪問の担当か完了した人」なので DB で先に絞れる。
        # 未完了は出す訪問 (見るたびに決まる) の担当で決まるので、下で絞る。
        stmt = stmt.where(
            or_(
                PatientTask.done_at.is_(None),
                PatientTask.done_by_staff_id == staff_id,
                PatientTask.done_visit_id.in_(
                    select(Visit.id).where(_staff_visibility_filter(staff_id))
                ),
            )
        )
    stmt = stmt.order_by(PatientTask.start_date, PatientTask.created_at, PatientTask.id)
    if staff_id is None or status_ == "done":
        stmt = stmt.limit(limit)
    tasks = list((await db.scalars(stmt)).all())

    next_by_task = await next_visits_for_tasks(db, tasks, today)
    done_visit_ids = {t.done_visit_id for t in tasks if t.done_visit_id is not None}
    done_visits: dict[UUID, Visit] = {}
    if done_visit_ids:
        done_visits = {
            v.id: v
            for v in (await db.scalars(select(Visit).where(Visit.id.in_(done_visit_ids)))).all()
        }
    all_visits = {v.id: v for v in [*next_by_task.values(), *done_visits.values()]}
    staff_by_visit = await visit_staff_map(db, list(all_visits.values()))

    names = await load_task_names(db, tasks)
    overdue_ids = await overdue_task_ids(db, tasks, today)
    out: list[dict] = []
    for t in tasks:
        nv = next_by_task.get(t.id)
        dv = done_visits.get(t.done_visit_id) if t.done_visit_id else None
        if staff_id is not None:
            if task_status(t) == "done":
                in_visit = dv is not None and any(
                    s.staff_id == staff_id for s in staff_by_visit.get(dv.id, [])
                )
                if not (in_visit or t.done_by_staff_id == staff_id):
                    continue
            elif nv is None or not any(
                s.staff_id == staff_id for s in staff_by_visit.get(nv.id, [])
            ):
                continue
        out.append(
            {
                **serialize_task(t, names, overdue=t.id in overdue_ids),
                "next_visit": visit_brief(nv, staff_by_visit.get(nv.id, [])) if nv else None,
                "done_visit": visit_brief(dv, staff_by_visit.get(dv.id, [])) if dv else None,
                "can_reopen": _can_reopen(t, user),
            }
        )
        if len(out) >= limit:
            break
    return out


@router.post("/tasks", response_model=PatientTaskCreateResult, status_code=status.HTTP_201_CREATED)
async def create_tasks(payload: PatientTaskCreate, db: DbDep, user: AdminUser) -> dict:
    patient = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="患者様が見つかりません")

    item_ids = list(dict.fromkeys(payload.item_type_ids))
    if not item_ids and payload.memo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="項目か伝言のどちらかを入れてください",
        )
    items: dict[UUID, TaskItemType] = {}
    if item_ids:
        items = {
            r.id: r
            for r in (
                await db.scalars(select(TaskItemType).where(TaskItemType.id.in_(item_ids)))
            ).all()
        }
        if any(i not in items or not items[i].is_active for i in item_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="使えない項目が含まれています",
            )

    now = datetime.now(UTC)
    # 項目ごとに 1 件。伝言は最初の 1 件に付ける (項目が無ければ伝言だけの 1 件)。
    specs: list[tuple[UUID | None, str | None]] = (
        [(iid, payload.memo if i == 0 else None) for i, iid in enumerate(item_ids)]
        if item_ids
        else [(None, payload.memo)]
    )
    tasks: list[PatientTask] = []
    for iid, memo in specs:
        t = PatientTask(
            patient_id=patient.id,
            start_date=payload.start_date,
            item_type_id=iid,
            memo=memo,
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(t)
        tasks.append(t)
    await db.flush()

    today = jst_today()
    next_by_task = await next_visits_for_tasks(db, tasks[:1], today)
    visit = next_by_task.get(tasks[0].id)
    staff = (await visit_staff_map(db, [visit])).get(visit.id, []) if visit else []
    notified = await notify_task_assigned(
        db,
        tasks=tasks,
        visit=visit,
        staff=staff,
        patient_name=patient.name,
        item_names=[items[i].name for i in item_ids],
    )
    await db.commit()

    names = await load_task_names(db, tasks)
    overdue_ids = await overdue_task_ids(db, tasks, today)
    brief = visit_brief(visit, staff) if visit else None
    return {
        "tasks": [
            {**serialize_task(t, names, overdue=t.id in overdue_ids), "next_visit": brief}
            for t in tasks
        ],
        "next_visit": brief,
        "notified_user_count": notified,
    }


@router.get("/tasks/counts", response_model=VisitTaskCounts)
async def visit_task_counts(
    db: DbDep,
    user: CurrentActiveUser,
    date_: Annotated[date, Query(alias="date")],
    staff_id: Annotated[UUID | None, Query(description="管理者だけ: この職員の訪問に絞る")] = None,
) -> dict:
    """訪問 id → 未完了数・持ち越しの有無・全部完了か (訪問モニター・スマホの今日の一覧用)."""
    _ensure_admin_or_staff(user)
    stmt = (
        select(Visit)
        .join(Patient, Patient.id == Visit.patient_id)
        .where(Visit.visit_date == date_, eligible_visit_clause(), Patient.deleted_at.is_(None))
    )
    if not _is_admin(user):
        if user.staff_id is None:
            return {"date": date_, "counts": {}}
        stmt = stmt.where(_staff_visibility_filter(user.staff_id))
    elif staff_id is not None:
        stmt = stmt.where(_staff_visibility_filter(staff_id))
    visits = list((await db.scalars(stmt)).all())
    entries_by_visit = await tasks_for_visits(db, visits)
    counts = {}
    for vid, entries in entries_by_visit.items():
        open_entries = [e for e in entries if e.task.done_at is None]
        counts[vid] = {
            "open_count": len(open_entries),
            "total_count": len(entries),
            "has_carried_over": any(e.carried_over for e in open_entries),
            "all_done": not open_entries,
        }
    return {"date": date_, "counts": counts}


@router.get("/visits/{visit_id}/tasks", response_model=list[PatientTaskRead])
async def list_visit_tasks(visit_id: UUID, db: DbDep, user: CurrentActiveUser) -> list[dict]:
    """その訪問に出るやること (職員は自分の訪問だけ・見えない訪問は 404)."""
    _ensure_admin_or_staff(user)
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not _is_admin(user) and not await _staff_sees_visit(db, user.staff_id, visit.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    entries = (await tasks_for_visits(db, [visit])).get(visit.id, [])
    tasks = [e.task for e in entries]
    names = await load_task_names(db, tasks)
    overdue_ids = await overdue_task_ids(db, tasks, jst_today())
    entries.sort(key=lambda e: task_sort_key(e.task, names))
    return [
        {
            **serialize_task(e.task, names, overdue=e.task.id in overdue_ids),
            "carried_over": e.carried_over,
            "can_reopen": _can_reopen(e.task, user),
        }
        for e in entries
    ]


@router.post("/tasks/{task_id}/complete", response_model=PatientTaskRead)
async def complete_task(
    task_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    payload: PatientTaskComplete | None = None,
) -> dict:
    """完了にする。職員は、そのやることが出ている自分の訪問からだけ.

    ``visit_id`` を省くと、管理者は訪問を指さずに完了 (完了した日の訪問に出る)、
    職員は今日の自分の訪問のうち、そのやることが出ているものを使う (無ければ 403)。
    2 名訪問は片方が完了すれば完了。
    """
    _ensure_admin_or_staff(user)
    is_admin = _is_admin(user)
    if not is_admin and user.staff_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="職員の登録がありません")
    task = await _get_task(db, task_id)
    if task_status(task) == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="取り消されたやることです")
    if task_status(task) == "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="すでに完了しています")

    visit_id = payload.visit_id if payload else None
    visit: Visit | None = None
    if visit_id is not None:
        visit = await db.scalar(
            select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        )
        if visit is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="訪問が見つかりません"
            )
        if not is_admin and not await _staff_sees_visit(db, user.staff_id, visit.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="担当の訪問ではありません"
            )
        shown = (await tasks_for_visits(db, [visit])).get(visit.id, [])
        if not any(e.task.id == task.id for e in shown):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="この訪問には出ていないやることです",
            )
        # 職員はお願い (項目のあるやること) を先の日の訪問で完了できない。伝言は先の日でも
        # 「読みました」にできる (mobile-future-days・PO 2026-11-01)。管理者は制限なし。
        if not is_admin and task.item_type_id is not None and visit.visit_date > jst_today():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="先の日の訪問では完了できません",
            )
    elif not is_admin:
        candidates = list(
            (
                await db.scalars(
                    select(Visit)
                    .where(
                        Visit.patient_id == task.patient_id,
                        Visit.visit_date == jst_today(),
                        eligible_visit_clause(),
                        _staff_visibility_filter(user.staff_id),
                    )
                    .order_by(Visit.start_time, Visit.id)
                )
            ).all()
        )
        shown_by_visit = await tasks_for_visits(db, candidates)
        visit = next(
            (
                v
                for v in candidates
                if any(e.task.id == task.id for e in shown_by_visit.get(v.id, []))
            ),
            None,
        )
        if visit is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="担当の訪問ではありません"
            )

    # 同時に押されたときは先の 1 回だけが通る (まだ完了・取消でない行だけを書き換える)。
    now = datetime.now(UTC)
    result = await db.execute(
        update(PatientTask)
        .where(
            PatientTask.id == task.id,
            PatientTask.done_at.is_(None),
            PatientTask.cancelled_at.is_(None),
        )
        .values(
            done_at=now,
            done_by_user_id=user.id,
            done_by_staff_id=user.staff_id,
            done_visit_id=visit.id if visit else None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="すでに完了しています")
    await db.commit()
    await db.refresh(task)
    return await _read_one(db, task, user)


@router.post("/tasks/{task_id}/reopen", response_model=PatientTaskRead)
async def reopen_task(task_id: UUID, db: DbDep, user: CurrentActiveUser) -> dict:
    """完了を戻す。管理者はどれでも、職員は自分で押した完了だけ."""
    _ensure_admin_or_staff(user)
    task = await _get_task(db, task_id)
    if task_status(task) != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="完了していません")
    if not _can_reopen(task, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="ほかの人の完了は戻せません"
        )
    task.done_at = None
    task.done_by_user_id = None
    task.done_by_staff_id = None
    task.done_visit_id = None
    task.updated_at = datetime.now(UTC)
    await db.commit()
    return await _read_one(db, task, user)


@router.post("/tasks/{task_id}/cancel", response_model=PatientTaskRead)
async def cancel_task(task_id: UUID, db: DbDep, user: AdminUser) -> dict:
    task = await _get_task(db, task_id)
    if task_status(task) == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="すでに取り消しています")
    now = datetime.now(UTC)
    task.cancelled_at = now
    task.cancelled_by = user.id
    task.updated_at = now
    await db.commit()
    return await _read_one(db, task, user)
