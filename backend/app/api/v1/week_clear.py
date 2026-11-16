"""「この週を空にする」と元に戻す — 青ピン廃止 BP-4 (PO 2026-10-12).

設計: ``docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md``

* ``POST /schedule/v2/weeks/{iso_year}/{iso_week}/clear`` — ``dry_run: true`` は件数だけ
  (DB 不変)。本実行は **先にスナップショット** (``inbound_snapshots``・kind=clear_week)
  を取ってから論理削除する。残すのは打刻・実績・サインのある訪問と取消の記録だけ。
* ``POST .../clear/undo`` — 消した訪問だけを書き戻す (残した訪問には触れない)。
* 期間で組む (``agent-period-build-design-2026-11-16.md`` §3-1): 任意の ``date_from``・
  ``date_to`` (どちらもこの週の中) を渡すと、その日だけを空にする。範囲の外の日の訪問は
  元に戻すときの判定に使わない (範囲の中だけを見る)。渡さなければ週全体 (今までどおり)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.schedule import _get_assign_staff_only_lock
from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.inbound_snapshot import InboundSnapshot
from app.models.user import User
from app.services.kaipoke.inbound_snapshot import (
    ClearUndoBlockedError,
    restore_cleared_visits,
    snapshot_week,
)
from app.services.scheduling.week_clear import (
    SNAPSHOT_KIND_CLEAR_WEEK,
    apply_clear,
    plan_clear,
    range_error,
)

router = APIRouter()


class ClearWeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    office_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None


class ClearWeekResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_delete: int
    kaipoke_linked: int
    kept_checked_in: int
    snapshot_id: UUID | None = None


class ClearUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID


class ClearUndoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restored: int


def _monday(iso_year: int, iso_week: int) -> date:
    try:
        return date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc


def _busy() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "この週で週の作成・自動スタッフ割当・週のコピー・週を空にする のいずれかを実行中です。"
            "完了までお待ちください（同じ週への同時実行はできません）。"
        ),
    )


@router.post(
    "/v2/weeks/{iso_year}/{iso_week}/clear",
    response_model=ClearWeekResponse,
    summary="この週を空にする (dry_run で件数・admin)",
)
async def clear_week(
    iso_year: int,
    iso_week: int,
    payload: ClearWeekRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> ClearWeekResponse:
    monday = _monday(iso_year, iso_week)
    if (msg := range_error(monday, payload.date_from, payload.date_to)) is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)
    # 範囲つきのときだけスナップショットと監査ログに範囲を残す (範囲なしは今までと同じ形)。
    range_info = (
        {"date_from": payload.date_from.isoformat(), "date_to": payload.date_to.isoformat()}
        if payload.date_from is not None and payload.date_to is not None
        else {}
    )
    if payload.dry_run:
        plan = await plan_clear(db, monday, payload.office_id, payload.date_from, payload.date_to)
        return ClearWeekResponse(
            to_delete=len(plan.to_delete),
            kaipoke_linked=plan.kaipoke_linked,
            kept_checked_in=len(plan.kept_checked_in),
        )

    lock = _get_assign_staff_only_lock(iso_year, iso_week)
    if lock.locked():
        raise _busy()
    async with lock:
        plan = await plan_clear(db, monday, payload.office_id, payload.date_from, payload.date_to)
        try:
            snap = await snapshot_week(db, monday, kind=SNAPSHOT_KIND_CLEAR_WEEK, user_id=user.id)
            snapshot_id = snap.id  # commit 後に ORM 属性を読まない (expire 対策)
            snap.payload = {
                **snap.payload,
                "office_id": str(payload.office_id) if payload.office_id else None,
                "cleared_visit_ids": [str(v.id) for v in plan.to_delete],
                "kept_visit_ids": [
                    str(v.id) for v in (*plan.kept_checked_in, *plan.kept_cancelled)
                ],
                "cleared_courses": [
                    {
                        "id": str(c.id),
                        "assigned_staff_id": str(c.assigned_staff_id),
                        "course_status": c.course_status,
                        "staff_assigned_at": (
                            c.staff_assigned_at.isoformat() if c.staff_assigned_at else None
                        ),
                    }
                    for c in plan.cleared_courses
                ],
                **range_info,
                # 範囲の外の日の訪問 (記録用)。元に戻すときは範囲の中だけを見るので判定には使わない。
                **(
                    {"outside_visit_ids": [str(v.id) for v in plan.outside_range]}
                    if range_info
                    else {}
                ),
            }
            counts = {
                "to_delete": len(plan.to_delete),
                "kaipoke_linked": plan.kaipoke_linked,
                "kept_checked_in": len(plan.kept_checked_in),
            }
            apply_clear(plan)
            db.add(
                AuditLog(
                    actor_user_id=user.id,
                    action="schedule_clear_week",
                    target_table="visits",
                    target_id=monday.isoformat(),
                    before={"snapshot_id": str(snapshot_id)},
                    after={
                        **counts,
                        "courses_cleared": len(plan.cleared_courses),
                        "office_id": str(payload.office_id) if payload.office_id else None,
                        **range_info,
                    },
                )
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return ClearWeekResponse(**counts, snapshot_id=snapshot_id)


@router.post(
    "/v2/weeks/{iso_year}/{iso_week}/clear/undo",
    response_model=ClearUndoResponse,
    summary="この週を空にする前へ戻す (admin)",
)
async def clear_week_undo(
    iso_year: int,
    iso_week: int,
    payload: ClearUndoRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> ClearUndoResponse:
    monday = _monday(iso_year, iso_week)
    lock = _get_assign_staff_only_lock(iso_year, iso_week)
    if lock.locked():
        raise _busy()
    async with lock:
        snap = await db.get(InboundSnapshot, payload.snapshot_id)
        if snap is None or snap.kind != SNAPSHOT_KIND_CLEAR_WEEK or snap.week_start != monday:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="snapshot not found")
        try:
            restored = await restore_cleared_visits(db, snap, now=datetime.now(UTC))
            db.add(
                AuditLog(
                    actor_user_id=user.id,
                    action="schedule_clear_week_undo",
                    target_table="visits",
                    target_id=monday.isoformat(),
                    before={"snapshot_id": str(payload.snapshot_id)},
                    after={"restored": restored},
                )
            )
            await db.commit()
        except ClearUndoBlockedError as exc:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception:
            await db.rollback()
            raise
    return ClearUndoResponse(restored=restored)
