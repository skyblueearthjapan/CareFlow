"""操作ジャーナル — undo/redo エンドポイント (Wave U-3).

フルパス (/schedule prefix を api/__init__.py で付与):
    GET  /api/v1/schedule/v2/op-log/state?iso_year=&iso_week=
    POST /api/v1/schedule/v2/op-log/undo
    POST /api/v1/schedule/v2/op-log/redo

RBAC: admin / manager のみ。
スタック境界: ユーザー × 週（D-4: 自分の操作のみ対象）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.v2.op_log import OpLogStateResponse, UndoRedoRequest, UndoRedoResponse
from app.services.op_log_service import OpLogConflictError, execute_redo, execute_undo, get_state

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /v2/op-log/state
# ---------------------------------------------------------------------------


@router.get(
    "/v2/op-log/state",
    response_model=OpLogStateResponse,
    status_code=status.HTTP_200_OK,
    summary="自分のその週の undo/redo スタック状態を返す (Wave U-3)",
)
async def get_op_log_state(
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: Annotated[int, Query(ge=2000, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
) -> OpLogStateResponse:
    """自分のその週の操作スタック状態を返す（D-4: 自分の操作のみ）。"""
    state = await get_state(db, user_id=current_user.id, iso_year=iso_year, iso_week=iso_week)
    return OpLogStateResponse(**state)


# ---------------------------------------------------------------------------
# POST /v2/op-log/undo
# ---------------------------------------------------------------------------


@router.post(
    "/v2/op-log/undo",
    response_model=UndoRedoResponse,
    status_code=status.HTTP_200_OK,
    summary="最新操作を取り消す (Wave U-3)",
)
async def undo_op(
    body: UndoRedoRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> UndoRedoResponse:
    """自分の最新の undone=False グループを inverse 実行する（グループ内は逆順）。

    実行前に対象の現在状態が forward の結果と一致するか検証。
    不一致（他者変更）は 409 で返し、undone 状態は変化させない。

    成功時: undone=True にセット、実行後の state を返す（FE の再フェッチ削減）。
    """
    try:
        await execute_undo(
            db,
            user_id=current_user.id,
            iso_year=body.iso_year,
            iso_week=body.iso_week,
        )
        await db.commit()
    except OpLogConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise

    state = await get_state(
        db, user_id=current_user.id, iso_year=body.iso_year, iso_week=body.iso_week
    )
    return UndoRedoResponse(state=OpLogStateResponse(**state))


# ---------------------------------------------------------------------------
# POST /v2/op-log/redo
# ---------------------------------------------------------------------------


@router.post(
    "/v2/op-log/redo",
    response_model=UndoRedoResponse,
    status_code=status.HTTP_200_OK,
    summary="取り消した操作を再適用する (Wave U-3)",
)
async def redo_op(
    body: UndoRedoRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> UndoRedoResponse:
    """自分の最古の undone=True グループの forward を再実行する（グループ内は正順）。

    実行前に対象の現在状態が inverse の結果と一致するか検証。
    不一致（他者変更）は 409 で返す。

    成功時: undone=False にセット、実行後の state を返す。
    """
    try:
        await execute_redo(
            db,
            user_id=current_user.id,
            iso_year=body.iso_year,
            iso_week=body.iso_week,
        )
        await db.commit()
    except OpLogConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise

    state = await get_state(
        db, user_id=current_user.id, iso_year=body.iso_year, iso_week=body.iso_week
    )
    return UndoRedoResponse(state=OpLogStateResponse(**state))
