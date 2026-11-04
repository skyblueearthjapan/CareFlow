"""毎月のやること (繰り返しの設定) endpoints — すべて管理者だけ.

正典 = ``docs/plans/patient-tasks-recurring-design-2026-11-02.md`` §4-5 (PO 決定 2026-11-02)。

    GET   /api/v1/recurring-task-rules                       — 設定の一覧 (対象の人数・未完了の数つき)
    GET   /api/v1/recurring-task-rules/candidates            — 外す方の候補 (区分で絞る)
    POST  /api/v1/recurring-task-rules/run-due               — 日々の見回り (VPS の cron 用・毎日 0:10 JST)
    POST  /api/v1/recurring-task-rules                       — 作る (今月の設定日を過ぎていれば今月の分も作る)
    PATCH /api/v1/recurring-task-rules/{id}                  — 変える・止める (作り済みの分は変えない)
    GET   /api/v1/recurring-task-rules/{id}/preview?month=   — その月の試し表示 (作らない)
    POST  /api/v1/recurring-task-rules/{id}/run?month=       — その月の分を今すぐ作る (抜けだけ)
    POST  /api/v1/recurring-task-rules/{id}/cancel-open      — 未完了の分をまとめて取り消す

作る処理・対象の決め方は ``app.services.recurring_tasks`` の 1 か所にある。
作った分にはベルを出さない (PO 2026-11-02)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select

from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.patient_task import RecurringTaskExclusion, RecurringTaskRule, TaskItemType
from app.models.user import User
from app.schemas.recurring_task import (
    RecurringCancelResult,
    RecurringCandidate,
    RecurringPreview,
    RecurringRunDueResult,
    RecurringRunResult,
    RecurringTaskRuleCreate,
    RecurringTaskRuleRead,
    RecurringTaskRuleUpdate,
    VisitCategory,
)
from app.services import recurring_tasks as svc
from app.services.patient_tasks import jst_today

router = APIRouter(prefix="/recurring-task-rules")

AdminUser = Annotated[User, Depends(require_role("admin"))]
MonthQuery = Annotated[
    str | None,
    Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (省くと今月)"),
]


def _month(value: str | None) -> date:
    if value is None:
        return svc.month_start(jst_today())
    try:
        return svc.parse_month(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="月の形が違います"
        ) from exc


async def _get_rule(db, rule_id: UUID) -> RecurringTaskRule:
    rule = await db.scalar(select(RecurringTaskRule).where(RecurringTaskRule.id == rule_id))
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return rule


async def _ensure_item_usable(db, item_type_id: UUID | None) -> None:
    if item_type_id is None:
        return
    active = await db.scalar(select(TaskItemType.is_active).where(TaskItemType.id == item_type_id))
    if not active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="使えない項目です"
        )


def _ensure_due_day(day_of_month: int, due_day: int | None) -> None:
    """毎月何日まで (1〜28) は毎月何日から以降 (0 = 末日はいつでもよい)."""
    if due_day is not None and due_day != 0 and due_day < day_of_month:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="期日（毎月何日まで）は、毎月何日からと同じ日か、後の日にしてください",
        )


async def _set_exclusions(db, rule_id: UUID, patient_ids: list[UUID], user_id: UUID) -> None:
    ids = list(dict.fromkeys(patient_ids))
    if ids:
        # 外した後に削除された方は黙って捨てる (画面が送り返しても保存できるように)。
        # 一度もいない id は今までどおり 422。
        rows = (
            await db.execute(select(Patient.id, Patient.deleted_at).where(Patient.id.in_(ids)))
        ).all()
        if len(rows) != len(ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="外す方に見つからない患者様が含まれています",
            )
        alive = {pid for pid, deleted_at in rows if deleted_at is None}
        ids = [pid for pid in ids if pid in alive]
    await db.execute(
        delete(RecurringTaskExclusion).where(RecurringTaskExclusion.rule_id == rule_id)
    )
    now = datetime.now(UTC)
    for pid in ids:
        db.add(
            RecurringTaskExclusion(
                rule_id=rule_id, patient_id=pid, created_by=user_id, created_at=now
            )
        )


async def _read_rules(db, rules: list[RecurringTaskRule]) -> list[dict]:
    if not rules:
        return []
    rule_ids = [r.id for r in rules]
    item_ids = {r.item_type_id for r in rules if r.item_type_id is not None}
    items: dict[UUID, tuple[str, bool]] = {}
    if item_ids:
        for iid, name, active in (
            await db.execute(
                select(TaskItemType.id, TaskItemType.name, TaskItemType.is_active).where(
                    TaskItemType.id.in_(item_ids)
                )
            )
        ).all():
            items[iid] = (name, active)
    excluded: dict[UUID, list[dict]] = {rid: [] for rid in rule_ids}
    for rid, pid, name in (
        await db.execute(
            select(RecurringTaskExclusion.rule_id, Patient.id, Patient.name)
            .join(Patient, Patient.id == RecurringTaskExclusion.patient_id)
            .where(RecurringTaskExclusion.rule_id.in_(rule_ids), Patient.deleted_at.is_(None))
            .order_by(Patient.kana, Patient.name)
        )
    ).all():
        excluded[rid].append({"patient_id": pid, "name": name})
    open_by_rule = await svc.open_counts(db, rule_ids)

    # 対象の人数: 稼働中の方を 1 回だけ引いて、設定ごとに区分と外す方で数える。
    active_rows = (
        await db.execute(
            select(Patient.id, Patient.visit_category).where(
                Patient.deleted_at.is_(None), Patient.status == svc.TARGET_STATUS
            )
        )
    ).all()
    out = []
    for r in rules:
        ex = {e["patient_id"] for e in excluded[r.id]}
        cats = set(r.target_categories)
        item = items.get(r.item_type_id) if r.item_type_id else None
        out.append(
            {
                "id": r.id,
                "item_type_id": r.item_type_id,
                "item_type_name": item[0] if item else None,
                "item_type_active": item[1] if item else r.item_type_id is None,
                "memo": r.memo,
                "day_of_month": r.day_of_month,
                "due_day": r.due_day,
                "target_categories": list(r.target_categories),
                "start_month": r.start_month,
                "is_active": r.is_active,
                "excluded_patients": excluded[r.id],
                "target_count": sum(1 for pid, c in active_rows if c in cats and pid not in ex),
                "open_count": open_by_rule.get(r.id, 0),
                "created_at": r.created_at,
            }
        )
    return out


async def _create_now_if_due(db, rule: RecurringTaskRule, user: User) -> int:
    """保存したとき、今月の設定日を過ぎていれば今月の分を作る (最初に作る月が今月以前のとき)."""
    today = jst_today()
    if not svc.is_due(rule, today):
        return 0
    return await svc.ensure_rule_month(
        db, rule, svc.month_start(today), today=today, created_by=user.id
    )


# ⚠ 登録の順: ``/candidates``・``/run-due`` は ``/{rule_id}`` より先。


@router.get("", response_model=list[RecurringTaskRuleRead])
async def list_rules(db: DbDep, _user: AdminUser) -> list[dict]:
    rules = list(
        (
            await db.scalars(
                select(RecurringTaskRule).order_by(
                    RecurringTaskRule.is_active.desc(), RecurringTaskRule.created_at
                )
            )
        ).all()
    )
    return await _read_rules(db, rules)


@router.get("/candidates", response_model=list[RecurringCandidate])
async def list_candidates(
    db: DbDep,
    _user: AdminUser,
    categories: Annotated[list[VisitCategory], Query()],
) -> list[dict]:
    rows = await svc.candidate_patients(db, list(categories))
    return [
        {
            "patient_id": p.id,
            "name": p.name,
            "kana": p.kana,
            "visit_category": p.visit_category,
            "status": p.status,
        }
        for p in rows
    ]


@router.post("/run-due", response_model=RecurringRunDueResult)
async def run_due(db: DbDep, _user: AdminUser) -> dict:
    """日々の見回り (VPS の cron 用)。何回流しても同じ方・同じ月に 2 件は作らない."""
    result = await svc.run_due(db)
    await db.commit()
    return result.as_dict()


@router.post("", response_model=RecurringTaskRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(payload: RecurringTaskRuleCreate, db: DbDep, user: AdminUser) -> dict:
    if payload.item_type_id is None and payload.memo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="項目か伝言のどちらかを入れてください",
        )
    await _ensure_item_usable(db, payload.item_type_id)
    _ensure_due_day(payload.day_of_month, payload.due_day)
    now = datetime.now(UTC)
    rule = RecurringTaskRule(
        item_type_id=payload.item_type_id,
        memo=payload.memo,
        day_of_month=payload.day_of_month,
        due_day=payload.due_day,
        target_categories=list(payload.target_categories),
        start_month=payload.start_month,
        is_active=payload.is_active,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    await db.flush()
    await _set_exclusions(db, rule.id, payload.excluded_patient_ids, user.id)
    await db.flush()
    created = await _create_now_if_due(db, rule, user)
    await db.commit()
    (read,) = await _read_rules(db, [rule])
    return {**read, "created_now": created}


@router.patch("/{rule_id}", response_model=RecurringTaskRuleRead)
async def update_rule(
    rule_id: UUID, payload: RecurringTaskRuleUpdate, db: DbDep, user: AdminUser
) -> dict:
    """変えた中身は次に作る分から使う (作り済みの分はそのまま・e=E1)."""
    rule = await _get_rule(db, rule_id)
    sent = payload.model_fields_set
    if "item_type_id" in sent and payload.item_type_id != rule.item_type_id:
        await _ensure_item_usable(db, payload.item_type_id)
        rule.item_type_id = payload.item_type_id
    if "memo" in sent:
        rule.memo = payload.memo
    if rule.item_type_id is None and rule.memo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="項目か伝言のどちらかを入れてください",
        )
    if payload.day_of_month is not None:
        rule.day_of_month = payload.day_of_month
    if "due_day" in sent:
        rule.due_day = payload.due_day
    _ensure_due_day(rule.day_of_month, rule.due_day)
    if payload.target_categories is not None:
        rule.target_categories = list(payload.target_categories)
    if payload.start_month is not None:
        rule.start_month = payload.start_month
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.excluded_patient_ids is not None:
        await _set_exclusions(db, rule.id, payload.excluded_patient_ids, user.id)
    rule.updated_at = datetime.now(UTC)
    await db.flush()
    created = await _create_now_if_due(db, rule, user)
    await db.commit()
    (read,) = await _read_rules(db, [rule])
    return {**read, "created_now": created}


@router.get("/{rule_id}/preview", response_model=RecurringPreview)
async def preview_rule(
    rule_id: UUID, db: DbDep, _user: AdminUser, month: MonthQuery = None
) -> dict:
    rule = await _get_rule(db, rule_id)
    return await svc.preview(db, rule, _month(month))


@router.post("/{rule_id}/run", response_model=RecurringRunResult)
async def run_rule(rule_id: UUID, db: DbDep, user: AdminUser, month: MonthQuery = None) -> dict:
    """その月の分を今すぐ作る (抜けている方だけ)."""
    rule = await _get_rule(db, rule_id)
    target = _month(month)
    if not rule.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="停止中の設定です")
    if target < rule.start_month:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="最初に作る月より前の月です"
        )
    await _ensure_item_usable(db, rule.item_type_id)
    created = await svc.ensure_rule_month(db, rule, target, created_by=user.id)
    await db.commit()
    return {"created_count": created}


@router.post("/{rule_id}/cancel-open", response_model=RecurringCancelResult)
async def cancel_open(rule_id: UUID, db: DbDep, user: AdminUser) -> dict:
    """この設定から作った未完了の分をまとめて取り消す (完了した分は残す)."""
    await _get_rule(db, rule_id)
    n = await svc.cancel_open_tasks(db, rule_id, user.id)
    await db.commit()
    return {"cancelled_count": n}
