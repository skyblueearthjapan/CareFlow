"""前の週をコピーして週を作る API (docs/plans/copy-week-design-2026-09-30.md §4-7).

    GET  /api/v1/schedule/v2/copy-week/sources?target_week_start=YYYY-MM-DD
    POST /api/v1/schedule/v2/copy-week/preview   (DB 不変)
    POST /api/v1/schedule/v2/copy-week           (confirm: true 必須)

安全 (§4-6):
  - admin のみ。写す先は今週以降のみ。
  - 実行前に写す先の週を ``snapshot_week(kind='copy_week')`` で保存する。戻すのは
    既存の ``POST /integrations/inbound-snapshots/{id}/restore`` (「コピー前に戻す」)。
  - 自動スタッフ割当と同じ週単位のロックで排他する (二度押し・同時実行は 409)。
  - 1 トランザクション。「続けて自動スタッフ割当」を選んだ場合も、割当が失敗したら
    コピーごと取り消す (割当の確定と同じ commit で書く)。
  - 監査ログに写す元・写す先・件数・選んだオプションを残す。カイポケへは送らない。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.schedule import (
    AssignStaffOnlyRequest,
    AssignStaffOnlyResponse,
    _assign_staff_only_impl,
    _get_assign_staff_only_lock,
)
from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.v2.copy_week import (
    CopyCounts,
    CopySkipCounts,
    CopyWeekMode,
    CopyWeekPreviewRequest,
    CopyWeekPreviewResponse,
    CopyWeekRequest,
    CopyWeekSourceItem,
    CopyWeekSourcesResponse,
    ExistingCounts,
    HolidayRead,
    MissingFixedItem,
    NeedsManualStaffItem,
    NotInFixedItem,
    WeekdayCount,
)
from app.services.accompaniment import expand_accompaniment_defaults
from app.services.kaipoke.inbound_snapshot import snapshot_week
from app.services.scheduling.copy_week import (
    CopyPlan,
    CopyWeekError,
    apply_copy_plan,
    build_copy_plan,
    list_source_weeks,
    monday_of,
)
from app.services.scheduling.jp_holidays import holidays_between
from app.services.scheduling.layer1_expander import _ensure_manager_courses_for_week
from app.services.staff_event_defaults import expand_staff_event_defaults

router = APIRouter()

#: スナップショットの種別 (inbound_snapshots.kind・String(16))。
SNAPSHOT_KIND_COPY_WEEK = "copy_week"


class CopyWeekResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_week_start: date
    target_week_start: date
    mode: CopyWeekMode
    created: int
    filled: int
    replaced: int
    skipped: CopySkipCounts
    courses_created: int
    courses_cleared: int
    staff_mirrored: int
    snapshot_id: UUID
    # 打刻のある週は復元 API が打刻ガードで止まる → 「コピー前に戻す」は使えない
    restorable: bool
    needs_manual_staff: list[NeedsManualStaffItem]
    # 実行時に計画し直した件数と、確認画面で見せた件数 (送られていれば) の比較
    actual_counts: CopyCounts
    expected_counts: CopyCounts | None = None
    differs_from_preview: bool = False
    assign_result: AssignStaffOnlyResponse | None = None


def _counts(plan: CopyPlan) -> CopyCounts:
    return CopyCounts(
        copy_count=len(plan.items),
        fill_count=plan.fill_count,
        replace_count=len(plan.replace_visits),
        needs_manual_count=len(plan.needs_manual_staff),
    )


def _needs_manual(plan: CopyPlan) -> list[NeedsManualStaffItem]:
    return [
        NeedsManualStaffItem(
            origin=it.origin,  # type: ignore[arg-type]
            patient_id=it.patient_id,
            patient_name=it.patient_name,
            target_date=it.target_date,
            start_time=it.start_time,
            end_time=it.end_time,
            reason=it.manual_reason or "",
        )
        for it in plan.needs_manual_staff
    ]


def _detail_text(detail: object) -> str:
    """HTTPException.detail (文字列 / dict / list) を人が読める 1 行にする."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("message", "detail", "msg"):
            if isinstance(detail.get(key), str):
                return detail[key]
    return json.dumps(detail, ensure_ascii=False, default=str)


def _holidays(monday: date) -> list[HolidayRead]:
    return [
        HolidayRead(date=d, name=n) for d, n in holidays_between(monday, monday + timedelta(days=6))
    ]


def _preview_from_plan(plan: CopyPlan) -> CopyWeekPreviewResponse:
    counts: dict[int, int] = {}
    for it in [*plan.items, *plan.fill_items]:
        counts[it.weekday] = counts.get(it.weekday, 0) + 1
    by_weekday = [
        WeekdayCount(weekday=wd, date=plan.target_monday + timedelta(days=wd), count=counts[wd])
        for wd in sorted(counts)
    ]
    patients = {it.patient_id for it in [*plan.items, *plan.fill_items]}
    return CopyWeekPreviewResponse(
        source_week_start=plan.source_monday,
        target_week_start=plan.target_monday,
        mode=plan.mode,  # type: ignore[arg-type]
        copy_count=len(plan.items),
        fill_count=plan.fill_count,
        patients=len(patients),
        by_weekday=by_weekday,
        skipped=CopySkipCounts(**plan.skipped),
        temp_course_count=sum(1 for it in plan.items if it.temp_course),
        not_in_fixed=_not_in_fixed_rows(plan),
        missing_fixed=[
            MissingFixedItem(
                patient_id=m.patient.id,
                patient_name=m.patient.name,
                weekday=m.weekday,
                target_date=m.target_date,
                start_time=m.start_time,
                end_time=m.end_time,
                visits=m.visits,
            )
            for m in plan.missing_fixed
        ],
        missing_fixed_count=sum(m.visits for m in plan.missing_fixed),
        missing_patients_without_visits=plan.missing_patients_without_visits,
        existing=ExistingCounts(**plan.existing),
        needs_manual_staff=_needs_manual(plan),
        source_holidays=_holidays(plan.source_monday),
        target_holidays=_holidays(plan.target_monday),
    )


def _not_in_fixed_rows(plan: CopyPlan) -> list[NotInFixedItem]:
    """2 名体制の組は 1 行にまとめる (外すときは組ごと)."""
    rows: list[NotInFixedItem] = []
    index_of_group: dict[UUID, int] = {}
    for it in plan.not_in_fixed:
        if it.group_key is not None and it.group_key in index_of_group:
            rows[index_of_group[it.group_key]].visit_ids.append(it.key)
            continue
        if it.group_key is not None:
            index_of_group[it.group_key] = len(rows)
        rows.append(
            NotInFixedItem(
                visit_ids=[it.key],
                patient_id=it.patient_id,
                patient_name=it.patient_name,
                weekday=it.weekday,
                target_date=it.target_date,
                start_time=it.start_time,
                end_time=it.end_time,
                excluded=it.key in plan.excluded_ids,
            )
        )
    return rows


async def _plan(db, payload: CopyWeekPreviewRequest) -> CopyPlan:
    try:
        return await build_copy_plan(
            db,
            source_monday=payload.source_week_start,
            target_monday=payload.target_week_start,
            exclude_visit_ids=set(payload.exclude_visit_ids),
            fill_from_fixed=payload.fill_from_fixed,
        )
    except CopyWeekError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/v2/copy-week/sources",
    response_model=CopyWeekSourcesResponse,
    summary="週のコピー: 写す元の候補週 (read-only)",
)
async def copy_week_sources(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    target_week_start: date = Query(...),
) -> CopyWeekSourcesResponse:
    target = monday_of(target_week_start)
    stats = await list_source_weeks(db, target)
    return CopyWeekSourcesResponse(
        target_week_start=target,
        items=[
            CopyWeekSourceItem(
                week_start=s.week_start,
                visits=s.visits,
                patients=s.patients,
                cancelled=s.cancelled,
                unplanned=s.unplanned,
                qr_arrivals=s.qr_arrivals,
                holidays=[HolidayRead(date=d, name=n) for d, n in s.holidays],
            )
            for s in stats
        ],
    )


@router.post(
    "/v2/copy-week/preview",
    response_model=CopyWeekPreviewResponse,
    summary="週のコピー: 確認画面の内容 (read-only・DB 不変)",
)
async def copy_week_preview(
    payload: CopyWeekPreviewRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> CopyWeekPreviewResponse:
    # 読むだけ (build_copy_plan は SELECT のみ・commit しない)
    return _preview_from_plan(await _plan(db, payload))


@router.post(
    "/v2/copy-week",
    response_model=CopyWeekResponse,
    summary="週のコピー: 実行 (confirm: true 必須・admin)",
)
async def copy_week(
    payload: CopyWeekRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> CopyWeekResponse:
    if payload.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="確認のうえ confirm: true を付けて実行してください",
        )
    iso = payload.target_week_start.isocalendar()
    lock = _get_assign_staff_only_lock(iso.year, iso.week)
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "この週で自動スタッフ割当または週のコピーを実行中です。"
                "完了までお待ちください（同じ週への同時実行はできません）。"
            ),
        )
    async with lock:
        return await _copy_week_locked(payload, db, user)


async def _copy_week_locked(payload: CopyWeekRequest, db, user: User) -> CopyWeekResponse:
    plan = await _plan(db, payload)
    iso = plan.target_monday.isocalendar()
    try:
        snap = await snapshot_week(
            db, plan.target_monday, kind=SNAPSHOT_KIND_COPY_WEEK, user_id=user.id
        )
        snapshot_id = snap.id  # commit 後に ORM 属性を読まない (expire 対策)
        # 「コピー前に戻す」を出してよいかの判断材料 (足すだけの週は戻せない)。
        snap.payload = {**snap.payload, "copy_mode": plan.mode}
        result = await apply_copy_plan(db, plan)
        # 週生成と同じく、マネージャーの M コースと既定の同行・固定イベントを入れる
        # (同行・イベントは写さず、今までどおり既定から展開する §4-3)。
        await _ensure_manager_courses_for_week(db, iso.year, iso.week, office_id=None)
        await expand_accompaniment_defaults(db, iso.year, iso.week)
        await expand_staff_event_defaults(db, iso.year, iso.week)
        db.add(
            AuditLog(
                actor_user_id=user.id,
                action="schedule_copy_week",
                target_table="visits",
                target_id=plan.target_monday.isoformat(),
                before={
                    "snapshot_id": str(snapshot_id),
                    "existing": dict(plan.existing),
                },
                after={
                    "source_week_start": plan.source_monday.isoformat(),
                    "target_week_start": plan.target_monday.isoformat(),
                    "mode": plan.mode,
                    "created": result.created,
                    "filled": result.filled,
                    "replaced": result.replaced,
                    "skipped": dict(plan.skipped),
                    "courses_created": result.courses_created,
                    "courses_cleared": result.courses_cleared,
                    "staff_mirrored": result.staff_mirrored,
                    "needs_manual_staff": len(plan.needs_manual_staff),
                    "options": {
                        "exclude_visit_ids": sorted(str(i) for i in payload.exclude_visit_ids),
                        "fill_from_fixed": payload.fill_from_fixed,
                        "assign_staff": payload.assign_staff,
                    },
                },
            )
        )
        await db.flush()
    except Exception:
        await db.rollback()
        raise

    assign_result: AssignStaffOnlyResponse | None = None
    if payload.assign_staff:
        # 割当の確定と同じ commit でコピーも確定する。割当が失敗したら
        # _assign_staff_only_impl が rollback するのでコピーも残らない。
        try:
            assign_result = await _assign_staff_only_impl(
                AssignStaffOnlyRequest(iso_year=iso.year, iso_week=iso.week), db
            )
        except HTTPException as exc:
            await db.rollback()  # impl も rollback 済み。念のため (何も残さない)
            raise HTTPException(
                status_code=exc.status_code,
                detail=(
                    "自動スタッフ割当に失敗したため、コピーも取り消しました: "
                    f"{_detail_text(exc.detail)}"
                ),
            ) from exc
    else:
        await db.commit()

    actual = _counts(plan)
    return CopyWeekResponse(
        source_week_start=plan.source_monday,
        target_week_start=plan.target_monday,
        mode=plan.mode,  # type: ignore[arg-type]
        created=result.created,
        filled=result.filled,
        replaced=result.replaced,
        skipped=CopySkipCounts(**plan.skipped),
        courses_created=result.courses_created,
        courses_cleared=result.courses_cleared,
        staff_mirrored=result.staff_mirrored,
        snapshot_id=snapshot_id,
        restorable=plan.mode == "replace",
        needs_manual_staff=_needs_manual(plan),
        actual_counts=actual,
        expected_counts=payload.expected_counts,
        differs_from_preview=(
            payload.expected_counts is not None and payload.expected_counts != actual
        ),
        assign_result=assign_result,
    )
