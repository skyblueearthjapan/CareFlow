"""Staff master Excel import / export API.

3 endpoints (admin / manager only):
  * GET  /staff/import-export/template — 空テンプレート Excel
  * GET  /staff/import-export/export   — 全件エクスポート
  * POST /staff/import-export/import   — multipart upload + dry_run プレビュー / 反映

設計詳細は ``app.services.staff_excel`` モジュール群を参照.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.office import Office
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.schemas.v2.staff_excel import (
    StaffExcelImportResponse,
)
from app.services.staff_excel.exporter import build_workbook, workbook_to_bytes
from app.services.staff_excel.importer import apply_changes, parse_and_diff

logger = logging.getLogger(__name__)

router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Excel アップロードのサイズ上限. スタッフ数 1000 規模でも数 MB 程度を想定.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB


def _attachment_headers(filename: str) -> dict[str, str]:
    # ASCII-safe filename. 日本語が混じる場合は RFC 5987 を使うが、今回は固定 ASCII.
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# ---------------------------------------------------------------------------
# GET /template — 空テンプレート
# ---------------------------------------------------------------------------


@router.get(
    "/template",
    summary="スタッフマスタ Excel テンプレートをダウンロード (admin/manager のみ)",
    response_class=Response,
)
async def download_template(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Response:
    """ヘッダーと DataValidation のみ書かれた空ワークブックを返す."""
    offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
    wb = build_workbook(
        staff_list=[],
        shifts=[],
        offices=list(offices),
    )
    content = workbook_to_bytes(wb)
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers=_attachment_headers("staff_master_template.xlsx"),
    )


# ---------------------------------------------------------------------------
# GET /export — 全件エクスポート
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    summary="全 active スタッフマスタ + 勤務シフトを Excel でダウンロード",
    response_class=Response,
)
async def export_all(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Response:
    """alive なスタッフ全件 + 関連 shift をワークブックに書いて返す."""
    staff_list = (
        await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)).order_by(Staff.code))
    ).all()
    shifts = (
        await db.scalars(
            select(StaffShift)
            .where(StaffShift.staff_id.in_([s.id for s in staff_list]) if staff_list else False)
            .order_by(StaffShift.staff_id, StaffShift.weekday)
        )
    ).all()
    offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()

    wb = build_workbook(
        staff_list=list(staff_list),
        shifts=list(shifts),
        offices=list(offices),
    )
    content = workbook_to_bytes(wb)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers=_attachment_headers(f"staff_master_{ts}.xlsx"),
    )


# ---------------------------------------------------------------------------
# POST /import — upload + diff + (optional) apply
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=StaffExcelImportResponse,
    summary="スタッフマスタ Excel のアップロード (プレビュー / 反映)",
)
async def import_excel(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    file: UploadFile = File(..., description="Excel ファイル (.xlsx)"),
    dry_run: bool = True,
) -> StaffExcelImportResponse:
    """multipart/form-data でアップロード. ``dry_run`` が True ならプレビューのみ."""
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="アップロードされたファイルが空です",
        )
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(f"ファイルサイズが上限 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB) を超えています"),
        )

    try:
        staff_rows, shift_rows, summary, staff_ops, shift_ops = await parse_and_diff(
            db,
            file_bytes=content,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Excel ファイルのパースに失敗しました: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — openpyxl が多様な例外を投げる
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Excel ファイルの読み込みに失敗しました: {type(exc).__name__}: {exc}",
        ) from exc

    transaction_applied = False
    if not dry_run:
        # partial commit: error 行は skip、有効な op (new/update/delete) のみ apply.
        # staff_ops / shift_ops は parse_and_diff の段階で error 行を除外済み
        # (error は op=None を返すので *_ops に積まれない).
        # 有効な op が 0 件 (全 error or 全 noop) の場合は commit せず False を返す.
        if not staff_ops and not shift_ops:
            return StaffExcelImportResponse(
                summary=summary,
                staff_rows=staff_rows,
                shift_rows=shift_rows,
                transaction_applied=False,
            )
        try:
            await apply_changes(db, staff_ops=staff_ops, shift_ops=shift_ops)
            await db.commit()
            transaction_applied = True
        except IntegrityError as exc:
            # 同時 import で staff_code が衝突するなど、DB 制約違反.
            await db.rollback()
            logger.warning("staff_excel import IntegrityError: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "データ整合性エラーが発生しました "
                    "(staff_code 重複や同時更新の可能性)。再試行してください。"
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"DB 反映時にエラーが発生しました: {type(exc).__name__}: {exc}",
            ) from exc

    return StaffExcelImportResponse(
        summary=summary,
        staff_rows=staff_rows,
        shift_rows=shift_rows,
        transaction_applied=transaction_applied,
    )
