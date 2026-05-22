"""Visit CRUD endpoints (Phase 2 domain router, W2-BE4 拡張).

Staff role users may only see visits where they are assigned as primary,
secondary, or mentor staff; admin/manager see everything.

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.3 / §4.5):
  - レスポンスに ``staff_assignments`` (visit_staff_assignments の一覧) を含める
  - 入力で ``course_id`` / ``required_staff_count`` / ``visit_group_id`` を受理
  - ``POST /visits/{id}/staff`` / ``DELETE /visits/{id}/staff/{staff_id}``
    で多対多テーブルを操作 (API 契約 §5.4 / §5.5)
  - スタッフロールの可視性は visit_staff_assignments も含めて判定
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.visit import VisitCreate, VisitRead, VisitUpdate

router = APIRouter()


def _staff_visibility_filter(staff_id: UUID):
    """Visit が当該スタッフに見える条件 (primary/secondary/mentor または assignments)."""
    # Note: visit_staff_assignments のサブクエリでも該当する visit を含める
    assignments_subq = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return or_(
        Visit.primary_staff_id == staff_id,
        Visit.secondary_staff_id == staff_id,
        Visit.mentor_staff_id == staff_id,
        Visit.id.in_(assignments_subq),
    )


async def _load_assignments(db, visit_id: UUID) -> list[dict]:
    """Load visit_staff_assignments rows for a single visit."""
    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_id)
        )
    ).all()
    return [
        {
            "visit_id": r.visit_id,
            "staff_id": r.staff_id,
            "assigned_at": r.created_at,
        }
        for r in rows
    ]


def _serialize_visit(visit: Visit, assignments: list[dict] | None = None) -> dict:
    """Project a Visit (with optional eager-loaded patient/primary_staff) into
    the VisitRead shape, including denormalized `patient_name`/`staff_name`
    and v2 ``staff_assignments`` (visit_staff_assignments).
    """
    data = {
        "id": visit.id,
        "patient_id": visit.patient_id,
        "primary_staff_id": visit.primary_staff_id,
        "secondary_staff_id": visit.secondary_staff_id,
        "mentor_staff_id": visit.mentor_staff_id,
        "visit_date": visit.visit_date,
        "start_time": visit.start_time,
        "end_time": visit.end_time,
        "type": visit.type,
        "status": visit.status,
        "source": visit.source,
        "note": visit.note,
        "kaipoke_id": visit.kaipoke_id,
        # W2-BE4 v2 fields
        "course_id": getattr(visit, "course_id", None),
        "required_staff_count": getattr(visit, "required_staff_count", 1),
        "visit_group_id": getattr(visit, "visit_group_id", None),
        "created_at": visit.created_at,
        "updated_at": visit.updated_at,
        "deleted_at": visit.deleted_at,
        "patient_name": getattr(visit.patient, "name", None) if visit.patient is not None else None,
        "staff_name": (
            getattr(visit.primary_staff, "name", None) if visit.primary_staff is not None else None
        ),
        "staff_assignments": assignments or [],
    }
    return data


@router.get("", response_model=list[VisitRead], summary="List visits")
async def list_visits(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    week_start: Annotated[
        date | None, Query(description="Inclusive start date (yyyy-MM-dd)")
    ] = None,
    week_end: Annotated[date | None, Query(description="Inclusive end date (yyyy-MM-dd)")] = None,
    staff_id: Annotated[
        UUID | None,
        Query(description="Filter to visits where this staff is primary/secondary/mentor"),
    ] = None,
    course_id: Annotated[
        UUID | None,
        Query(description="Filter to visits in this course (W2-BE4)"),
    ] = None,
    patient_id: Annotated[
        UUID | None,
        Query(
            description=(
                "Filter to visits for a single patient. Wave Next 1 H3: "
                "PatientScheduleDetailDialog で当該患者の今週分のみ取得する用途."
            ),
        ),
    ] = None,
) -> list[dict]:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = (
        select(Visit)
        .where(Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    # Determine the effective staff filter:
    #   - staff role: always force-narrow to the caller's own staff_id (ignore
    #     any client-supplied value to prevent cross-staff peeking).
    #   - admin/manager: honor the optional `staff_id` query for the mobile
    #     "my visits" view; without it they continue to see everyone.
    if user.role == "staff":
        if user.staff_id is None:
            return []
        stmt = stmt.where(_staff_visibility_filter(user.staff_id))
    elif staff_id is not None:
        stmt = stmt.where(_staff_visibility_filter(staff_id))
    if week_start is not None:
        stmt = stmt.where(Visit.visit_date >= week_start)
    if week_end is not None:
        stmt = stmt.where(Visit.visit_date <= week_end)
    if course_id is not None:
        stmt = stmt.where(Visit.course_id == course_id)
    if patient_id is not None:
        stmt = stmt.where(Visit.patient_id == patient_id)
    stmt = stmt.order_by(Visit.visit_date, Visit.start_time).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    # Bulk-load assignments for the listed visits in a single query
    visit_ids = [v.id for v in rows]
    assignments_by_visit: dict[UUID, list[dict]] = {vid: [] for vid in visit_ids}
    if visit_ids:
        asn_rows = (
            await db.scalars(
                select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
            )
        ).all()
        for a in asn_rows:
            assignments_by_visit.setdefault(a.visit_id, []).append(
                {
                    "visit_id": a.visit_id,
                    "staff_id": a.staff_id,
                    "assigned_at": a.created_at,
                }
            )
    return [_serialize_visit(v, assignments=assignments_by_visit.get(v.id, [])) for v in rows]


@router.get("/{visit_id}", response_model=VisitRead, summary="Get visit by id")
async def get_visit(
    visit_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    assignments = await _load_assignments(db, visit.id)

    if user.role == "staff":
        if user.staff_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        # Visibility: primary/secondary/mentor staff OR a visit_staff_assignments row
        assigned_staff_ids = {a["staff_id"] for a in assignments}
        if user.staff_id not in (
            {visit.primary_staff_id, visit.secondary_staff_id, visit.mentor_staff_id}
            | assigned_staff_ids
        ):
            # Hide existence from non-assigned staff.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _serialize_visit(visit, assignments=assignments)


@router.post(
    "",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create visit",
)
async def create_visit(
    payload: VisitCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> dict:
    visit = Visit(**payload.model_dump())
    db.add(visit)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Conflict: duplicate value"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc
    # Reload with relationships for the response.
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit.id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit.id)
    return _serialize_visit(visit, assignments=assignments)


@router.patch("/{visit_id}", response_model=VisitRead, summary="Update visit")
async def update_visit(
    visit_id: UUID,
    payload: VisitUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> dict:
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(visit, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Conflict: duplicate value"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit.id)
    return _serialize_visit(visit, assignments=assignments)


@router.delete(
    "/{visit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete visit (admin / manager)",
)
async def delete_visit(
    visit_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    cascade_fixed_visit: Annotated[
        bool,
        Query(
            description=(
                "True: 同 (patient_id, weekday) の patient_fixed_visits "
                "(mode='normal' / 'special') も同時に物理削除する (W18 Codex-fix 重大-2)。"
                "B-5 配置移動 (delete + place-and-fix) で旧曜日の固定枠が残って"
                "翌週以降 Layer 1 で二重展開するのを防ぐ。"
            ),
        ),
    ] = False,
    cascade_partner: Annotated[
        bool,
        Query(
            description=(
                "True (default): 同 visit_group_id を持つペア visit "
                "(2 名体制対応の partner visit) も同時に soft-delete する "
                "(W37 Phase 2-A)。False: 当該 visit のみ片方削除する."
            ),
        ),
    ] = True,
) -> None:
    """visit を soft-delete する.

    W18 Codex-fix 重大-1: RBAC を admin / manager に拡張.
    W18 Codex-fix 重大-2: ``cascade_fixed_visit=true`` のとき、当該 visit の
    ``(patient_id, visit_date.weekday())`` に紐付く ``patient_fixed_visits``
    (mode='normal' / 'special' 両方) を **物理削除** する.

    W37 Phase 2-A: ``cascade_partner=true`` (default) のとき、当該 visit と
    同じ ``visit_group_id`` を持つ partner visit (2 名体制ペア) も同時に
    soft-delete する. 「2 名はペアで削除」が標準フロー. 入力ミスで片方だけ
    消したいケースは ``cascade_partner=false`` で対応.

    両 cascade は併用可能 (cascade_fixed_visit=true & cascade_partner=true で
    ペアの visit + 共通 weekday の固定枠 normal/special を全て削除).
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # ----- W37 Phase 2-A: partner visit の同時 soft-delete -----
    # visit_group_id が NULL の場合 (1 名体制) は cascade_partner の値に依らず
    # no-op (partner はそもそも存在しない).
    partner_visits: list[Visit] = []
    if cascade_partner and visit.visit_group_id is not None:
        partner_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_group_id == visit.visit_group_id,
                    Visit.id != visit.id,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        partner_visits = list(partner_rows)

    # cascade_fixed_visit: visit の (patient_id, weekday) に紐付く固定枠を物理削除.
    # mode='normal' と 'special' の両方を対象にする (どちらが残っていても
    # 翌週展開で二重訪問になり得るため). slot_index 0/1 も全て削除される
    # (W37 Phase 2-A: 2 名体制患者の partner も含めた全 slot を一掃).
    #
    # Phase G-21 final C1: pinned PFV (is_pinned=True) は D&D / cascade で
    # 動かせない (= 物理削除 + 新規 PFV で is_pinned=false 化されるバイパス防止).
    # 削除対象に pinned PFV が 1 件でもあれば 422 で拒否し、ユーザーに先に
    # 完全固定を解除するよう促す.
    if cascade_fixed_visit:
        old_weekday = visit.visit_date.weekday()
        pinned_targets = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == visit.patient_id,
                    PatientFixedVisit.weekday == old_weekday,
                    PatientFixedVisit.is_pinned.is_(True),
                )
            )
        ).all()
        if pinned_targets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=("完全固定の固定枠は削除できません. 先に完全固定を解除してください."),
            )
        await db.execute(
            delete(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == visit.patient_id,
                PatientFixedVisit.weekday == old_weekday,
            )
        )

    # 当該 visit を soft-delete.
    visit.deleted_at = func.now()
    # partner visits も soft-delete (W37 Phase 2-A).
    for pv in partner_visits:
        pv.deleted_at = func.now()
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# W2-BE4: visit_staff_assignments operations (API 契約 §5.4 / §5.5)
# ---------------------------------------------------------------------------


class VisitStaffAddRequest(BaseModel):
    """``POST /visits/{id}/staff`` リクエスト."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID


@router.post(
    "/{visit_id}/staff",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add staff to visit (visit_staff_assignments) — W2-BE4",
)
async def add_visit_staff(
    visit_id: UUID,
    payload: VisitStaffAddRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> dict:
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    assignment = VisitStaffAssignment(visit_id=visit_id, staff_id=payload.staff_id)
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg or "primary" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: staff already assigned to this visit",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc

    assignments = await _load_assignments(db, visit_id)
    return _serialize_visit(visit, assignments=assignments)


@router.delete(
    "/{visit_id}/staff/{staff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove staff from visit (visit_staff_assignments) — W2-BE4",
)
async def remove_visit_staff(
    visit_id: UUID,
    staff_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> None:
    # 確認: visit が存在するか
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    # 既存 assignment を削除. 該当行が無い場合は 404
    # 先に存在チェックしてから削除する (SQLAlchemy 2.0 の Result では rowcount が
    # 利用できない方言があるため)
    existing = await db.scalar(
        select(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id == visit_id,
            VisitStaffAssignment.staff_id == staff_id,
        )
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    await db.execute(
        delete(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id == visit_id,
            VisitStaffAssignment.staff_id == staff_id,
        )
    )
    await db.commit()
    return None
