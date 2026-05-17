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
from app.models.visit import Visit
from app.schemas.v2.patient_sync import (
    SyncChangeEntry,
    SyncPfvSnapshot,
    SyncWeekToFixedRequest,
    SyncWeekToFixedResponse,
    SyncWeekToFixedSummary,
)

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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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


__all__ = ["router"]
