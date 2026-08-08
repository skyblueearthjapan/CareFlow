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
from app.models.visit import (
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.visit import VisitCreate, VisitRead, VisitUpdate
from app.schemas.visit_checkin import CheckinCreate
from app.services.checkin.judge import judge_checkin
from app.services.checkin.notify import notify_checkin_mismatch, resolve_checkin_missing
from app.services.op_log_service import fmt_time, fmt_weekday, record_op
from app.services.trainee_accompaniment import (
    accompaniment_visibility_condition,
    is_accompaniment_visit_for_staff,
    resolve_accompaniment_by_visit,
)

router = APIRouter()


def _staff_visibility_filter(staff_id: UUID):
    """Visit が当該スタッフに見える条件 (primary/secondary/mentor または assignments).

    新人同行 (§6.4 C-1): 新人本人の「今日の訪問」「今週の予定」に同行訪問が出るよう、
    同行リンク条件 (visit 直リンク OR course リンク・live JOIN) を OR で足す。
    """
    # Note: visit_staff_assignments のサブクエリでも該当する visit を含める
    assignments_subq = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return or_(
        Visit.primary_staff_id == staff_id,
        Visit.secondary_staff_id == staff_id,
        Visit.mentor_staff_id == staff_id,
        Visit.id.in_(assignments_subq),
        accompaniment_visibility_condition(staff_id),
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


def _project_latest_checkin(row: VisitCheckin) -> dict:
    """VisitCheckin 行を VisitRead.latest_checkin (CheckinRead 形) に射影する。"""
    return {
        "id": row.id,
        "kind": row.kind,
        "match_status": row.match_status,
        "distance_m": float(row.distance_m) if row.distance_m is not None else None,
        "accuracy_m": float(row.accuracy_m) if row.accuracy_m is not None else None,
        "scanned_at": row.scanned_at,
        "checkin_source": row.checkin_source,
        "reason": row.reason,
        "is_override": row.is_override,
    }


async def _load_latest_checkin(db, visit_id: UUID) -> dict | None:
    """Load the most recent visit_checkins row (any kind) for a visit.

    Append-only テーブルから ``scanned_at DESC LIMIT 1`` で最新の打刻を採用する。
    VisitRead.latest_checkin に非破壊で載せる射影 (CheckinRead 形)。
    """
    row = await db.scalar(
        select(VisitCheckin)
        .where(VisitCheckin.visit_id == visit_id)
        .order_by(VisitCheckin.scanned_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return _project_latest_checkin(row)


async def _load_latest_checkins_bulk(db, visit_ids: list[UUID]) -> dict[UUID, dict]:
    """対象 visit 群の最新打刻 (any kind) を 1 クエリでバッチ取得する。

    一覧 (list_visits) の N+1 を避けるため、monitor の latest-per-kind 取得と同様に
    ``scanned_at DESC`` で全行を取得し visit_id ごと最初に出た行 (= 最新) を採用する。
    """
    latest: dict[UUID, dict] = {}
    if not visit_ids:
        return latest
    rows = (
        await db.scalars(
            select(VisitCheckin)
            .where(VisitCheckin.visit_id.in_(visit_ids))
            .order_by(VisitCheckin.scanned_at.desc())
        )
    ).all()
    for r in rows:
        if r.visit_id not in latest:  # DESC 並びなので最初に出た = 最新。
            latest[r.visit_id] = _project_latest_checkin(r)
    return latest


def _serialize_visit(
    visit: Visit,
    assignments: list[dict] | None = None,
    latest_checkin: dict | None = None,
    accompaniment: dict | None = None,
) -> dict:
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
        # 週のピン (青ピン / 2026-08-09)。手書き dict のため列追加時はここにも
        # 足すこと — 漏れると VisitRead の default False で上書きされ、DB が true
        # でも API が false を返す (実際に起きた事故)。
        "week_pinned": bool(getattr(visit, "week_pinned", False)),
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
        # 患者ジオコード (Numeric → float). モバイル QR チェックインの距離プレビュー
        # 用に非破壊で公開する. patient 未ロード / 未ジオコードは None.
        "patient_lat": (
            float(visit.patient.lat)
            if visit.patient is not None and visit.patient.lat is not None
            else None
        ),
        "patient_lng": (
            float(visit.patient.lng)
            if visit.patient is not None and visit.patient.lng is not None
            else None
        ),
        # モバイル訪問カードの性別ウォッシュ/📍住所用 (R-9)。patient 未ロードは None。
        "patient_sex": getattr(visit.patient, "sex", None) if visit.patient is not None else None,
        "patient_address": (
            getattr(visit.patient, "address", None) if visit.patient is not None else None
        ),
        "staff_assignments": assignments or [],
        # QR チェックイン (Phase 1) の最新打刻. 既存呼出は None のまま (非破壊).
        "latest_checkin": latest_checkin,
        # 新人同行 (非破壊追加). 同行新人が付く訪問なら {staff_id, staff_name}、無ければ None.
        "accompaniment": accompaniment,
    }
    return data


def _accompaniment_payload(resolved: tuple[UUID, str | None] | None) -> dict | None:
    """resolve_accompaniment_by_visit の (staff_id, name) を VisitRead.accompaniment 形へ."""
    if resolved is None:
        return None
    staff_id, staff_name = resolved
    return {"staff_id": staff_id, "staff_name": staff_name}


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
    # 最新打刻を 1 クエリでバッチ取得し (N+1 回避)、各 visit に紐付ける。GET /visits/{id}
    # との契約対称性のため一覧でも latest_checkin を返す (QR チェックイン)。
    latest_by_visit = await _load_latest_checkins_bulk(db, visit_ids)
    # 新人同行 (§6.4): 訪問群の同行者を 1 度に解決し、各 visit に非破壊で載せる。
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, list(rows))
    return [
        _serialize_visit(
            v,
            assignments=assignments_by_visit.get(v.id, []),
            latest_checkin=latest_by_visit.get(v.id),
            accompaniment=_accompaniment_payload(accompaniment_by_visit.get(v.id)),
        )
        for v in rows
    ]


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
        visible = user.staff_id in (
            {visit.primary_staff_id, visit.secondary_staff_id, visit.mentor_staff_id}
            | assigned_staff_ids
        )
        # 新人同行 (§6.4 C-1): 同行リンク (visit 直リンク OR course リンク) も可視に
        # 含める (漏れると新人がカードをタップした瞬間 404)。
        if not visible:
            visible = await is_accompaniment_visit_for_staff(
                db, visit_id=visit.id, course_id=visit.course_id, staff_id=user.staff_id
            )
        if not visible:
            # Hide existence from non-assigned staff.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    latest_checkin = await _load_latest_checkin(db, visit.id)
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, [visit])
    return _serialize_visit(
        visit,
        assignments=assignments,
        latest_checkin=latest_checkin,
        accompaniment=_accompaniment_payload(accompaniment_by_visit.get(visit.id)),
    )


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
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
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
    op_group_id: Annotated[
        UUID | None,
        Query(
            description="操作グループ ID（Wave U-3 undo/redo）。FE が DnD の delete+place に同値を送る。省略で自動発行。",
        ),
    ] = None,
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

    # Wave U-3: op_log 用データを commit 前に確保
    _op_patient_id = visit.patient_id
    _op_visit_date = visit.visit_date
    _op_start_time = visit.start_time
    _op_all_visit_ids = [str(visit.id)] + [str(pv.id) for pv in partner_visits]

    # 当該 visit を soft-delete.
    visit.deleted_at = func.now()
    # partner visits も soft-delete (W37 Phase 2-A).
    for pv in partner_visits:
        pv.deleted_at = func.now()
    await db.commit()

    # Wave U-3: 操作ジャーナルに記録（ベストエフォート）
    _wd = _op_visit_date.weekday()
    _label = f"訪問を削除（{fmt_weekday(_wd)}{fmt_time(_op_start_time)}）"
    await record_op(
        db,
        user_id=current_user.id,
        iso_year=_op_visit_date.isocalendar()[0],
        iso_week=_op_visit_date.isocalendar()[1],
        op_group_id=op_group_id,
        op_kind="delete_visit",
        label=_label,
        forward_payload={"op": "soft_delete_visits", "visit_ids": _op_all_visit_ids},
        inverse_payload={"op": "restore_visits", "visit_ids": _op_all_visit_ids},
    )
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


# ---------------------------------------------------------------------------
# QR 訪問チェックイン (Phase 1) — checkin / checkout / no-show
#
# staff が自分の visit に対して到着 / 退出 / 未訪問を打刻する。判定 (距離・
# match_status) は ``app.services.checkin.judge`` がサーバ側で確定する。返却は
# 既存 GET /visits/{id} と同形 (VisitRead) で、最新打刻を ``latest_checkin`` に
# 非破壊で載せる (フロント me.ts の MyVisit を壊さない / 設計 R1・R7)。
# ---------------------------------------------------------------------------


async def _load_visit_for_checkin(db, visit_id: UUID, user: User) -> tuple[Visit, UUID]:
    """打刻用に visit をロードし、呼び出し元が「自分の visit」か検証する。

    visit ガード (削除 / 取消 / 当日) は judge が行うため、ここでは
    ``deleted_at`` でフィルタしない (削除済みは judge が 409 を返す)。
    可視性 (担当外) は存在を秘匿するため 404 とする (既存 get_visit と同方針)。

    可視性判定を ``_staff_visibility_filter`` (WHERE 句) で直接絞らず、visit を
    無条件にロードしてから Python 側で担当判定するのは意図的:
      - 担当外 (visibility NG) は 404 で「存在を秘匿」したいが、
      - 削除済み / 取消 / 当日外 (= 担当はしている) は judge が 409 を返す必要がある。
    フィルタで一括に弾くと、削除済み visit が「行なし」となり 409 ではなく 404 に
    潰れてしまい、この 409 と 404 の区別が付かなくなる。両者を分けるため visit を
    取得後に Python で可視性のみ判定し、deleted/cancelled/当日外は judge に委ねる。

    検証済みの ``(visit, staff_id)`` を返す (``staff_id`` は非 NULL を保証)。
    """
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    staff_id = user.staff_id
    if staff_id is None:
        # User に staff 未紐付け → 打刻者を確定できない (設計 §2-1)。
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not linked to a staff record",
        )

    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    assignments = await _load_assignments(db, visit.id)
    assigned_staff_ids = {a["staff_id"] for a in assignments}
    visible = staff_id in (
        {visit.primary_staff_id, visit.secondary_staff_id, visit.mentor_staff_id}
        | assigned_staff_ids
    )
    # 新人同行 (§6.4 C-1): 同行リンクも可視に含める。新人は自分の ID でチェックイン
    # 可能 (カイポケ上も正規 2 人目扱いのため打刻も本人が行う設計判断)。judge_checkin は
    # 渡された staff_id で VisitCheckin を記録するのみで「担当≠打刻者」を警告する経路は
    # 無いため、同行者打刻はそのまま正当な打刻として扱われる (別途の抑止は不要)。
    if not visible:
        visible = await is_accompaniment_visit_for_staff(
            db, visit_id=visit.id, course_id=visit.course_id, staff_id=staff_id
        )
    if not visible:
        # 担当外には存在を秘匿する。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return visit, staff_id


async def _checkin_response(db, visit_id: UUID) -> dict:
    """打刻後の visit を GET /visits/{id} と同形で組み立てる (latest_checkin 付き)."""
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit_id)
    latest_checkin = await _load_latest_checkin(db, visit_id)
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, [visit])
    return _serialize_visit(
        visit,
        assignments=assignments,
        latest_checkin=latest_checkin,
        accompaniment=_accompaniment_payload(accompaniment_by_visit.get(visit_id)),
    )


@router.post(
    "/{visit_id}/checkin",
    response_model=VisitRead,
    summary="QR チェックイン (到着) — staff (自分の visit)",
)
async def checkin_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user)
    checkin = await judge_checkin(db, visit, staff_id, payload, "arrival")
    # status 退行ガード: 既に completed の visit に再 checkin しても completed の
    # まま据え置く (in_progress へ巻き戻さない)。planned / in_progress のときのみ
    # in_progress へ進める。checkin 行 (append-only) は status に依らず記録される。
    if visit.status in (VISIT_STATUS_PLANNED, VISIT_STATUS_IN_PROGRESS):
        visit.status = VISIT_STATUS_IN_PROGRESS
    # Phase 5-2: 場所違い (mismatch) は admin/manager へイベント駆動で冪等通知する
    # (同一 transaction で add し、下の commit で一括永続化)。
    await notify_checkin_mismatch(db, visit=visit, checkin=checkin)
    # 遅刻→到着で cron 生成済みの「未訪問」通知が残らないよう、到着記録と同一
    # transaction で当該 visit の missing 通知を解消する (全ユーザー分削除)。
    await resolve_checkin_missing(db, visit.id)
    await db.commit()
    return await _checkin_response(db, visit_id)


@router.post(
    "/{visit_id}/checkout",
    response_model=VisitRead,
    summary="QR チェックアウト (退出) — staff (自分の visit)",
)
async def checkout_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user)
    await judge_checkin(db, visit, staff_id, payload, "departure")
    visit.status = VISIT_STATUS_COMPLETED
    await db.commit()
    return await _checkin_response(db, visit_id)


@router.post(
    "/{visit_id}/no-show",
    response_model=VisitRead,
    summary="未訪問記録 (理由必須) — staff (自分の visit)",
)
async def no_show_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    # no_show は理由必須 (場所違いの強行記録とは異なり、未実施の説明を残す)。
    if payload.reason is None or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reason is required for no-show",
        )
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user)
    # no_show は visit.status を据置 (planned のまま; モニターが時間ベースで判定)。
    await judge_checkin(db, visit, staff_id, payload, "no_show")
    await db.commit()
    return await _checkin_response(db, visit_id)
