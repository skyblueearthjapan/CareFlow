"""Pending request CRUD + approve/reject endpoints (W2-BE5 + W7-BE3).

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §7 に対応する v2 endpoints。

エンドポイント (`docs/plans/v2-api-contracts.md` §7):
    POST   /api/v1/pending-requests                — 申請作成 (admin/manager/staff)
    POST   /api/v1/pending-requests/create-and-apply — 同一 TX 即時反映 (admin/manager) [W7-BE3]
    GET    /api/v1/pending-requests                — 一覧 (filter)
    GET    /api/v1/pending-requests/{id}           — 詳細
    PATCH  /api/v1/pending-requests/{id}/approve              — 承認 (applier 起動)
    PATCH  /api/v1/pending-requests/{id}/approve-with-edit    — 編集承認 (edited_payload 必須)
    PATCH  /api/v1/pending-requests/{id}/reject               — 却下 (rejection_reason 必須)
    DELETE /api/v1/pending-requests/{id}          — 取り下げ (申請者staff本人 or admin・pendingのみ)

RBAC:
    - POST  : admin / manager / staff (Staff は自分軸のみ; §3.5.3)
    - POST create-and-apply: admin / manager のみ (§3.5.3 即時反映)
    - GET   : admin / manager / staff (Staff は自分の申請 + 自分宛のみ)
    - PATCH approve / reject: admin / manager のみ

冪等性 (§3.5.3 / 受入基準 4 / Codex Must-fix #5):
    - W2-BE5: status='approved' チェックで二重承認を防ぐ
    - W7-BE3: ``applied_at`` カラム + ``SELECT ... FOR UPDATE`` 行ロック +
      条件付き UPDATE (``WHERE applied_at IS NULL AND status='pending'``)
      による同時 approve 競合の根絶。0 行更新なら 409 を返す。

トランザクション (受入基準 5 / Codex Must-fix #3):
    - approve では applier の業務反映 + status / applied_at 更新を **同一 TX** で実行
    - create-and-apply では 申請作成 + applier 実行 + status / applied_at 設定を
      **同一 TX** で実行 (FE の 2 HTTP call による TX 分裂を解消)
    - 失敗時は両方 rollback。``PendingRequestApplyError`` を捕捉して
      HTTP エラーに翻訳する。

Payload 検証強化 (Codex Must-fix #4):
    - Staff role は ``staff_*`` 系で ``payload.staff_id`` を強制上書き
    - Staff role は ``patient_*`` 系で payload.patient_id の患者が自分の担当か検証
    - target_staff_id / target_patient_id と payload 内 ID の不一致を 422
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.pending_request import PendingRequest
from app.models.staff import StaffWeeklyOverride
from app.models.user import User, normalize_user_role
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
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

# ``staff_*`` 系: payload.staff_id を current_user.staff_id に強制上書きする
_STAFF_AXIS_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        RequestType.STAFF_OFF.value,
        RequestType.STAFF_EVENT.value,
    }
)

# ``patient_*`` 系 (Staff が自分軸で扱える範囲): payload.patient_id が
# 当該スタッフの担当患者であるかを Visit / VisitStaffAssignment で検証する
_PATIENT_AXIS_REQUEST_TYPES: frozenset[str] = frozenset(
    {
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


def _coerce_uuid(value: Any) -> UUID | None:
    """payload 内の任意値を UUID に変換 (失敗時 None).

    JSON 由来は str, ORM 由来は UUID のいずれもありうる。
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _staff_owns_patient(db: AsyncSession, *, staff_id: UUID, patient_id: UUID) -> bool:
    """staff_id が patient_id の担当者であるかを判定する (Codex Must-fix #4).

    判定条件 (どちらか一方でも該当すれば True):
      1. ``visits.primary_staff_id = staff_id`` の visit が当該患者に存在
      2. ``visit_staff_assignments`` 経由で staff_id が当該患者の visit に
         アサインされている (2 名体制の secondary 含む)

    soft-delete された visit (``deleted_at IS NOT NULL``) は除外する。
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


async def _enforce_payload_validation(
    db: AsyncSession,
    user: User,
    payload: PendingRequestV2Create,
) -> dict:
    """Codex Must-fix #4 の payload 検証 + Staff スコープ強制.

    戻り値: 必要に応じて補正された payload 辞書 (新しい dict を返す).

    検証ルール:
      1. Staff role + ``staff_*`` 系: payload.staff_id を user.staff_id に強制上書き
      2. Staff role + ``patient_*`` 系: payload.patient_id が user.staff_id の
         担当患者であることを ``_staff_owns_patient`` で確認 (違反は 403)
      3. target_staff_id / target_patient_id と payload 内 ID の不一致を 422
         (admin/manager/staff いずれにも適用)
    """
    raw_payload = dict(payload.payload or {})
    rt_value = payload.request_type.value

    # ---- (1) Staff role + staff_* 系: payload.staff_id を強制上書き ----
    if user.role == "staff" and rt_value in _STAFF_AXIS_REQUEST_TYPES:
        if user.staff_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff user is not linked to a staff record",
            )
        # payload.staff_id を user.staff_id に強制上書き (申請者偽装の防止)
        payload_staff = _coerce_uuid(raw_payload.get("staff_id"))
        if payload_staff is not None and payload_staff != user.staff_id:
            # 強制上書きしてログ用に書き換える
            raw_payload["staff_id"] = str(user.staff_id)
        else:
            raw_payload["staff_id"] = str(user.staff_id)

    # ---- (2) Staff role + patient_* 系: 担当患者検証 ----
    if user.role == "staff" and rt_value in _PATIENT_AXIS_REQUEST_TYPES:
        if user.staff_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff user is not linked to a staff record",
            )
        # 患者 ID は payload.patient_id か target_patient_id のいずれかから取得
        patient_id = _coerce_uuid(raw_payload.get("patient_id") or payload.target_patient_id)
        if patient_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="patient_id is required for patient_* request from staff",
            )
        owns = await _staff_owns_patient(db, staff_id=user.staff_id, patient_id=patient_id)
        if not owns:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff is not assigned to this patient",
            )

    # ---- (3) target_* と payload 内 ID の不一致は 422 ----
    payload_staff = _coerce_uuid(raw_payload.get("staff_id"))
    if (
        payload.target_staff_id is not None
        and payload_staff is not None
        and payload.target_staff_id != payload_staff
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload.staff_id and target_staff_id must match",
        )
    payload_patient = _coerce_uuid(raw_payload.get("patient_id"))
    if (
        payload.target_patient_id is not None
        and payload_patient is not None
        and payload.target_patient_id != payload_patient
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload.patient_id and target_patient_id must match",
        )

    return raw_payload


def _enforce_staff_self_scope(user: User, payload: PendingRequestV2Create) -> None:
    """staff ロールの「自分軸」スコープをサーバ側で強制する (§3.5.3).

    自分軸 (§3.5.3):
      - 自分の休み・予定 (staff_off / staff_event)
      - 自分が担当する患者の今日のキャンセル / 日時変更 / 特別週 ON/OFF

    具体的な payload 内 ID 検証 (担当患者か等) は ``_enforce_payload_validation``
    が担当する。本関数は request_type のホワイトリスト + target_staff_id 一致のみを
    最終チェックする (W2-BE5 互換).
    """
    if user.role != "staff":
        return

    if payload.request_type.value not in _STAFF_ALLOWED_REQUEST_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff cannot create this request_type",
        )
    # 自分のスタッフ予定 (off / event) のときは target_staff_id を最終ガード
    if payload.request_type.value in _STAFF_AXIS_REQUEST_TYPES:
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


async def _ensure_staff_off_creatable(
    db: AsyncSession,
    *,
    payload: PendingRequestV2Create,
    payload_dict: dict,
) -> None:
    """``staff_off`` の作成時重複ガード (mobile-leave-request-design.md §1-c).

    applier (`_apply_staff_off`) は upsert せず INSERT のみのため、同日に
    pending 申請や既存 override があると **承認時に** IntegrityError→422 になる。
    作成時に前倒しで 409 を返し、申請者 (モバイル) に即フィードバックする。
    staff_id / 日付が特定できない payload は従来どおり素通し
    (承認時の applier 検証に委ねる — 既存クライアント互換)。
    """
    if payload.request_type.value != RequestType.STAFF_OFF.value:
        return

    staff_id = _coerce_uuid(payload_dict.get("staff_id")) or payload.target_staff_id
    target: date | None = payload.target_date
    if target is None:
        raw = payload_dict.get("date")
        try:
            target = date.fromisoformat(str(raw)) if raw else None
        except ValueError:
            target = None
    if staff_id is None or target is None:
        return

    dup = await db.scalar(
        select(PendingRequest.id)
        .where(
            PendingRequest.request_type == RequestType.STAFF_OFF.value,
            PendingRequest.status == RequestStatus.PENDING.value,
            PendingRequest.target_staff_id == staff_id,
            PendingRequest.target_date == target,
        )
        .limit(1)
    )
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="この日は既に休み申請中です",
        )

    # applier `_date_to_iso` と同一規約: isocalendar + weekday() (0=月曜)
    iso = target.isocalendar()
    existing = await db.scalar(
        select(StaffWeeklyOverride.id)
        .where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == iso.year,
            StaffWeeklyOverride.iso_week == iso.week,
            StaffWeeklyOverride.weekday == target.weekday(),
        )
        .limit(1)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="この日は既に休み・時間変更が登録されています",
        )


async def _commit_or_409(db: AsyncSession) -> None:
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
    if normalize_user_role(user.role) == "admin":
        return
    if user.role != "staff":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if row.requester_user_id == user.id:
        return
    if user.staff_id is not None and row.target_staff_id == user.staff_id:
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _build_pending_request_row(
    *,
    requester_user_id: UUID,
    payload: PendingRequestV2Create,
    payload_dict: dict,
    initial_status: str = RequestStatus.PENDING.value,
) -> PendingRequest:
    """``PendingRequest`` ORM 行をビルドする (DB に add する前の状態)."""
    return PendingRequest(
        requester_user_id=requester_user_id,
        request_type=payload.request_type.value,
        payload=payload_dict,
        target_staff_id=payload.target_staff_id,
        target_patient_id=payload.target_patient_id,
        target_date=payload.target_date,
        scope=payload.scope.value if payload.scope is not None else None,
        status=initial_status,
    )


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
    user: Annotated[User, Depends(require_role("admin", "staff"))],
) -> PendingRequestV2Read:
    _enforce_staff_self_scope(user, payload)
    _enforce_reschedule_scope_required(payload)
    payload_dict = await _enforce_payload_validation(db, user, payload)
    await _ensure_staff_off_creatable(db, payload=payload, payload_dict=payload_dict)

    row = _build_pending_request_row(
        requester_user_id=user.id,
        payload=payload,
        payload_dict=payload_dict,
    )
    db.add(row)
    await _commit_or_409(db)
    await db.refresh(row)
    return _to_read(row)


@router.delete(
    "/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a pending request (requester-staff or admin)",
)
async def withdraw_pending_request(
    request_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> None:
    """未処理 (pending) の申請を取り下げる (mobile-leave-request-design.md §1-c).

    - staff: **自分が申請した** pending のみ。他人の申請は存在も明かさず 404
      (`_check_read_access` と同じ流儀)。
    - admin: 任意の pending。
    - approved / rejected / applied 済みは 409 (業務判断が付いた記録は消さない)。
    - 行ロックで approve との同時実行競合を防ぐ (approve 側の
      `SELECT ... FOR UPDATE` と同じ規約)。ハード削除 — 却下と違い
      業務判断が発生していないため履歴には残さない。
    """
    row = (
        await db.execute(
            select(PendingRequest).where(PendingRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if normalize_user_role(user.role) != "admin":
        if user.role != "staff" or row.requester_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if row.status != RequestStatus.PENDING.value or row.applied_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="既に処理済みの申請は取り下げできません",
        )

    await db.delete(row)
    await _commit_or_409(db)


@router.post(
    "/create-and-apply",
    response_model=PendingRequestV2Read,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Create + immediately apply in a single TX (admin/manager only). "
        "PC admin/manager の即時反映フロー (Codex Must-fix #3)."
    ),
)
async def create_and_apply_pending_request(
    payload: PendingRequestV2Create,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> PendingRequestV2Read:
    """申請作成 + applier による業務反映を **同一トランザクション** で実行する.

    Codex Must-fix #3: 申請履歴フローで POST + PATCH の 2 HTTP call により
    TX が分裂していた問題を解消する。本エンドポイントは 1 リクエストで:

      1. PendingRequest を作成 (ただし db.flush のみで commit はしない)
      2. ``PendingRequestApplier`` で業務テーブルを更新
      3. status='approved' / approved_at / applied_at を設定
      4. 全体を 1 回の commit

    手順 2 で例外が発生したら全体を rollback して 422/404 等を返す。
    """
    _enforce_reschedule_scope_required(payload)
    payload_dict = await _enforce_payload_validation(db, user, payload)
    await _ensure_staff_off_creatable(db, payload=payload, payload_dict=payload_dict)

    # 同一 TX 内で作成 → applier → status 更新 → commit を実行する。
    # 注意: 行は **status='pending'** で初期化する。
    # applier の冪等性ガードは ``status='approved' AND approved_at IS NOT NULL``
    # で no-op するため、最初から approved にすると applier が走らない。
    now = datetime.now(UTC)
    row = _build_pending_request_row(
        requester_user_id=user.id,
        payload=payload,
        payload_dict=payload_dict,
        initial_status=RequestStatus.PENDING.value,
    )
    db.add(row)

    try:
        # PRIMARY KEY 等を確定させる (id 採番)
        await db.flush()

        # 業務反映 (失敗時は外側 except で rollback)
        applier = PendingRequestApplier()
        await applier.apply(db, row)

        # 反映完了 → approved に遷移 + applied_at をセット
        row.status = RequestStatus.APPROVED.value
        row.approved_by = user.id
        row.approved_at = now
        row.applied_at = now

        await db.commit()
    except PendingRequestApplyError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error during create-and-apply",
        ) from exc
    except Exception:
        await db.rollback()
        raise

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
    if normalize_user_role(user.role) not in {"admin", "staff"}:
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
                or_(
                    PendingRequest.requester_user_id == user.id,
                    PendingRequest.target_staff_id == user.staff_id,
                )
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


async def _claim_pending_for_approve(db: AsyncSession, request_id: UUID) -> PendingRequest:
    """Codex Must-fix #5: 同時 approve 競合を防ぐ「勝者単一化」処理.

    手順:
      1. 行存在チェック (404 判定)
      2. ``SELECT ... FOR UPDATE`` で行ロック (PG)
      3. 状態確認 (既に approved / rejected / applied なら 409)

    本関数を抜けた段階で、当該 request 行は本 TX 専用に確保された状態となり、
    呼び出し側は安全に applier 起動 → status 更新 → applied_at 設定を行える。

    SQLite (テスト) は ``FOR UPDATE`` を実質サポートしないが、
    SQLAlchemy は ``with_for_update()`` を no-op として扱うため例外にならない。
    """
    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == request_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # ----- row lock + 再読込 (PG only でロック効果あり) -----
    locked = await db.scalar(
        select(PendingRequest).where(PendingRequest.id == request_id).with_for_update()
    )
    if locked is None:
        # 直前まで存在したのに消えた異常ケース
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # ----- 状態確認 (W2-BE5 互換) -----
    if locked.status == RequestStatus.APPROVED.value or locked.applied_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already approved",
        )
    if locked.status == RequestStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already rejected",
        )
    return locked


async def _do_approve(
    db: AsyncSession,
    request_id: UUID,
    user: User,
    edited_payload: dict | None,
) -> PendingRequestV2Read:
    """承認処理を実行する.

    Codex Must-fix #5 の冪等性強化:
      1. ``_claim_pending_for_approve`` で行ロック + 状態チェック (status='pending' &&
         applied_at IS NULL の確認)
      2. 二重ガード: ``SELECT ... FOR UPDATE`` 後に追加の条件付き UPDATE を実行し、
         ``WHERE applied_at IS NULL AND status='pending'`` で 0 行更新なら
         競合敗者として 409 を返す。これにより SQLite テスト (FOR UPDATE が
         実質 no-op) でも複数 worker / 複数セッション競合を検出できる。
      3. applier 業務反映 + status / applied_at 更新を **同一 TX** で実行。
    """
    row = await _claim_pending_for_approve(db, request_id)

    if edited_payload is not None:
        row.edited_payload = edited_payload

    # ----- 二重ガード: 条件付き UPDATE ([CAS] applied_at IS NULL AND status='pending') -----
    # 競合相手が先に approve を完走させていた場合、ここで rowcount=0 となる。
    # PostgreSQL では _claim_pending_for_approve の FOR UPDATE が先に
    # 競合をシリアライズするため、この CAS は二重防御として機能する。
    # SQLite テストでは FOR UPDATE が no-op になるため、本 CAS が一次防御。
    now = datetime.now(UTC)
    update_stmt = (
        update(PendingRequest)
        .where(
            PendingRequest.id == request_id,
            PendingRequest.applied_at.is_(None),
            PendingRequest.status == RequestStatus.PENDING.value,
        )
        .values(
            status=RequestStatus.APPROVED.value,
            approved_by=user.id,
            approved_at=now,
            applied_at=now,
        )
        .execution_options(synchronize_session="fetch")
    )
    result = await db.execute(update_stmt)
    if (result.rowcount or 0) == 0:  # type: ignore[attr-defined]
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already approved",
        )

    # CAS 後の row は session 上では status='approved' / applied_at=now に同期済み
    # (synchronize_session='fetch'). ただし applier の冪等性ガードは
    # ``applied_at IS NOT NULL`` を no-op 条件にするため、applier に渡す前に
    # 一旦 None に戻して handler を実行させ、終了後に書き戻す。
    saved_applied_at = row.applied_at
    saved_status = row.status
    row.applied_at = None
    row.status = RequestStatus.PENDING.value

    applier = PendingRequestApplier()
    try:
        await applier.apply(db, row)
    except PendingRequestApplyError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    # 正常完了: 反映後の値を書き戻す
    row.applied_at = saved_applied_at
    row.status = saved_status

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error during approve",
        ) from exc

    # ORM ローカル状態を DB 値で再同期 (server defaults 等もリロード)
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
    user: Annotated[User, Depends(require_role("admin"))],
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
    user: Annotated[User, Depends(require_role("admin"))],
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
    user: Annotated[User, Depends(require_role("admin"))],
) -> PendingRequestV2Read:
    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == request_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # 同様に row lock を取得して状態を再確認 (Codex Must-fix #5 流儀の二重ガード)
    locked = await db.scalar(
        select(PendingRequest).where(PendingRequest.id == request_id).with_for_update()
    )
    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if locked.status == RequestStatus.APPROVED.value or locked.applied_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already approved",
        )
    if locked.status == RequestStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already rejected",
        )

    locked.status = RequestStatus.REJECTED.value
    locked.rejected_by = user.id
    locked.rejected_at = datetime.now(UTC)
    locked.rejection_reason = payload.rejection_reason
    await _commit_or_409(db)
    await db.refresh(locked)
    return _to_read(locked)


__all__ = [
    "router",
]
