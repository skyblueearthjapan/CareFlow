"""Patient master Excel import / export API.

3 endpoints (admin / manager only):
  * GET  /patients/import-export/template — 空テンプレート Excel
  * GET  /patients/import-export/export   — 全件エクスポート
  * POST /patients/import-export/import   — multipart upload + dry_run プレビュー / 反映

設計詳細は ``app.services.patient_excel`` モジュール群を参照.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.schemas.v2.patient_excel import (
    PatientExcelImportResponse,
    PatientExcelReplaceAllResponse,
)
from app.services.patient_excel.exporter import build_workbook, workbook_to_bytes
from app.services.patient_excel.importer import apply_changes, parse_and_diff
from app.services.patient_excel.replace_all import (
    apply_replace_all,
    parse_and_diff_replace_all,
)

logger = logging.getLogger(__name__)

router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Excel アップロードのサイズ上限. 患者数 1 万規模でも数 MB 程度を想定.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB


def _attachment_headers(filename: str) -> dict[str, str]:
    # ASCII-safe filename. 日本語が混じる場合は RFC 5987 を使うが、今回は固定 ASCII.
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# ---------------------------------------------------------------------------
# GET /template — 空テンプレート
# ---------------------------------------------------------------------------


@router.get(
    "/template",
    summary="患者マスタ Excel テンプレートをダウンロード (admin/manager のみ)",
    response_class=Response,
)
async def download_template(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Response:
    """ヘッダーと DataValidation のみ書かれた空ワークブックを返す."""
    # template でも office / course_template の dropdown は将来ロードして書きたいが、
    # 今は schema.py の固定値で十分.
    offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
    course_templates = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()
    wb = build_workbook(
        patients=[],
        pfvs=[],
        offices=list(offices),
        course_templates=list(course_templates),
    )
    content = workbook_to_bytes(wb)
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers=_attachment_headers("patients_master_template.xlsx"),
    )


# ---------------------------------------------------------------------------
# GET /export — 全件エクスポート
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    summary="全 active 患者マスタ + 固定訪問パターンを Excel でダウンロード",
    response_class=Response,
)
async def export_all(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Response:
    """alive な患者全件 + 関連 PFV をワークブックに書いて返す."""
    patients = (
        await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)).order_by(Patient.code))
    ).all()
    pfvs = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id.in_([p.id for p in patients]) if patients else False
            )
            .order_by(
                PatientFixedVisit.patient_id,
                PatientFixedVisit.mode,
                PatientFixedVisit.weekday,
                PatientFixedVisit.slot_index,
            )
        )
    ).all()
    offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
    course_templates = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()

    # Phase E-7 (gap P2): クロスオフィス course_template 参照 warning を収集し、
    # response header (X-Excel-Crossoffice-Warnings-Count) で operator が気付ける
    # ようにする. 0 件でも常に header を返す.
    crossoffice_warnings: list[str] = []
    wb = build_workbook(
        patients=list(patients),
        pfvs=list(pfvs),
        offices=list(offices),
        course_templates=list(course_templates),
        crossoffice_warnings_out=crossoffice_warnings,
    )
    content = workbook_to_bytes(wb)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    headers = _attachment_headers(f"patients_master_{ts}.xlsx")
    headers["X-Excel-Crossoffice-Warnings-Count"] = str(len(crossoffice_warnings))
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /import — upload + diff + (optional) apply
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=PatientExcelImportResponse,
    summary="患者マスタ Excel のアップロード (プレビュー / 反映)",
)
async def import_excel(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    file: UploadFile = File(..., description="Excel ファイル (.xlsx)"),
    dry_run: bool = True,
) -> PatientExcelImportResponse:
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
        patient_rows, pfv_rows, summary, patient_ops, pfv_ops = await parse_and_diff(
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
        # patient_ops / pfv_ops は parse_and_diff の段階で error 行を除外済み
        # (error は op=None を返すので *_ops に積まれない).
        # 有効な op が 0 件 (全 error or 全 noop) の場合は commit せず False を返す.
        if not patient_ops and not pfv_ops:
            return PatientExcelImportResponse(
                summary=summary,
                patient_rows=patient_rows,
                pfv_rows=pfv_rows,
                transaction_applied=False,
            )
        try:
            await apply_changes(db, patient_ops=patient_ops, pfv_ops=pfv_ops)
            await db.commit()
            transaction_applied = True
        except IntegrityError as exc:
            # 同時 import で patient_code が衝突するなど、DB 制約違反.
            # 生 IntegrityError を user に晒さず日本語メッセージで 409 を返す.
            await db.rollback()
            logger.warning("patients_excel import IntegrityError: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "データ整合性エラーが発生しました "
                    "(patient_code 重複や同時更新の可能性)。再試行してください。"
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"DB 反映時にエラーが発生しました: {type(exc).__name__}: {exc}",
            ) from exc

    return PatientExcelImportResponse(
        summary=summary,
        patient_rows=patient_rows,
        pfv_rows=pfv_rows,
        transaction_applied=transaction_applied,
    )


# ---------------------------------------------------------------------------
# POST /replace-all — 完全置換 (バックアップ復元) (admin のみ)
# ---------------------------------------------------------------------------


@router.post(
    "/replace-all",
    response_model=PatientExcelReplaceAllResponse,
    summary="患者マスタの完全置換インポート (admin のみ / atomic transaction)",
)
async def replace_all_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    file: UploadFile = File(..., description="Excel ファイル (.xlsx)"),
    dry_run: bool = True,
) -> PatientExcelReplaceAllResponse:
    """完全置換インポート (バックアップ復元).

    通常 import との違い:
      - Excel に無い既存 alive 患者は **soft delete** (関連 PFV は物理削除).
      - 空セルは NULL で上書き (現在値維持ではなく).
      - 既存 PFV を全件物理削除してから Excel 通りに再投入.
      - **atomic**: error 1 件でも全 rollback (partial commit ではない).
      - RBAC: admin のみ (manager 不可).
    """
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
        (
            patient_rows,
            pfv_rows,
            summary,
            patient_ops,
            pfv_ops,
            patients_to_soft_delete_preview,
        ) = await parse_and_diff_replace_all(db, file_bytes=content)
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
        # atomic: error 1 件でもあれば全 rollback (commit せず 422 を返す).
        if summary.patients_error > 0 or summary.pfv_error > 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"エラー行があるため完全置換を中止しました "
                    f"(patients_error={summary.patients_error}, "
                    f"pfv_error={summary.pfv_error})"
                ),
            )
        patient_ids_to_soft_delete = [
            UUID(p["patient_id"]) for p in patients_to_soft_delete_preview
        ]
        try:
            await apply_replace_all(
                db,
                patient_ops=patient_ops,
                pfv_ops=pfv_ops,
                patient_ids_to_soft_delete=patient_ids_to_soft_delete,
            )
            await db.commit()
            transaction_applied = True
        except IntegrityError as exc:
            await db.rollback()
            logger.warning("patients_excel replace_all IntegrityError: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "データ整合性エラーが発生しました "
                    "(patient_code 重複や同時更新の可能性)。再試行してください。"
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"DB 反映時にエラーが発生しました: {type(exc).__name__}: {exc}",
            ) from exc

    return PatientExcelReplaceAllResponse(
        summary=summary,
        patient_rows=patient_rows,
        pfv_rows=pfv_rows,
        transaction_applied=transaction_applied,
        patients_to_soft_delete_preview=patients_to_soft_delete_preview,
    )
