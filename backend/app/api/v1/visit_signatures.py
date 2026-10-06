"""サインの画像の取り出し (``GET /api/v1/visit-signatures/{id}/image``).

正典設計書: ``docs/plans/signature-checkin-design-2026-10-06.md`` §5-1 Q6。

* **管理者も職員も、期間の制限なく見られる** (当日も過去の日も・担当外も)。
* 取り出すたびに ``audit_logs`` に 1 行残す (``action='signature_read'``)。監査
  ミドルウェアは GET を記録しないので、音声の取り出しと同じく自分で書く。
  **記録できなければ画像を渡さない** (503・fail closed)。「見た記録が残ります」と
  画面で約束しているので、記録の無い閲覧を作らない。
* 保持期間 (5 年) を過ぎて画像を消した行は 410。
* 画像は個人情報なので ``Cache-Control: no-store``。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep
from app.models.audit_log import AuditLog
from app.models.user import User, normalize_user_role
from app.models.visit_signature import VisitSignature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/visit-signatures", tags=["visit-signatures"])

_NO_STORE: dict[str, str] = {"Cache-Control": "no-store"}

#: 見られるアカウント (管理者・職員。PO 決定 Q6)。
_VIEWER_ROLES = frozenset({"admin", "staff"})

DETAIL_NOT_FOUND = "サインが見つかりません"
DETAIL_PURGED = "サインの画像は保存期間（5 年）を過ぎたため削除されています"
DETAIL_FILE_MISSING = "サインの画像ファイルがありません"
DETAIL_AUDIT_FAILED = (
    "見た記録を残せなかったため、サインを表示できません。少し待ってから、もう一度お試しください"
)


async def _audit_signature_read(
    db: AsyncSession, request: Request, user: User, signature_id: uuid.UUID
) -> bool:
    """サインの画像を見たことを ``audit_logs`` に 1 行残す。残せたら True。"""
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",", 1)[0].strip()[:64]
        if forwarded
        else (request.client.host[:64] if request.client else None)
    )
    db.add(
        AuditLog(
            actor_user_id=user.id,
            role=normalize_user_role(user.role),
            action="signature_read",
            target_table="visit_signatures",
            target_id=str(signature_id)[:64],
            method="GET",
            path=f"/api/v1/visit-signatures/{signature_id}/image"[:255],
            status_code=200,
            ip_address=ip_address,
            user_agent=(request.headers.get("user-agent") or None),
        )
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — 失敗は呼び出し側で 503 にする
        logger.exception("signature: audit insert failed (image not served)")
        await db.rollback()
        return False
    return True


@router.get(
    "/{signature_id}/image",
    response_class=FileResponse,
    summary="サインの画像 (管理者・職員・期間の制限なし・audit_logs に記録)",
)
async def get_visit_signature_image(
    signature_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: CurrentActiveUser,
) -> FileResponse:
    if normalize_user_role(user.role) not in _VIEWER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    row = await db.scalar(select(VisitSignature).where(VisitSignature.id == signature_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DETAIL_NOT_FOUND)
    if not row.image_path or row.image_deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=DETAIL_PURGED)
    file_path = Path(row.image_path)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=DETAIL_FILE_MISSING)
    # 監査の commit / rollback で ORM の属性が expire しても困らないよう、先に控える。
    media_type = row.image_mime or "application/octet-stream"
    if not await _audit_signature_read(db, request, user, signature_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=DETAIL_AUDIT_FAILED
        )
    return FileResponse(path=str(file_path), media_type=media_type, headers=_NO_STORE)
