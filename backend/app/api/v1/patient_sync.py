"""Patient sync endpoints (今週 visits → 固定枠 個別反映).

POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed
    指定週 (iso_year, iso_week) の対象患者 active visits を patient_fixed_visits
    (mode='normal', slot_index=0) に upsert する。

設計判断 (apply-individual との差分):
  * apply_individual_proposal は提案に無い weekday の既存 PFV を **削除** するが、
    本エンドポイントは「今週の visit に無い weekday は触らない」運用ユースケース
    (= 患者ごとに固定枠を段階的に更新したい) を満たすため、削除を行わない。
  * 個別反映 = 1 患者のみが対象 (atomic; この患者の PFV だけを 1 TX で commit).
  * dry_run=True (default) では DB を変更せず diff のみを返す.

RBAC: admin / manager のみ (staff は 403).

Wave Next 1 cross-review (Codex/Opus) HIGH 5 件 + MEDIUM 4 件対応 (2026-05-18):
  * H1: visit.course_id is None でも既存 PFV の ``course_template_id`` を
        fallback で保持する (= NULL 上書きしない).
  * H2: ``requires_multiple_staff=True`` patient で当該 weekday に
        active visit が複数 (= multi-staff pair) ある場合は当該 weekday を
        ``operation="skipped"`` + ``reason="multi_staff_not_supported"`` で
        スキップ (slot 0/1 ペア対応は将来 Phase で実装).
  * M1: ``Course`` soft-delete (``deleted_at IS NOT NULL``) 行は course_template
        逆引きに使わない (= ct_id 解決不能扱い → H1 の fallback が効く).
  * M3: 今週に visit が無い既存 PFV を ``untouched_existing`` で返す (FE で
        「今週 visit なし・PFV 残存」の zombie 候補を可視化できる).
  * M4: DB の deadlock / serialization failure (PG SQLSTATE 40P01 / 40001 /
        55P03) を 503 にマップしてリトライを促す.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from datetime import time as time_cls
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.schemas.v2.patient_sync import (
    BulkApplyWeekOnlyVisitChangesRequest,
    BulkApplyWeekOnlyVisitChangesResponse,
    BulkSyncWeekToFixedItem,
    BulkSyncWeekToFixedRequest,
    BulkSyncWeekToFixedResponse,
    BulkWeekOnlyChangeOutcome,
    SyncChangeEntry,
    SyncPfvSnapshot,
    SyncWeekToFixedRequest,
    SyncWeekToFixedResponse,
    SyncWeekToFixedSummary,
)
from app.services.scheduling.auto_allocator_v2 import _address_bucket

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _snapshot_from_pfv(pfv: PatientFixedVisit) -> SyncPfvSnapshot:
    return SyncPfvSnapshot(
        weekday=pfv.weekday,
        start_time=pfv.start_time,
        duration_min=pfv.duration_min,
        course_template_id=pfv.course_template_id,
    )


def _snapshot_from_visit(
    *,
    weekday: int,
    start_time: time_cls,
    duration_min: int,
    course_template_id: UUID | None,
) -> SyncPfvSnapshot:
    return SyncPfvSnapshot(
        weekday=weekday,
        start_time=start_time,
        duration_min=duration_min,
        course_template_id=course_template_id,
    )


def _calc_duration_min(start_t: time_cls, end_t: time_cls) -> int:
    """end_t > start_t を想定して分単位の差を返す. defensive に 1..480 に clamp."""
    start_total = start_t.hour * 60 + start_t.minute
    end_total = end_t.hour * 60 + end_t.minute
    diff = end_total - start_total
    if diff <= 0:
        return 30
    if diff > 480:
        return 480
    return diff


def _is_unchanged(old: PatientFixedVisit, new_snap: SyncPfvSnapshot) -> bool:
    """PFV の比較キー: (start_time, duration_min, course_template_id)."""
    return (
        old.start_time == new_snap.start_time
        and old.duration_min == new_snap.duration_min
        and old.course_template_id == new_snap.course_template_id
    )


# H2: multi-staff pair が触れない理由を表す reason 文字列.
_REASON_MULTI_STAFF = "multi_staff_not_supported"

# M4: 一時的なロック / serialization 衝突として 503 にマップする SQLSTATE.
_TRANSIENT_DB_SQLSTATES = frozenset({"40P01", "40001", "55P03"})


def _is_transient_db_error(exc: OperationalError) -> bool:
    """PostgreSQL の deadlock / serialization failure / lock_not_available か判定."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if isinstance(sqlstate, str) and sqlstate in _TRANSIENT_DB_SQLSTATES:
        return True
    return False


# ---------------------------------------------------------------------------
# Shared core: compute & apply a single patient's week→PFV sync.
#
# 既存の単発エンドポイントと bulk エンドポイントの両方から呼び出される.
# 呼び出し側で flush / commit / rollback を制御する (本関数は flush も呼ばない).
# ---------------------------------------------------------------------------


async def _compute_sync_diff_for_patient(
    *,
    db,
    patient: Patient,
    iso_year: int,
    iso_week: int,
    lock_pfv: bool,
) -> tuple[
    list[SyncChangeEntry],
    list[SyncPfvSnapshot],
    SyncWeekToFixedSummary,
    dict[int, PatientFixedVisit],
]:
    """1 患者分の sync diff を計算する (DB は変更しない).

    Returns:
        (changes, untouched_existing, summary, existing_by_wd)
        existing_by_wd は呼び出し側が apply 時に update 操作で参照するため返す.
    """
    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    requires_multi = bool(getattr(patient, "requires_multiple_staff", False))

    visit_rows = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
    ).all()

    # course_id → CourseTemplate.id を 1 度に解決 (N+1 回避; soft-deleted course は除外).
    course_ids = {v.course_id for v in visit_rows if v.course_id is not None}
    course_template_by_course: dict[UUID, UUID | None] = {}
    if course_ids:
        from app.models.course import Course

        course_rows = (
            await db.scalars(
                select(Course).where(Course.id.in_(course_ids), Course.deleted_at.is_(None))
            )
        ).all()
        course_template_by_course = {c.id: c.template_id for c in course_rows}

    pfv_stmt = select(PatientFixedVisit).where(
        PatientFixedVisit.patient_id == patient.id,
        PatientFixedVisit.mode == "normal",
        PatientFixedVisit.slot_index == 0,
    )
    if lock_pfv:
        pfv_stmt = pfv_stmt.with_for_update()
    existing_rows = (await db.scalars(pfv_stmt)).all()
    existing_by_wd: dict[int, PatientFixedVisit] = {p.weekday: p for p in existing_rows}

    visits_by_wd: dict[int, list[Visit]] = {}
    for v in visit_rows:
        visits_by_wd.setdefault(v.visit_date.weekday(), []).append(v)

    proposed_by_wd: dict[int, SyncPfvSnapshot] = {}
    skipped_by_wd: dict[int, str] = {}

    for wd, visits in visits_by_wd.items():
        if requires_multi and len(visits) >= 2:
            skipped_by_wd[wd] = _REASON_MULTI_STAFF
            continue
        v = visits[0]
        dur = _calc_duration_min(v.start_time, v.end_time)
        ct_id: UUID | None
        if v.course_id is not None and v.course_id in course_template_by_course:
            ct_id = course_template_by_course[v.course_id]
        else:
            existing = existing_by_wd.get(wd)
            ct_id = existing.course_template_id if existing is not None else None
        proposed_by_wd[wd] = _snapshot_from_visit(
            weekday=wd,
            start_time=v.start_time,
            duration_min=dur,
            course_template_id=ct_id,
        )

    changes: list[SyncChangeEntry] = []
    pfv_inserted = 0
    pfv_updated = 0
    pfv_unchanged = 0
    pfv_skipped = 0

    for wd in sorted(skipped_by_wd.keys()):
        changes.append(
            SyncChangeEntry(
                weekday=wd,
                operation="skipped",
                old=(_snapshot_from_pfv(existing_by_wd[wd]) if wd in existing_by_wd else None),
                new=None,
                reason=skipped_by_wd[wd],
            )
        )
        pfv_skipped += 1

    for wd, new_snap in sorted(proposed_by_wd.items()):
        ex = existing_by_wd.get(wd)
        if ex is None:
            changes.append(SyncChangeEntry(weekday=wd, operation="insert", old=None, new=new_snap))
            pfv_inserted += 1
            continue
        if _is_unchanged(ex, new_snap):
            changes.append(
                SyncChangeEntry(
                    weekday=wd,
                    operation="unchanged",
                    old=_snapshot_from_pfv(ex),
                    new=new_snap,
                )
            )
            pfv_unchanged += 1
            continue
        changes.append(
            SyncChangeEntry(
                weekday=wd,
                operation="update",
                old=_snapshot_from_pfv(ex),
                new=new_snap,
            )
        )
        pfv_updated += 1

    touched_weekdays = {c.weekday for c in changes}
    untouched_existing: list[SyncPfvSnapshot] = [
        _snapshot_from_pfv(ex)
        for wd, ex in sorted(existing_by_wd.items())
        if wd not in touched_weekdays
    ]

    summary = SyncWeekToFixedSummary(
        pfv_inserted=pfv_inserted,
        pfv_updated=pfv_updated,
        pfv_unchanged=pfv_unchanged,
        pfv_skipped=pfv_skipped,
    )
    return changes, untouched_existing, summary, existing_by_wd


def _apply_sync_changes(
    *,
    db,
    patient_id: UUID,
    changes: list[SyncChangeEntry],
    existing_by_wd: dict[int, PatientFixedVisit],
    now: datetime,
) -> None:
    """sync diff の changes を実 PFV に反映する (insert / update のみ).

    flush / commit は呼び出し側で制御する.

    Wave U-0 (§2.2-3): INSERT 経路で ``is_pinned`` / ``movability`` を、同一
    (weekday, start_time) の既存 PFV から引き継ぐ. 現行の diff ロジックでは同一
    weekday の既存 PFV は UPDATE 経路 (= 行を in-place で更新するので pin は保持)
    に振り分けられるため、この引き継ぎは防御的だが、pin 状態が silent に False へ
    落ちる回帰を将来にわたって防ぐ. UPDATE 経路は元々 ``is_pinned`` /
    ``movability`` を touch しないため保持済み.
    """
    for change in changes:
        if change.operation in ("unchanged", "skipped"):
            continue
        if change.new is None:
            continue
        if change.operation == "insert":
            # 同一 weekday の既存 PFV (slot_index=0 の 1 行/weekday) があれば
            # pin / 可動域を引き継ぐ.
            prior = existing_by_wd.get(change.new.weekday)
            inherit_pin = bool(prior.is_pinned) if prior is not None else False
            inherit_mov = prior.movability if prior is not None else "unknown"
            new_pfv = PatientFixedVisit(
                patient_id=patient_id,
                mode="normal",
                weekday=change.new.weekday,
                start_time=change.new.start_time,
                duration_min=change.new.duration_min,
                slot_index=0,
                course_template_id=change.new.course_template_id,
                is_pinned=inherit_pin,
                movability=inherit_mov,
            )
            db.add(new_pfv)
        elif change.operation == "update":
            ex = existing_by_wd[change.new.weekday]
            ex.start_time = change.new.start_time
            ex.duration_min = change.new.duration_min
            ex.course_template_id = change.new.course_template_id
            ex.updated_at = now


# ---------------------------------------------------------------------------
# POST /patients/{patient_id}/sync-week-visits-to-fixed
# ---------------------------------------------------------------------------


@router.post(
    "/{patient_id}/sync-week-visits-to-fixed",
    response_model=SyncWeekToFixedResponse,
    status_code=status.HTTP_200_OK,
    summary="今週の visits を固定枠 (PFV) に個別反映する (dry_run / apply)",
)
async def sync_week_visits_to_fixed_endpoint(
    patient_id: UUID,
    payload: SyncWeekToFixedRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> SyncWeekToFixedResponse:
    """指定週の患者 active visits を PFV (mode='normal', slot_index=0) に upsert.

    * 今週に visit が無い weekday は **触らない** (apply_individual_proposal とは異なる).
    * dry_run=True (default) ではコミットせず diff のみ返す.
    * 1 weekday に 2 件以上の active visit がある場合は、最も早い start_time の 1 件を採用.
    * H2: ``requires_multiple_staff=True`` patient で当該 weekday に visit が
      2 件以上ある場合 (= multi-staff pair) は当該 weekday を skip する.
    """
    # 1) week_monday / week_sunday を解決
    try:
        week_monday = date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc
    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    # 2) 患者の存在確認 (soft-delete 済みは 404)
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"patient_id={patient_id} が見つかりません",
        )

    requires_multi = bool(getattr(patient, "requires_multiple_staff", False))

    # 3) 今週 visits を取得 (active のみ).
    #    course_id 経由で course_template_id を引けるよう Course を join せず、
    #    後段の処理を簡略化するため courses を別途 lookup する.
    visit_rows = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.patient_id == patient_id,
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
    ).all()

    # course_id → CourseTemplate.id を 1 度に解決 (N+1 回避).
    # M1: soft-deleted course (deleted_at IS NOT NULL) は除外 → 該当 course の
    # ct_id は解決不能扱いとなり、後段 H1 の fallback で既存 PFV の ct_id を保持する.
    course_ids = {v.course_id for v in visit_rows if v.course_id is not None}
    course_template_by_course: dict[UUID, UUID | None] = {}
    if course_ids:
        from app.models.course import Course

        course_rows = (
            await db.scalars(
                select(Course).where(Course.id.in_(course_ids), Course.deleted_at.is_(None))
            )
        ).all()
        course_template_by_course = {c.id: c.template_id for c in course_rows}

    # 4) 既存 PFV (mode='normal', slot_index=0) を取得.
    #    apply 経路 (dry_run=False) ではトランザクション境界の防御のため
    #    with_for_update() で行ロックする.
    pfv_stmt = select(PatientFixedVisit).where(
        PatientFixedVisit.patient_id == patient_id,
        PatientFixedVisit.mode == "normal",
        PatientFixedVisit.slot_index == 0,
    )
    if not payload.dry_run:
        pfv_stmt = pfv_stmt.with_for_update()
    existing_rows = (await db.scalars(pfv_stmt)).all()
    existing_by_wd: dict[int, PatientFixedVisit] = {p.weekday: p for p in existing_rows}

    # 5) weekday ごとに採用判定 (H2: multi-staff pair は skip).
    #    visits は (visit_date, start_time) 昇順なので、weekday 内最初の visit が最早 start.
    visits_by_wd: dict[int, list[Visit]] = {}
    for v in visit_rows:
        visits_by_wd.setdefault(v.visit_date.weekday(), []).append(v)

    proposed_by_wd: dict[int, SyncPfvSnapshot] = {}
    skipped_by_wd: dict[int, str] = {}  # H2: skip 理由を保持

    for wd, visits in visits_by_wd.items():
        # H2: requires_multiple_staff=True patient で当該 weekday に visit が
        # 2 件以上ある場合は multi-staff pair (visit_group_id 共有想定) と判断し skip.
        if requires_multi and len(visits) >= 2:
            skipped_by_wd[wd] = _REASON_MULTI_STAFF
            continue

        # 最早 start_time の 1 件を採用 (既に並び替え済み).
        v = visits[0]
        dur = _calc_duration_min(v.start_time, v.end_time)

        # H1: visit に course なし or soft-deleted course の場合は、既存 PFV の
        # course_template_id を fallback として保持する (= NULL 上書きしない).
        ct_id: UUID | None
        if v.course_id is not None and v.course_id in course_template_by_course:
            ct_id = course_template_by_course[v.course_id]
        else:
            # visit の course が無い or soft-deleted な場合 → 既存 PFV の ct_id を維持.
            existing = existing_by_wd.get(wd)
            ct_id = existing.course_template_id if existing is not None else None

        proposed_by_wd[wd] = _snapshot_from_visit(
            weekday=wd,
            start_time=v.start_time,
            duration_min=dur,
            course_template_id=ct_id,
        )

    # 6) 各 weekday を分類 (insert / update / unchanged / skipped).
    changes: list[SyncChangeEntry] = []
    pfv_inserted = 0
    pfv_updated = 0
    pfv_unchanged = 0
    pfv_skipped = 0

    # H2: skipped weekday を最初に積む (weekday 昇順).
    for wd in sorted(skipped_by_wd.keys()):
        changes.append(
            SyncChangeEntry(
                weekday=wd,
                operation="skipped",
                old=(_snapshot_from_pfv(existing_by_wd[wd]) if wd in existing_by_wd else None),
                new=None,
                reason=skipped_by_wd[wd],
            )
        )
        pfv_skipped += 1

    for wd, new_snap in sorted(proposed_by_wd.items()):
        ex = existing_by_wd.get(wd)
        if ex is None:
            changes.append(SyncChangeEntry(weekday=wd, operation="insert", old=None, new=new_snap))
            pfv_inserted += 1
            continue
        if _is_unchanged(ex, new_snap):
            changes.append(
                SyncChangeEntry(
                    weekday=wd,
                    operation="unchanged",
                    old=_snapshot_from_pfv(ex),
                    new=new_snap,
                )
            )
            pfv_unchanged += 1
            continue
        changes.append(
            SyncChangeEntry(
                weekday=wd,
                operation="update",
                old=_snapshot_from_pfv(ex),
                new=new_snap,
            )
        )
        pfv_updated += 1

    # M3: 今週 visit が無い (= changes に登場しなかった) 既存 PFV を untouched_existing で返す.
    #     ただし H2 で skipped 扱いになった weekday の既存 PFV は changes に old として
    #     既出のため untouched_existing に重複させない.
    touched_weekdays = {c.weekday for c in changes}
    untouched_existing: list[SyncPfvSnapshot] = [
        _snapshot_from_pfv(ex)
        for wd, ex in sorted(existing_by_wd.items())
        if wd not in touched_weekdays
    ]

    summary = SyncWeekToFixedSummary(
        pfv_inserted=pfv_inserted,
        pfv_updated=pfv_updated,
        pfv_unchanged=pfv_unchanged,
        pfv_skipped=pfv_skipped,
    )

    # 7) dry_run なら ここで return (DB 変更なし).
    if payload.dry_run:
        return SyncWeekToFixedResponse(
            patient_id=patient_id,
            summary=summary,
            changes=changes,
            untouched_existing=untouched_existing,
            transaction_applied=False,
        )

    # 8) apply: insert / update のみを実行. unchanged / skipped は no-op.
    #    触らない weekday も no-op.
    now = datetime.now(tz=UTC)
    try:
        for change in changes:
            if change.operation in ("unchanged", "skipped"):
                continue
            if change.new is None:
                # 防御: skipped 以外で new=None はあり得ないが、型上は Optional のため。
                continue
            if change.operation == "insert":
                new_pfv = PatientFixedVisit(
                    patient_id=patient_id,
                    mode="normal",
                    weekday=change.new.weekday,
                    start_time=change.new.start_time,
                    duration_min=change.new.duration_min,
                    slot_index=0,
                    course_template_id=change.new.course_template_id,
                )
                db.add(new_pfv)
            elif change.operation == "update":
                ex = existing_by_wd[change.new.weekday]
                ex.start_time = change.new.start_time
                ex.duration_min = change.new.duration_min
                ex.course_template_id = change.new.course_template_id
                ex.updated_at = now
        await db.flush()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "sync_week_visits_to_fixed: integrity error (likely concurrent sync): "
            "patient=%s err=%s",
            patient_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ患者の固定枠を更新中です。もう一度実行してください。",
        ) from exc
    except OperationalError as exc:
        # M4: PostgreSQL の deadlock / serialization failure / lock_not_available は
        # 一時的な競合なので 503 にマップしてクライアントにリトライを促す.
        await db.rollback()
        if _is_transient_db_error(exc):
            logger.warning(
                "sync_week_visits_to_fixed: transient DB lock conflict: patient=%s err=%s",
                patient_id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DB が混雑しています、再度お試しください",
            ) from exc
        raise
    except Exception:
        await db.rollback()
        raise

    return SyncWeekToFixedResponse(
        patient_id=patient_id,
        summary=summary,
        changes=changes,
        untouched_existing=untouched_existing,
        transaction_applied=True,
    )


# ---------------------------------------------------------------------------
# POST /patients/bulk-sync-week-to-fixed (永続: PFV を 1 TX で複数 patient 反映)
# ---------------------------------------------------------------------------


@router.post(
    "/bulk-sync-week-to-fixed",
    response_model=BulkSyncWeekToFixedResponse,
    status_code=status.HTTP_200_OK,
    summary="複数患者の今週 visits → 固定枠 (PFV) を 1 TX で一括反映",
)
async def bulk_sync_week_visits_to_fixed_endpoint(
    payload: BulkSyncWeekToFixedRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> BulkSyncWeekToFixedResponse:
    """``sync_week_visits_to_fixed`` を複数 patient に対し 1 transaction で実行.

    atomic 性: 1 患者でも DB エラー (IntegrityError 等) が発生したら全 rollback.
    存在しない patient_id は ``results[].error`` に "not_found" を記録するが
    全体は失敗扱いにしない (部分成功を許容; ただし dry_run=False でも全成功
    した patient のみ commit される).
    """
    try:
        week_monday = date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}"),
        ) from exc
    # week_sunday は _compute_sync_diff_for_patient 内部で算出するため、ここでは
    # validation 目的でのみ呼ぶ (= week_monday の存在を担保).
    _ = week_monday

    unique_ids: list[UUID] = []
    seen: set[UUID] = set()
    for pid in payload.patient_ids:
        if pid in seen:
            continue
        seen.add(pid)
        unique_ids.append(pid)

    # 一度に全 patient を取得 (N+1 回避).
    patient_rows = (
        await db.scalars(
            select(Patient).where(Patient.id.in_(unique_ids), Patient.deleted_at.is_(None))
        )
    ).all()
    patients_by_id: dict[UUID, Patient] = {p.id: p for p in patient_rows}

    results: list[BulkSyncWeekToFixedItem] = []
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0
    total_skipped = 0
    errors: list[str] = []

    now = datetime.now(tz=UTC)
    try:
        for pid in unique_ids:
            patient = patients_by_id.get(pid)
            if patient is None:
                results.append(
                    BulkSyncWeekToFixedItem(
                        patient_id=pid,
                        summary=SyncWeekToFixedSummary(
                            pfv_inserted=0,
                            pfv_updated=0,
                            pfv_unchanged=0,
                            pfv_skipped=0,
                        ),
                        changes=[],
                        untouched_existing=[],
                        transaction_applied=False,
                        error="not_found",
                    )
                )
                continue

            (
                changes,
                untouched_existing,
                summary,
                existing_by_wd,
            ) = await _compute_sync_diff_for_patient(
                db=db,
                patient=patient,
                iso_year=payload.iso_year,
                iso_week=payload.iso_week,
                lock_pfv=not payload.dry_run,
            )

            if not payload.dry_run:
                _apply_sync_changes(
                    db=db,
                    patient_id=patient.id,
                    changes=changes,
                    existing_by_wd=existing_by_wd,
                    now=now,
                )

            results.append(
                BulkSyncWeekToFixedItem(
                    patient_id=patient.id,
                    summary=summary,
                    changes=changes,
                    untouched_existing=untouched_existing,
                    transaction_applied=(not payload.dry_run),
                    error=None,
                )
            )
            total_inserted += summary.pfv_inserted
            total_updated += summary.pfv_updated
            total_unchanged += summary.pfv_unchanged
            total_skipped += summary.pfv_skipped

        if not payload.dry_run:
            await db.flush()
            await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "bulk_sync_week_visits_to_fixed: integrity error: ids=%s err=%s",
            unique_ids,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ患者の固定枠を更新中です。もう一度実行してください。",
        ) from exc
    except OperationalError as exc:
        await db.rollback()
        if _is_transient_db_error(exc):
            logger.warning(
                "bulk_sync_week_visits_to_fixed: transient DB lock conflict: err=%s",
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DB が混雑しています、再度お試しください",
            ) from exc
        raise
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    return BulkSyncWeekToFixedResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        results=results,
        total_inserted=total_inserted,
        total_updated=total_updated,
        total_unchanged=total_unchanged,
        total_skipped=total_skipped,
        errors=errors,
        transaction_applied=(not payload.dry_run),
    )


# ---------------------------------------------------------------------------
# POST /patients/bulk-apply-week-only-visit-changes (週限定: visit のみ update; PFV 不変)
# ---------------------------------------------------------------------------


# 週限定 update を許可する visit.source (本番運用 visit を保護するため).
# patient_sync の bulk 反映は manual / 自動算出 v1 / v2 / reset-to-fixed 由来の
# visit のみ対象. 外部連携 (kaipoke 等) の visit は対象外。
# W41 v2.10 fix: reset_v2 (固定枠リセット由来) を whitelist に追加。
# これがないと「固定枠に戻す」直後の bulk 反映が全件 skipped になる重大バグ。
_WEEK_ONLY_BULK_ALLOWED_SOURCES = frozenset(
    {"manual", "auto_alloc_v1", "auto_alloc_v2", "auto_alloc_v2w", "reset_v2"}
)


@router.post(
    "/bulk-apply-week-only-visit-changes",
    response_model=BulkApplyWeekOnlyVisitChangesResponse,
    status_code=status.HTTP_200_OK,
    summary="今週の visits のみを (patient, weekday) 単位で一括 update (PFV 不変)",
)
async def bulk_apply_week_only_visit_changes_endpoint(
    payload: BulkApplyWeekOnlyVisitChangesRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> BulkApplyWeekOnlyVisitChangesResponse:
    """指定週の active visits を (patient, weekday) 単位で update する.

    - PFV は **一切変更しない** (= 「今週だけイレギュラー対応」モード).
    - 当該 weekday に active visit が無い場合は skipped (insert は新規 course 解決等が
      不要な簡易 UI 向け; INSERT は将来必要になったら別 endpoint で対応).
    - 1 TX 単位 atomic. 1 件でも DB エラーなら全 rollback.
    - 同 weekday に visit が複数ある場合は最早 start_time の 1 件を update.
    """
    try:
        week_monday = date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}"),
        ) from exc
    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    # (patient_id, weekday) 重複は最後の指定を採用.
    by_key: dict[tuple[UUID, int], object] = {}
    for ch in payload.patient_visit_changes:
        by_key[(ch.patient_id, ch.weekday)] = ch

    # 対象 patient_id を 1 度に取得し、active のみ通す.
    patient_ids = list({k[0] for k in by_key})
    patient_rows = (
        await db.scalars(
            select(Patient).where(Patient.id.in_(patient_ids), Patient.deleted_at.is_(None))
        )
    ).all()
    patients_by_id = {p.id: p for p in patient_rows}

    # 当該週の active visit を 1 度に取得.
    pfv_lock = not payload.dry_run
    visit_stmt = select(Visit).where(
        Visit.patient_id.in_(patient_ids),
        Visit.deleted_at.is_(None),
        Visit.visit_date >= week_monday,
        Visit.visit_date <= week_sunday,
    )
    if pfv_lock:
        visit_stmt = visit_stmt.with_for_update()
    visit_rows = (await db.scalars(visit_stmt)).all()

    visits_by_key: dict[tuple[UUID, int], list[Visit]] = {}
    for v in visit_rows:
        key = (v.patient_id, v.visit_date.weekday())
        visits_by_key.setdefault(key, []).append(v)
    # 各 key の visit を start_time 昇順に整列.
    for vs in visits_by_key.values():
        vs.sort(key=lambda x: (x.visit_date, x.start_time))

    # Fix D4 (CareFlow #103): 異住所同時刻衝突を境界検証するため、
    # 当該週の **他患者** active visit も読み込み、(visit_date, course_id,
    # start_time) → [Visit] の index を構築する. payload の patient_id 以外も
    # 含まれる点に注意 (= 1 患者だけ update して衝突が起きるケースを検出).
    # course_id が NULL の visit は「コース未割当」扱いで除外 (= 衝突判定の対象外).
    other_visits_stmt = select(Visit).where(
        Visit.deleted_at.is_(None),
        Visit.visit_date >= week_monday,
        Visit.visit_date <= week_sunday,
        Visit.course_id.is_not(None),
        # 「今週だけ取消」(週空間 Phase E / week-cockpit-design.md D1) 済みの枠は
        # 実施されない = その時刻を占有していない。衝突相手に数えると、取消した
        # だけで他患者の正当な配置が弾かれる。
        Visit.status != VISIT_STATUS_CANCELLED,
    )
    other_visit_rows = (await db.scalars(other_visits_stmt)).all()
    # patient_id → Patient 辞書 (住所判定用). payload 外の patient も含める.
    all_visit_patient_ids = list({v.patient_id for v in other_visit_rows})
    if all_visit_patient_ids:
        more_patient_rows = (
            await db.scalars(
                select(Patient).where(
                    Patient.id.in_(all_visit_patient_ids),
                    Patient.deleted_at.is_(None),
                )
            )
        ).all()
        for p in more_patient_rows:
            if p.id not in patients_by_id:
                patients_by_id[p.id] = p

    # (visit_date, course_id, start_time) で他患者 visit を引ける index.
    visits_by_slot: dict[tuple[date, UUID, time_cls], list[Visit]] = {}
    for v in other_visit_rows:
        if v.course_id is None:
            continue
        slot_key = (v.visit_date, v.course_id, v.start_time)
        visits_by_slot.setdefault(slot_key, []).append(v)

    outcomes: list[BulkWeekOnlyChangeOutcome] = []
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0
    total_skipped = 0
    errors: list[str] = []
    now = datetime.now(tz=UTC)

    try:
        for (pid, wd), ch in by_key.items():
            patient = patients_by_id.get(pid)
            if patient is None:
                outcomes.append(
                    BulkWeekOnlyChangeOutcome(
                        patient_id=pid,
                        weekday=wd,
                        operation="skipped",
                        reason="patient_not_found",
                    )
                )
                total_skipped += 1
                continue

            visits = visits_by_key.get((pid, wd), [])
            if not visits:
                outcomes.append(
                    BulkWeekOnlyChangeOutcome(
                        patient_id=pid,
                        weekday=wd,
                        operation="skipped",
                        reason="no_active_visit",
                    )
                )
                total_skipped += 1
                continue

            target_visit = visits[0]
            if target_visit.source not in _WEEK_ONLY_BULK_ALLOWED_SOURCES:
                outcomes.append(
                    BulkWeekOnlyChangeOutcome(
                        patient_id=pid,
                        weekday=wd,
                        operation="skipped",
                        visit_id=target_visit.id,
                        reason=f"source_not_allowed:{target_visit.source}",
                    )
                )
                total_skipped += 1
                continue

            # 比較: start_time / duration_min が一致なら unchanged.
            old_start = target_visit.start_time
            old_end = target_visit.end_time
            old_dur = _calc_duration_min(old_start, old_end)
            new_start = ch.start_time  # type: ignore[attr-defined]
            new_dur = int(ch.duration_min)  # type: ignore[attr-defined]
            if old_start == new_start and old_dur == new_dur:
                outcomes.append(
                    BulkWeekOnlyChangeOutcome(
                        patient_id=pid,
                        weekday=wd,
                        operation="unchanged",
                        old_start_time=old_start,
                        new_start_time=new_start,
                        old_duration_min=old_dur,
                        new_duration_min=new_dur,
                        visit_id=target_visit.id,
                    )
                )
                total_unchanged += 1
                continue

            # Fix D4 (CareFlow #103): 当該 update が異住所同時刻衝突を引き起こすかを検証.
            # 同 (visit_date, course_id, new_start_time) で他 patient_id の active visit が
            # 存在し、かつ住所バケット (= ``_address_bucket``) が異なるなら、本件のみ
            # skip + reason='same_time_conflict_other_patient' で記録 (atomic 維持).
            # 同住所ペア (家族・施設) は許容 (= 既存挙動).
            # course_id が None の visit はコース未割当のため衝突対象外.
            same_time_conflict = False
            conflict_other_pids: list[UUID] = []
            if target_visit.course_id is not None:
                same_slot_visits = visits_by_slot.get(
                    (target_visit.visit_date, target_visit.course_id, new_start),
                    [],
                )
                this_patient = patient
                this_bucket: tuple[float, float] | None = None
                if (
                    this_patient is not None
                    and this_patient.lat is not None
                    and this_patient.lng is not None
                ):
                    this_bucket = _address_bucket(float(this_patient.lat), float(this_patient.lng))
                for other_v in same_slot_visits:
                    # 自分自身 (target_visit) は除外.
                    if other_v.id == target_visit.id:
                        continue
                    if other_v.patient_id == pid:
                        continue
                    other_p = patients_by_id.get(other_v.patient_id)
                    if other_p is None or other_p.lat is None or other_p.lng is None:
                        # 住所不明な相手とは衝突扱い (安全側; マスター修正を促す).
                        same_time_conflict = True
                        conflict_other_pids.append(other_v.patient_id)
                        continue
                    other_bucket = _address_bucket(float(other_p.lat), float(other_p.lng))
                    if this_bucket is None or this_bucket != other_bucket:
                        same_time_conflict = True
                        conflict_other_pids.append(other_v.patient_id)

            if same_time_conflict:
                logger.warning(
                    "bulk_apply_week_only: cross-address same-time conflict: "
                    "patient=%s weekday=%s new_start=%s other_patients=%s",
                    pid,
                    wd,
                    new_start,
                    conflict_other_pids,
                )
                outcomes.append(
                    BulkWeekOnlyChangeOutcome(
                        patient_id=pid,
                        weekday=wd,
                        operation="skipped",
                        old_start_time=old_start,
                        new_start_time=new_start,
                        old_duration_min=old_dur,
                        new_duration_min=new_dur,
                        visit_id=target_visit.id,
                        reason="same_time_conflict_other_patient",
                    )
                )
                total_skipped += 1
                continue

            # update: start_time / end_time を上書き. PFV は一切触らない.
            new_end_total = new_start.hour * 60 + new_start.minute + new_dur
            if new_end_total >= 24 * 60:
                new_end_total = 23 * 60 + 59
            new_end = time_cls(new_end_total // 60, new_end_total % 60)

            if not payload.dry_run:
                target_visit.start_time = new_start
                target_visit.end_time = new_end
                target_visit.updated_at = now
                # Fix D4: 更新後の slot index を反映 (連続適用で次の候補が
                # 同じ slot を狙うケースを正しく検出するため).
                old_slot = (target_visit.visit_date, target_visit.course_id, old_start)
                new_slot = (target_visit.visit_date, target_visit.course_id, new_start)
                if old_slot in visits_by_slot:
                    visits_by_slot[old_slot] = [
                        v for v in visits_by_slot[old_slot] if v.id != target_visit.id
                    ]
                if target_visit.course_id is not None:
                    visits_by_slot.setdefault(new_slot, []).append(target_visit)

            outcomes.append(
                BulkWeekOnlyChangeOutcome(
                    patient_id=pid,
                    weekday=wd,
                    operation="update",
                    old_start_time=old_start,
                    new_start_time=new_start,
                    old_duration_min=old_dur,
                    new_duration_min=new_dur,
                    visit_id=target_visit.id,
                )
            )
            total_updated += 1

        if not payload.dry_run:
            await db.flush()
            await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "bulk_apply_week_only_visit_changes: integrity error: err=%s",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ visit を更新中です。もう一度実行してください。",
        ) from exc
    except OperationalError as exc:
        await db.rollback()
        if _is_transient_db_error(exc):
            logger.warning(
                "bulk_apply_week_only_visit_changes: transient DB lock conflict: err=%s",
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DB が混雑しています、再度お試しください",
            ) from exc
        raise
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    return BulkApplyWeekOnlyVisitChangesResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        outcomes=outcomes,
        total_inserted=total_inserted,
        total_updated=total_updated,
        total_unchanged=total_unchanged,
        total_skipped=total_skipped,
        errors=errors,
        transaction_applied=(not payload.dry_run),
    )


__all__ = ["router"]
