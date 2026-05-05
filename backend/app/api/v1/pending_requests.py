"""Pending request CRUD + approve/reject endpoints (W2-BE5).

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §7 に対応する v2 endpoints。

エンドポイント (`docs/plans/v2-api-contracts.md` §7):
    POST   /api/v1/pending-requests                — 申請作成 (admin/manager/staff)
    GET    /api/v1/pending-requests                — 一覧 (filter)
    GET    /api/v1/pending-requests/{id}           — 詳細
    PATCH  /api/v1/pending-requests/{id}/approve              — 承認 (applier 起動)
    PATCH  /api/v1/pending-requests/{id}/approve-with-edit    — 編集承認 (edited_payload 必須)
    PATCH  /api/v1/pending-requests/{id}/reject               — 却下 (rejection_reason 必須)

RBAC:
    - POST  : admin / manager / staff (Staff は自分軸のみ; §3.5.3)
    - GET   : admin / manager / staff (Staff は自分の申請 + 自分宛のみ)
    - PATCH approve / reject: admin / manager のみ

冪等性 (§3.5.3 / 受入基準 4):
    既に approved 済みの request に対して再度 approve が来た場合は 409 で返す
    (idempotency violation)。applier 自身も二重反映を no-op で防ぐ二重ガード。

トランザクション (受入基準 5):
    approve では applier の業務反映 + 自身の status 更新を **同一 TX** で実行し、
    どちらかが失敗したら両方 rollback する。`PendingRequestApplyError` を捕捉して
    HTTP エラーに翻訳する。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.pending_request import PendingRequest
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.pending_request import (
    PendingRequestApprove,
    PendingRequestReject,
    PendingRequestV2Create,
    PendingRequestV2Read,
)
from app.schemas.v2.enums import RequestStatus, RequestType
from app.services.pending_request_applier import (
    PendingRequestApplier,
    PendingRequestApplyError,
)

router = APIRouter()

# Staff が自分軸として申請できる request_type 集合 (§3.5.3 「自分軸」の定義).
_STAFF_ALLOWED_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        RequestType.STAFF_OFF.value,
        RequestType.STAFF_EVENT.value,
        RequestType.PATIENT_CANCEL.value,
        RequestType.PATIENT_RESCHEDULE.value,
        RequestType.PATIENT_SPECIAL_WEEK_ON.value,
        RequestType.PATIENT_SPECIAL_WEEK_OFF.value,
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_read(row: PendingRequest) -> PendingRequestV2Read:
    """SQLAlchemy ORM 行を Pydantic Read schema に詰め替える。

    enum 列は DB 上は ``str`` で持っているので、Pydantic StrEnum がそのまま受け入れる。
    """
    return PendingRequestV2Read.model_validate(row, from_attributes=True)


def _enforce_staff_self_scope(user: User, payload: PendingRequestV2Create) -> None:
    """staff ロールの「自分軸」スコープをサーバ側で強制する (§3.5.3).

    自分軸 (§3.5.3):
      - 自分の休み・予定 (staff_off / staff_event)
      - 自分が担当する患者の今日のキャンセル / 日時変更 / 特別週 ON/OFF

    本実装では payload に含まれる ``target_staff_id`` が user.staff_id と一致
    することを最低限のガードとして要求する。患者軸の「自分が担当する患者か」の
    詳細チェックは visit テーブル横断が必要なため、現段階では request_type の
    ホワイトリスト + target_staff_id チェックに絞る (Wave 3 以降で拡張余地)。
    """
    if user.role != "staff":
        return

    if payload.request_type.value not in _STAFF_ALLOWED_REQUEST_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff cannot create this request_type",
        )
    # 自分のスタッフ予定 (off / event) のときは target_staff_id を強制チェック
    if payload.request_type.value in {
        RequestType.STAFF_OFF.value,
        RequestType.STAFF_EVENT.value,
    }:
        if user.staff_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff user is not linked to a staff record",
            )
        if payload.target_staff_id is not None and payload.target_staff_id != user.staff_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff can only request for themselves",
            )


def _enforce_reschedule_scope_required(payload: PendingRequestV2Create) -> None:
    """patient_reschedule では scope (one_time / permanent) が必須 (§3.5.6)."""
    if payload.request_type.value == RequestType.PATIENT_RESCHEDULE.value:
        if payload.scope is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="patient_reschedule requires scope (one_time / permanent)",
            )


async def _commit_or_409(db) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


def _check_read_access(user: User, row: PendingRequest) -> None:
    """Staff は自分の申請 + 自分宛のみ閲覧可能 (§3.5.3)."""
    if user.role in {"admin", "manager"}:
        return
    if user.role != "staff":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if row.requester_user_id == user.id:
        return
    if user.staff_id is not None and row.target_staff_id == user.staff_id:
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PendingRequestV2Read,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pending request (admin/manager/staff)",
)
async def create_pending_request(
    payload: PendingRequestV2Create,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
) -> PendingRequestV2Read:
    _enforce_staff_self_scope(user, payload)
    _enforce_reschedule_scope_required(payload)

    row = PendingRequest(
        requester_user_id=user.id,
        request_type=payload.request_type.value,
        payload=payload.payload or {},
        target_staff_id=payload.target_staff_id,
        target_patient_id=payload.target_patient_id,
        target_date=payload.target_date,
        scope=payload.scope.value if payload.scope is not None else None,
        ai_interpret_log_id=payload.ai_interpret_log_id,
        status=RequestStatus.PENDING.value,
    )
    db.add(row)
    await _commit_or_409(db)
    await db.refresh(row)
    return _to_read(row)


@router.get(
    "",
    response_model=Paginated[PendingRequestV2Read],
    summary="List pending requests with filters",
)
async def list_pending_requests(
    db: DbDep,
    user: CurrentActiveUser,
    request_status: Annotated[
        RequestStatus | None, Query(alias="status", description="pending/approved/rejected")
    ] = None,
    request_type: Annotated[RequestType | None, Query()] = None,
    target_staff_id: Annotated[UUID | None, Query()] = None,
    target_patient_id: Annotated[UUID | None, Query()] = None,
    target_date_from: Annotated[date | None, Query()] = None,
    target_date_to: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[PendingRequestV2Read]:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    base_stmt = select(PendingRequest)
    if request_status is not None:
        base_stmt = base_stmt.where(PendingRequest.status == request_status.value)
    if request_type is not None:
        base_stmt = base_stmt.where(PendingRequest.request_type == request_type.value)
    if target_staff_id is not None:
        base_stmt = base_stmt.where(PendingRequest.target_staff_id == target_staff_id)
    if target_patient_id is not None:
        base_stmt = base_stmt.where(PendingRequest.target_patient_id == target_patient_id)
    if target_date_from is not None:
        base_stmt = base_stmt.where(PendingRequest.target_date >= target_date_from)
    if target_date_to is not None:
        base_stmt = base_stmt.where(PendingRequest.target_date <= target_date_to)

    if user.role == "staff":
        # 自分の申請 + 自分宛のみ
        if user.staff_id is not None:
            base_stmt = base_stmt.where(
                (PendingRequest.requester_user_id == user.id)
                | (PendingRequest.target_staff_id == user.staff_id)
            )
        else:
            base_stmt = base_stmt.where(PendingRequest.requester_user_id == user.id)

    total = await db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0

    stmt = base_stmt.order_by(PendingRequest.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()

    items = [_to_read(r) for r in rows]
    return Paginated[PendingRequestV2Read](
        items=items, total=int(total), limit=limit, offset=offset
    )


@router.get(
    "/{request_id}",
    response_model=PendingRequestV2Read,
    summary="Get a pending request by id",
)
async def get_pending_request(
    request_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> PendingRequestV2Read:
    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == request_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _check_read_access(user, row)
    return _to_read(row)


# ---------------------------------------------------------------------------
# Approve / Reject
# ---------------------------------------------------------------------------


async def _do_approve(
    db,
    request_id: UUID,
    user: User,
    edited_payload: dict | None,
) -> PendingRequestV2Read:
    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == request_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # 冪等性ガード (受入基準 4)
    if row.status == RequestStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already approved",
        )
    if row.status == RequestStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already rejected",
        )

    if edited_payload is not None:
        row.edited_payload = edited_payload

    # ----- applier 起動 (失敗時はトランザクション全体を rollback) -----
    applier = PendingRequestApplier()
    try:
        await applier.apply(db, row)
    except PendingRequestApplyError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    # 業務反映が成功したら status / approved_by / approved_at を更新
    row.status = RequestStatus.APPROVED.value
    row.approved_by = user.id
    row.approved_at = datetime.utcnow()

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error during approve",
        ) from exc

    await db.refresh(row)
    return _to_read(row)


@router.patch(
    "/{request_id}/approve",
    response_model=PendingRequestV2Read,
    summary="Approve a pending request (admin/manager) — apply business change",
)
async def approve_pending_request(
    request_id: UUID,
    payload: PendingRequestApprove,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PendingRequestV2Read:
    return await _do_approve(db, request_id, user, payload.edited_payload)


@router.patch(
    "/{request_id}/approve-with-edit",
    response_model=PendingRequestV2Read,
    summary="Approve with edited payload (admin/manager)",
)
async def approve_with_edit_pending_request(
    request_id: UUID,
    payload: PendingRequestApprove,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PendingRequestV2Read:
    if payload.edited_payload is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="approve-with-edit requires edited_payload",
        )
    return await _do_approve(db, request_id, user, payload.edited_payload)


@router.patch(
    "/{request_id}/reject",
    response_model=PendingRequestV2Read,
    summary="Reject a pending request (admin/manager) — rejection_reason required",
)
async def reject_pending_request(
    request_id: UUID,
    payload: PendingRequestReject,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PendingRequestV2Read:
    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == request_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if row.status == RequestStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already approved",
        )
    if row.status == RequestStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already rejected",
        )

    row.status = RequestStatus.REJECTED.value
    row.rejected_by = user.id
    row.rejected_at = datetime.utcnow()
    row.rejection_reason = payload.rejection_reason
    await _commit_or_409(db)
    await db.refresh(row)
    return _to_read(row)
