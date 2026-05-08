"""Patient fixed-visit pattern endpoints (W9-BE1 / W9-BE2).

GET    /patients/{patient_id}/fixed-visits[?mode=normal|special]
PUT    /patients/{patient_id}/fixed-visits   body: PatientFixedVisitsBulkPut
DELETE /patients/{patient_id}/fixed-visits?mode=normal|special
POST   /patients/{patient_id}/fixed-visits/from-week  (W9-BE2: 個別固定化)
POST   /patients/fixed-visits/from-week-bulk          (W9-BE2: 全患者一括固定化)

RBAC:
  GET             — admin / manager / staff (staff は担当患者のみ)
  PUT             — admin / manager のみ
  DELETE          — admin / manager のみ
  from-week       — admin / manager のみ
  from-week-bulk  — admin / manager のみ

UNIQUE (patient_id, mode, weekday) 違反は 409 Conflict。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.course import Course
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.v2.patient_fixed_visit import (
    PatientFixedVisitMode,
    PatientFixedVisitsBulkPut,
    PatientFixedVisitV2Read,
)
from app.services.scheduling.layer1_expander import _is_special_week_active

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _ensure_patient_exists(db: AsyncSession, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


async def _staff_owns_patient(db: AsyncSession, *, staff_id: UUID, patient_id: UUID) -> bool:
    """staff_id が patient_id の担当者であるかを判定する (W7-BE1 パターン流用).

    判定条件 (どちらか一方でも該当すれば True):
      1. visits.primary_staff_id = staff_id の visit が当該患者に存在
      2. visit_staff_assignments 経由で staff_id が当該患者の visit に
         アサインされている

    soft-delete された visit (deleted_at IS NOT NULL) は除外する。
    """
    stmt_primary = (
        select(Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Visit.primary_staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    if await db.scalar(stmt_primary) is not None:
        return True

    stmt_assign = (
        select(VisitStaffAssignment.staff_id)
        .join(Visit, Visit.id == VisitStaffAssignment.visit_id)
        .where(
            Visit.patient_id == patient_id,
            VisitStaffAssignment.staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    return await db.scalar(stmt_assign) is not None


async def _check_read_access(db: AsyncSession, user: User, patient_id: UUID) -> None:
    """admin/manager は全患者可; staff は担当患者のみ可 (範囲外は 403)."""
    if user.role in {"admin", "manager"}:
        return
    if user.role == "staff":
        if user.staff_id is None or not await _staff_owns_patient(
            db, staff_id=user.staff_id, patient_id=patient_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff is not assigned to this patient",
            )
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


async def _commit_or_409(db: AsyncSession) -> None:
    """Commit し、UNIQUE 違反を 409 に変換する."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                # W37 Phase 1: UNIQUE は (patient_id, mode, weekday, slot_index).
                detail="Conflict: duplicate (patient_id, mode, weekday, slot_index)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{patient_id}/fixed-visits",
    response_model=list[PatientFixedVisitV2Read],
    summary="患者の固定訪問パターン一覧取得",
)
async def get_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    mode: PatientFixedVisitMode | None = Query(
        default=None, description="フィルタ: normal / special。未指定は両方。"
    ),
) -> list[PatientFixedVisitV2Read]:
    await _ensure_patient_exists(db, patient_id)
    await _check_read_access(db, user, patient_id)

    stmt = (
        select(PatientFixedVisit)
        .where(PatientFixedVisit.patient_id == patient_id)
        .order_by(
            PatientFixedVisit.mode,
            PatientFixedVisit.weekday,
            PatientFixedVisit.slot_index,
        )
    )
    if mode is not None:
        stmt = stmt.where(PatientFixedVisit.mode == mode)

    rows = (await db.scalars(stmt)).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]


@router.put(
    "/{patient_id}/fixed-visits",
    response_model=list[PatientFixedVisitV2Read],
    summary="患者の固定訪問パターン一括上書き (admin/manager のみ)",
)
async def put_fixed_visits(
    patient_id: UUID,
    body: PatientFixedVisitsBulkPut,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> list[PatientFixedVisitV2Read]:
    await _ensure_patient_exists(db, patient_id)

    # 1 TX: 当該 (patient_id, mode) を DELETE → INSERT
    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == body.mode,
        )
    )
    for item in body.items:
        db.add(
            PatientFixedVisit(
                patient_id=patient_id,
                mode=body.mode,
                weekday=item.weekday,
                start_time=item.start_time,
                duration_min=item.duration_min,
                # W22 Phase A: course_template_id (任意) を保存. 未指定なら NULL.
                course_template_id=item.course_template_id,
                # W37 Phase 1: slot_index (default 0) を反映. 1 名体制では常に 0.
                slot_index=item.slot_index,
            )
        )
    await _commit_or_409(db)

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == body.mode,
            )
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]


@router.delete(
    "/{patient_id}/fixed-visits",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="患者の固定訪問パターン削除 (mode 指定必須; admin/manager のみ)",
)
async def delete_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    mode: PatientFixedVisitMode = Query(..., description="削除する mode: normal または special"),
) -> None:
    await _ensure_patient_exists(db, patient_id)

    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == mode,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# W9-BE2: 全患者一括固定化 (collection-level; {id} より先に登録すること)
# ---------------------------------------------------------------------------


@router.post(
    "/fixed-visits/from-week-bulk",
    summary="全患者一括固定化 (admin/manager のみ)",
    status_code=status.HTTP_200_OK,
)
async def from_week_bulk(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(...),
    iso_week: int = Query(...),
    mode: PatientFixedVisitMode | None = Query(default=None),
) -> dict:
    """全 active 患者に対し当該週 visits を patient_fixed_visits に書き戻す.

    冪等性保証: 既存 (patient_id, mode) 全削除 → INSERT を 1 TX で実行。
    """
    # 全 active 患者を取得
    patients_rows = (
        await db.scalars(
            select(Patient).where(
                Patient.status == "active",
                Patient.deleted_at.is_(None),
            )
        )
    ).all()

    # 当該週の月曜を導出 (weekday 計算用)
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc

    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    updated_patient_ids: list[UUID] = []

    for patient in patients_rows:
        # mode 自動推定
        effective_mode: str = mode or (
            "special" if _is_special_week_active(patient, iso_year, iso_week) else "normal"
        )

        # 当該週の visits 取得 (deleted_at IS NULL)
        visit_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient.id,
                    Visit.visit_date >= week_monday,
                    Visit.visit_date <= week_sunday,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()

        # weekday 重複チェック
        weekday_counts: dict[int, int] = {}
        for v in visit_rows:
            wd = v.visit_date.weekday()
            weekday_counts[wd] = weekday_counts.get(wd, 0) + 1
        dup_weekdays = [wd for wd, cnt in weekday_counts.items() if cnt > 1]
        if dup_weekdays:
            # 一括処理ではエラーをスキップして次患者へ (bulk は best-effort)
            continue

        # 既存削除
        await db.execute(
            delete(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == effective_mode,
            )
        )

        # INSERT
        for v in visit_rows:
            wd = v.visit_date.weekday()
            duration_min = int(
                (v.end_time.hour * 60 + v.end_time.minute)
                - (v.start_time.hour * 60 + v.start_time.minute)
            )
            if duration_min <= 0:
                duration_min = 30
            # W22: visit.course_id → course.template_id を逆引きして course_template_id を保存
            visit_course_template_id = None
            if v.course_id is not None:
                course = await db.scalar(select(Course).where(Course.id == v.course_id))
                if course is not None:
                    visit_course_template_id = course.template_id
            db.add(
                PatientFixedVisit(
                    patient_id=patient.id,
                    mode=effective_mode,
                    weekday=wd,
                    start_time=v.start_time,
                    duration_min=duration_min,
                    course_template_id=visit_course_template_id,
                )
            )

        updated_patient_ids.append(patient.id)

    await db.commit()

    return {
        "updated_count": len(updated_patient_ids),
        "patients": [str(pid) for pid in updated_patient_ids],
    }


# ---------------------------------------------------------------------------
# W9-BE2: 個別固定化 API
# ---------------------------------------------------------------------------


@router.post(
    "/{patient_id}/fixed-visits/from-week",
    response_model=list[PatientFixedVisitV2Read],
    summary="当該週の visits を patient_fixed_visits に書き戻し (admin/manager のみ)",
)
async def from_week(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: int = Query(...),
    iso_week: int = Query(...),
    mode: PatientFixedVisitMode | None = Query(default=None),
) -> list[PatientFixedVisitV2Read]:
    """当該週の visits を patient_fixed_visits に書き戻す.

    mode 未指定: patient.special_week_active に当該週があれば 'special'、なければ 'normal'。
    visit_staff_assignments は読まない (時刻 + duration のみ)。
    visits が 0 件 → 既存 fixed_visits を削除して空配列返す。
    同一 weekday に複数 visit → 409。
    """
    patient = await _ensure_patient_exists(db, patient_id)

    # mode 自動推定
    effective_mode: str = mode or (
        "special" if _is_special_week_active(patient, iso_year, iso_week) else "normal"
    )

    # 当該週の月曜 / 日曜を導出
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc

    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    # 当該週の visits 取得 (deleted_at IS NULL)
    visit_rows = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient_id,
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()

    # 同一 weekday 重複チェック → 409
    weekday_counts: dict[int, int] = {}
    for v in visit_rows:
        wd = v.visit_date.weekday()
        weekday_counts[wd] = weekday_counts.get(wd, 0) + 1
    dup_weekdays = [wd for wd, cnt in weekday_counts.items() if cnt > 1]
    if dup_weekdays:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate visits on weekday(s) {dup_weekdays} for patient {patient_id}",
        )

    # 1 TX: 既存削除 → INSERT
    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == effective_mode,
        )
    )

    for v in visit_rows:
        wd = v.visit_date.weekday()
        duration_min = int(
            (v.end_time.hour * 60 + v.end_time.minute)
            - (v.start_time.hour * 60 + v.start_time.minute)
        )
        if duration_min <= 0:
            duration_min = 30
        # W22: visit.course_id → course.template_id を逆引きして course_template_id を保存
        visit_course_template_id = None
        if v.course_id is not None:
            course = await db.scalar(select(Course).where(Course.id == v.course_id))
            if course is not None:
                visit_course_template_id = course.template_id
        db.add(
            PatientFixedVisit(
                patient_id=patient_id,
                mode=effective_mode,
                weekday=wd,
                start_time=v.start_time,
                duration_min=duration_min,
                course_template_id=visit_course_template_id,
            )
        )

    await _commit_or_409(db)

    # 結果を返す
    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == effective_mode,
            )
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]
