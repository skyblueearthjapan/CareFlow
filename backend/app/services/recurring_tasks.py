"""毎月のやること (繰り返しの設定) — 対象の方の決め方・その月の分を作る・試し表示.

正典 = ``docs/plans/patient-tasks-recurring-design-2026-11-02.md`` (PO 決定 2026-11-02:
§6 の (a)〜(g) はすべておすすめのとおり)。

* 対象 = 削除されていない・**稼働中** (``status='active'``) の方のうち、訪問の区分が設定の
  区分に入り、個別に外していない方 (a=A3)。休止・入院・開始前の方には作らない (g=G1)。
  稼働に戻った・月の途中で始まった方は、次の見回りでその月の分を作る (b=B1)。
* 作るのは ``patient_tasks`` の 1 件 (``start_date`` = その月の設定日)。出し方・持ち越し・
  完了は手で付けたものと同じ。先月の分が残っていても今月の分を並べて作る (c=C1)。
* **同じ設定・同じ方・同じ月は 1 件だけ** (一意の索引 + ON CONFLICT DO NOTHING)。取り消した
  分も「作った」に数えるので、取り消した方へもう一度作らない。
* 同じ月に同じ項目を手で付けた方 (取消でないもの) には作らない (f)。
* 作り済みの分は、設定を変えても止めても変えない (e=E1)。止めた後の未完了は
  :func:`cancel_open_tasks` でまとめて取り消す。
* ベルは出さない (PO 2026-11-02)。作った件数はログに残す。

日々の見回り (:func:`run_due`) は VPS の cron から毎日 0:10 (JST) に呼ぶ。今月の設定日を
過ぎた有効な設定について、抜けている方の分を作る (何回流しても同じ)。手で流すときは
``python -m app.services.recurring_tasks`` か ``POST /api/v1/recurring-task-rules/run-due``。
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_task import (
    PatientTask,
    RecurringTaskExclusion,
    RecurringTaskRule,
    TaskItemType,
)
from app.services.patient_tasks import (
    first_visit_on_or_after,
    jst_today,
    load_patient_visits,
    visit_brief,
    visit_staff_map,
)

logger = logging.getLogger(__name__)

#: 訪問の区分 (``patients.visit_category``)。
VISIT_CATEGORIES: Final = ("psychiatric", "general")
#: 対象になる患者の状態 (稼働中だけ)。
TARGET_STATUS: Final = "active"
#: 設定の画面で「外す方」の候補に並べる状態 (取消 = 終了の方は並べない)。
CANDIDATE_STATUSES: Final = ("active", "suspended", "admitted", "pending")


def month_start(d: date) -> date:
    return d.replace(day=1)


def parse_month(s: str) -> date:
    """``2026-12`` → 2026-12-01 (形が違えば ValueError)."""
    y, m = s.split("-")
    if len(y) != 4 or len(m) != 2:
        raise ValueError(s)
    return date(int(y), int(m), 1)


def next_month(d: date) -> date:
    return (month_start(d) + timedelta(days=32)).replace(day=1)


def rule_day(rule: RecurringTaskRule, month: date) -> date:
    """その月の設定日 (1〜28 なので、どの月にもある)."""
    return month.replace(day=rule.day_of_month)


def task_start_date(rule: RecurringTaskRule, month: date, today: date) -> date:
    """作るやることの ``start_date``.

    その月の設定日。ただし設定日より後に作るとき (今月の途中で稼働になった方・
    見回りが止まった日の抜け・設定を後から作った・今月より前の月の分を手で作った) は
    今日にする。作ったその日に「期日を過ぎた」と出さないため。出す訪問
    (今日以降の最初の訪問) は同じ。
    """
    return max(rule_day(rule, month), today)


def task_due_date(rule: RecurringTaskRule, month: date, today: date) -> date | None:
    """作るやることの ``due_date`` (``docs/plans/patient-tasks-due-design-2026-11-04.md``).

    その月の ``due_day`` 日 (0 は末日)。その日が作った日 (今日) より前なら今日にする
    (§6-2: 作った直後に「期日を過ぎた」と出さない)。``due_day`` が無ければ期日なし。
    """
    if rule.due_day is None:
        return None
    day = rule.due_day or calendar.monthrange(month.year, month.month)[1]
    return max(month.replace(day=day), today)


# ---------------------------------------------------------------------------
# 対象の方
# ---------------------------------------------------------------------------


async def excluded_ids(db: AsyncSession, rule_id: UUID) -> set[UUID]:
    return set(
        (
            await db.scalars(
                select(RecurringTaskExclusion.patient_id).where(
                    RecurringTaskExclusion.rule_id == rule_id
                )
            )
        ).all()
    )


async def target_patients(
    db: AsyncSession, categories: list[str], exclude: set[UUID]
) -> list[Patient]:
    """いま稼働中で、区分が合い、個別に外していない方 (名前の読み順)."""
    if not categories:
        return []
    rows = (
        await db.scalars(
            select(Patient)
            .where(
                Patient.deleted_at.is_(None),
                Patient.status == TARGET_STATUS,
                Patient.visit_category.in_(categories),
            )
            .order_by(Patient.kana, Patient.name)
        )
    ).all()
    return [p for p in rows if p.id not in exclude]


async def target_count(db: AsyncSession, rule: RecurringTaskRule) -> int:
    return len(
        await target_patients(db, list(rule.target_categories), await excluded_ids(db, rule.id))
    )


async def candidate_patients(db: AsyncSession, categories: list[str]) -> list[Patient]:
    """設定の画面の「外す方」の候補 (区分が合い、終了でない方)."""
    if not categories:
        return []
    return list(
        (
            await db.scalars(
                select(Patient)
                .where(
                    Patient.deleted_at.is_(None),
                    Patient.status.in_(CANDIDATE_STATUSES),
                    Patient.visit_category.in_(categories),
                )
                .order_by(Patient.kana, Patient.name)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# その月の分を作る
# ---------------------------------------------------------------------------


async def _made_patient_ids(db: AsyncSession, rule_id: UUID, month: date) -> set[UUID]:
    """この設定でこの月にもう作った方 (取消・完了も含む)."""
    return set(
        (
            await db.scalars(
                select(PatientTask.patient_id).where(
                    PatientTask.recurring_rule_id == rule_id,
                    PatientTask.recurring_month == month,
                )
            )
        ).all()
    )


async def _manual_patient_ids(
    db: AsyncSession, rule: RecurringTaskRule, month: date, patient_ids: set[UUID]
) -> set[UUID]:
    """同じ月に同じ項目を手で付けた方 (取消でないもの)。伝言だけの設定は見ない."""
    if rule.item_type_id is None or not patient_ids:
        return set()
    return set(
        (
            await db.scalars(
                select(PatientTask.patient_id).where(
                    PatientTask.patient_id.in_(patient_ids),
                    PatientTask.recurring_rule_id.is_(None),
                    PatientTask.item_type_id == rule.item_type_id,
                    PatientTask.cancelled_at.is_(None),
                    PatientTask.start_date >= month,
                    PatientTask.start_date < next_month(month),
                )
            )
        ).all()
    )


async def _item_is_active(db: AsyncSession, rule: RecurringTaskRule) -> bool:
    if rule.item_type_id is None:
        return True
    active = await db.scalar(
        select(TaskItemType.is_active).where(TaskItemType.id == rule.item_type_id)
    )
    return bool(active)


def _insert_ignoring_duplicates(db: AsyncSession, rows: list[dict]):
    """一意の索引 (設定・方・月) にぶつかる行は飛ばす INSERT (同時に流れても 2 件にしない)."""
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return (
        insert(PatientTask)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["recurring_rule_id", "patient_id", "recurring_month"],
            index_where=PatientTask.recurring_rule_id.is_not(None),
        )
        .returning(PatientTask.id)
    )


async def ensure_rule_month(
    db: AsyncSession,
    rule: RecurringTaskRule,
    month: date,
    *,
    today: date | None = None,
    created_by: UUID | None = None,
) -> int:
    """この設定のこの月の分を、抜けている対象の方へ作る。作った件数を返す.

    止めた設定・使わない項目・最初に作る月より前の月には作らない。**commit しない**。
    """
    today = today or jst_today()
    month = month_start(month)
    if not rule.is_active or month < rule.start_month or not await _item_is_active(db, rule):
        return 0
    targets = await target_patients(
        db, list(rule.target_categories), await excluded_ids(db, rule.id)
    )
    if not targets:
        return 0
    ids = {p.id for p in targets}
    skip = await _made_patient_ids(db, rule.id, month)
    skip |= await _manual_patient_ids(db, rule, month, ids - skip)
    todo = [p for p in targets if p.id not in skip]
    if not todo:
        return 0
    now = datetime.now(UTC)
    start = task_start_date(rule, month, today)
    due = task_due_date(rule, month, today)
    rows = [
        {
            "id": uuid.uuid4(),
            "patient_id": p.id,
            "start_date": start,
            "due_date": due,
            "item_type_id": rule.item_type_id,
            "memo": rule.memo,
            "created_by": created_by,
            "recurring_rule_id": rule.id,
            "recurring_month": month,
            "created_at": now,
            "updated_at": now,
        }
        for p in todo
    ]
    created = len((await db.execute(_insert_ignoring_duplicates(db, rows))).all())
    logger.info(
        "recurring_tasks: rule=%s month=%s created=%d (targets=%d)",
        rule.id,
        month.isoformat(),
        created,
        len(targets),
    )
    return created


def is_due(rule: RecurringTaskRule, today: date) -> bool:
    """今月の分を作る日を過ぎたか (有効・最初に作る月から・設定日以降)."""
    month = month_start(today)
    return rule.is_active and rule.start_month <= month and today.day >= rule.day_of_month


@dataclass
class RunDueResult:
    today: date
    created_total: int = 0
    #: 設定 id → 作った件数 (作る日を過ぎた有効な設定だけ)。
    by_rule: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "today": self.today.isoformat(),
            "month": month_start(self.today).isoformat(),
            "created_total": self.created_total,
            "by_rule": self.by_rule,
        }


async def run_due(
    db: AsyncSession,
    *,
    today: date | None = None,
    rule_ids: set[UUID] | None = None,
    created_by: UUID | None = None,
) -> RunDueResult:
    """日々の見回り: 作る日を過ぎた有効な設定の、今月の抜けを作る。**commit しない**."""
    today = today or jst_today()
    stmt = select(RecurringTaskRule).where(RecurringTaskRule.is_active.is_(True))
    if rule_ids is not None:
        stmt = stmt.where(RecurringTaskRule.id.in_(rule_ids))
    result = RunDueResult(today=today)
    for rule in (await db.scalars(stmt.order_by(RecurringTaskRule.created_at))).all():
        if not is_due(rule, today):
            continue
        n = await ensure_rule_month(
            db, rule, month_start(today), today=today, created_by=created_by
        )
        result.by_rule[str(rule.id)] = n
        result.created_total += n
    logger.info(
        "recurring_tasks: run_due today=%s created_total=%d",
        today.isoformat(),
        result.created_total,
    )
    return result


async def cancel_open_tasks(db: AsyncSession, rule_id: UUID, user_id: UUID | None) -> int:
    """この設定から作った未完了の分をまとめて取り消す (完了・取消済みは触らない)。**commit しない**."""
    now = datetime.now(UTC)
    result = await db.execute(
        update(PatientTask)
        .where(
            PatientTask.recurring_rule_id == rule_id,
            PatientTask.done_at.is_(None),
            PatientTask.cancelled_at.is_(None),
        )
        .values(cancelled_at=now, cancelled_by=user_id, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def open_counts(db: AsyncSession, rule_ids: list[UUID]) -> dict[UUID, int]:
    """設定 id → その設定から作った未完了の件数."""
    if not rule_ids:
        return {}
    rows = (
        await db.execute(
            select(PatientTask.recurring_rule_id, func.count())
            .where(
                PatientTask.recurring_rule_id.in_(rule_ids),
                PatientTask.done_at.is_(None),
                PatientTask.cancelled_at.is_(None),
            )
            .group_by(PatientTask.recurring_rule_id)
        )
    ).all()
    return {rid: int(n) for rid, n in rows}


# ---------------------------------------------------------------------------
# 試し表示 (作らずに計算だけ)
# ---------------------------------------------------------------------------


async def preview(db: AsyncSession, rule: RecurringTaskRule, month: date) -> dict:
    """この月にどの方のどの訪問に出るか (作らない).

    行の ``result``:
    ``create`` = 作る (``visit`` が無ければ「訪問が入ったら出ます」) /
    ``exists`` = この月の分はもう作った / ``manual`` = 同じ月に同じ項目を手で付けた /
    ``excluded`` = 個別に外した / ``not_active`` = 休止・入院・開始前なので作らない
    (稼働に戻ったら作る)。``prev_open`` = この設定の前の月の分が未完了で残っている。
    """
    today = jst_today()
    month = month_start(month)
    categories = list(rule.target_categories)
    exclude = await excluded_ids(db, rule.id)
    candidates = await candidate_patients(db, categories)
    known = {p.id for p in candidates}
    missing = exclude - known
    if missing:
        candidates += list(
            (
                await db.scalars(
                    select(Patient).where(Patient.id.in_(missing), Patient.deleted_at.is_(None))
                )
            ).all()
        )
    ids = {p.id for p in candidates}
    made = await _made_patient_ids(db, rule.id, month)
    manual = await _manual_patient_ids(db, rule, month, ids - made)
    prev_open = set(
        (
            await db.scalars(
                select(PatientTask.patient_id).where(
                    PatientTask.recurring_rule_id == rule.id,
                    PatientTask.recurring_month < month,
                    PatientTask.done_at.is_(None),
                    PatientTask.cancelled_at.is_(None),
                )
            )
        ).all()
    )

    start = task_start_date(rule, month, today)
    show_from = max(start, today)
    rows: list[dict] = []
    for p in candidates:
        if p.id in exclude:
            res = "excluded"
        elif p.status != TARGET_STATUS:
            res = "not_active"
        elif p.id in made:
            res = "exists"
        elif p.id in manual:
            res = "manual"
        else:
            res = "create"
        rows.append({"patient": p, "result": res})

    showing = {r["patient"].id for r in rows if r["result"] in ("create", "exists")}
    visits_by_patient = await load_patient_visits(db, showing, show_from)
    firsts = {
        pid: first_visit_on_or_after(visits_by_patient.get(pid, []), show_from) for pid in showing
    }
    staff = await visit_staff_map(db, [v for v in firsts.values() if v is not None])

    counts: dict[str, int] = defaultdict(int)
    out_rows = []
    for r in rows:
        p = r["patient"]
        counts[r["result"]] += 1
        v = firsts.get(p.id)
        out_rows.append(
            {
                "patient_id": p.id,
                "patient_name": p.name,
                "status": p.status,
                "visit_category": p.visit_category,
                "result": r["result"],
                "visit": visit_brief(v, staff.get(v.id, [])) if v is not None else None,
                "prev_open": p.id in prev_open,
            }
        )
    order = {"create": 0, "exists": 1, "manual": 2, "not_active": 3, "excluded": 4}
    out_rows.sort(key=lambda x: order[x["result"]])
    return {
        "rule_id": rule.id,
        "month": month,
        "start_date": start,
        "due_date": task_due_date(rule, month, today),
        "is_active": rule.is_active,
        "item_is_active": await _item_is_active(db, rule),
        "counts": dict(counts),
        "prev_open_count": sum(1 for r in out_rows if r["prev_open"]),
        "rows": out_rows,
    }


# ---------------------------------------------------------------------------
# 手で流す: python -m app.services.recurring_tasks [--date YYYY-MM-DD]
# ---------------------------------------------------------------------------


async def _main(today: date | None) -> dict:
    import app.models  # noqa: F401 — 外部キーの先の表をすべて読み込む
    from app.db.session import dispose_engine, get_session_factory

    try:
        async with get_session_factory()() as db:
            result = await run_due(db, today=today)
            await db.commit()
            return result.as_dict()
    finally:
        await dispose_engine()


if __name__ == "__main__":  # pragma: no cover — cron から呼ぶ入口
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="毎月のやることの見回り (今月の抜けを作る)")
    parser.add_argument("--date", help="今日の代わりに使う日 (JST・YYYY-MM-DD)。試すときだけ")
    args = parser.parse_args()
    d = date.fromisoformat(args.date) if args.date else None
    print(json.dumps(asyncio.run(_main(d)), ensure_ascii=False))
