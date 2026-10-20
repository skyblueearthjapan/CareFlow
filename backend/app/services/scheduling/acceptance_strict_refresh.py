"""受け入れ枠のカイポケの材料の取り直し — 取る範囲を決めて、段階 0 の仕組みを呼ぶ.

正典: ``docs/plans/acceptance-strict-design-2026-10-20.md`` §3-3・§3-6・§4-2。
取り直しの仕組み (ジョブ・RPA の空き確認・待ち・保存) は段階 0 の
``app.services.kaipoke.sources``。ここで決めるのは **取る月・週の一覧** だけ。

* ふだん (``recent``): 実績 = 今日から 14 日前〜昨日の日を含む月、予定 = 今週と来週
  (月〜土) の日を含む月、個別業務 = 見る範囲 (8 週) のうち、段階 0 の表に取れた行が
  無い週と、その週の日曜より前に取ったままの週。
* すべて (``all``): 8 週にかかる全部の月の実績・予定と、8 週ぶんの個別業務。
* ``kaipoke_csv_snapshots`` にも業務データにも書かない (段階 0 の表とジョブだけ)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.services.kaipoke import sources
from app.services.kaipoke.sources import (
    KIND_ACTUAL_CSV,
    KIND_PLAN_CSV,
    KIND_TASKS,
    OP_ACCEPTANCE_REFRESH,
    STATUS_EMPTY,
    STATUS_OK,
)
from app.services.scheduling.acceptance_strict_data import JST, months_between, view_mondays

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.kaipoke_job import KaipokeJob
    from app.services.kaipoke_client import KaipokeClient

SCOPE_RECENT: Final = "recent"
SCOPE_ALL: Final = "all"
SCOPES: Final = (SCOPE_RECENT, SCOPE_ALL)
#: ふだんの取り直しで実績を取り直す日数 (昨日まで)。
ACTUAL_LOOKBACK_DAYS: Final = 14


@dataclass(frozen=True)
class RefreshTarget:
    """取り直す 1 本 (``kind`` と月の 1 日 / 週の月曜)。"""

    kind: str
    period_start: date

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "period_start": self.period_start.isoformat()}


def _week_saturday(monday: date) -> date:
    return monday + timedelta(days=5)


def plan_targets(
    today: date,
    scope: str,
    tasks_fetched: dict[date, datetime | None],
) -> list[RefreshTarget]:
    """取り直す本の一覧 (実績 → 予定 → 個別業務の順)。

    ``tasks_fetched``: 見る範囲の週の月曜 → 段階 0 の表に取れた行 (``ok`` / ``empty``)
    がある週の ``fetched_at`` (無い週は入れないか None)。
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    mondays = sorted(view_mondays(today))
    this_monday = mondays[-1]
    if scope == SCOPE_ALL:
        months = months_between(mondays[0], _week_saturday(this_monday))
        actual_months = months
        plan_months = months
        task_weeks = mondays
    else:
        actual_months = months_between(
            today - timedelta(days=ACTUAL_LOOKBACK_DAYS), today - timedelta(days=1)
        )
        plan_months = months_between(this_monday, _week_saturday(this_monday + timedelta(weeks=1)))
        task_weeks = []
        for monday in mondays:
            fetched = tasks_fetched.get(monday)
            sunday_start = datetime.combine(monday + timedelta(days=6), time.min, tzinfo=JST)
            if fetched is None or fetched < sunday_start:
                task_weeks.append(monday)
    return [
        *(RefreshTarget(KIND_ACTUAL_CSV, m) for m in actual_months),
        *(RefreshTarget(KIND_PLAN_CSV, m) for m in plan_months),
        *(RefreshTarget(KIND_TASKS, w) for w in task_weeks),
    ]


async def load_tasks_fetched(db: AsyncSession, today: date) -> dict[date, datetime | None]:
    """見る範囲の週の個別業務が、段階 0 の表にいつ取れているか。"""
    mondays = view_mondays(today)
    rows = (
        await db.execute(
            select(KaipokeSourceSnapshot.period_start, KaipokeSourceSnapshot.fetched_at).where(
                KaipokeSourceSnapshot.kind == KIND_TASKS,
                KaipokeSourceSnapshot.period_start.in_(mondays),
                KaipokeSourceSnapshot.status.in_((STATUS_OK, STATUS_EMPTY)),
            )
        )
    ).all()
    out: dict[date, datetime | None] = {}
    for period_start, fetched_at in rows:
        if fetched_at is not None and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)  # SQLite は時差を落として返す
        out[period_start] = fetched_at
    return out


async def start_refresh(
    db: AsyncSession,
    *,
    scope: str,
    today: date,
    user_id: uuid.UUID | None,
) -> tuple[KaipokeJob, list[RefreshTarget]]:
    """取る本を決めてジョブを ``running`` で立てる。他のジョブが動いていれば 409 (段階 0)。"""
    targets = plan_targets(today, scope, await load_tasks_fetched(db, today))
    first_monday = min(view_mondays(today))
    job = await sources.start_source_job(
        db,
        op=OP_ACCEPTANCE_REFRESH,
        week_start=first_monday,
        user_id=user_id,
        params={"scope": scope, "targets": [t.as_dict() for t in targets]},
        total=len(targets),
    )
    return job, targets


def source_plan(targets: list[RefreshTarget]) -> list[dict[str, object]]:
    """段階 0 の ``run_source_job`` に渡す「取る本の一覧」(kind と period)。"""
    return [{"kind": t.kind, "period": t.period_start} for t in targets]


async def run_refresh_job(
    *,
    job_id: uuid.UUID,
    targets: list[RefreshTarget],
    client: KaipokeClient,
) -> None:
    """応答の後に自前のセッションで段階 0 の ``run_source_job`` を呼ぶ。

    1 本ずつ取る・進み具合を書き戻す・例外でも必ずジョブを閉じるのは段階 0 の仕事
    (受け入れ枠は取り直しのループを持たない)。
    """
    factory = get_session_factory()
    async with factory() as db:
        await sources.run_source_job(db, client, job_id, source_plan(targets))


__all__ = [
    "ACTUAL_LOOKBACK_DAYS",
    "SCOPES",
    "SCOPE_ALL",
    "SCOPE_RECENT",
    "RefreshTarget",
    "load_tasks_fetched",
    "plan_targets",
    "run_refresh_job",
    "source_plan",
    "start_refresh",
]
