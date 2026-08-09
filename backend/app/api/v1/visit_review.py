"""訪問「確認済み」API — QR チェックイン Phase 5-3.

POST   /api/v1/visits/{visit_id}/review   確認済みにする (upsert)。body {comment?}
DELETE /api/v1/visits/{visit_id}/review   確認を取り消す (undo, 行削除)

review は **visit 単位** (未訪問=checkin 無しでもクリアできるよう / 設計 §5-3)。
確認済みの visit は訪問モニターの要対応トレイから外れ (``compute_alert`` を抑制)、
タイムラインに「確認済」印が付く。RBAC: admin / manager のみ (staff は 403)。

監査ログは ``audit_logs`` に ``visit_review_upsert`` / ``visit_review_delete`` を
明示記録する (scheduling_settings.py の作法に倣う)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_review import VisitReview
from app.schemas.visit_review import VisitReviewRequest, VisitReviewResponse

router = APIRouter()


def _reviewer_name(actor: User) -> str | None:
    """確認者の表示名 (staff 名 → 無ければ email).

    ``actor.staff`` は ``User.staff`` が ``lazy="selectin"`` (models/user.py) の
    ため current-user 解決時に eager-load 済みである前提で同期アクセスする
    (追加の await/lazy I/O は発生しない)。
    """
    staff = getattr(actor, "staff", None)
    if staff is not None and getattr(staff, "name", None):
        return staff.name
    # email may be NULL for staff (code-based) accounts — fall back to username.
    return actor.email or actor.username


async def _load_visit_or_404(db: DbDep, visit_id: UUID) -> Visit:
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return visit


@router.post(
    "/{visit_id}/review",
    response_model=VisitReviewResponse,
    summary="訪問を確認済みにする (upsert) — admin/manager",
)
async def review_visit(
    visit_id: UUID,
    payload: VisitReviewRequest,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> VisitReviewResponse:
    """確認済みマークを upsert する (reviewed_by=current_user / reviewed_at=now)."""
    await _load_visit_or_404(db, visit_id)

    comment = payload.comment.strip() if payload.comment and payload.comment.strip() else None
    now = datetime.now(UTC)

    review = await db.scalar(select(VisitReview).where(VisitReview.visit_id == visit_id))
    if review is None:
        review = VisitReview(visit_id=visit_id)
        db.add(review)
        action = "visit_review_upsert"
        before: dict[str, object | None] = {}
    else:
        action = "visit_review_upsert"
        before = {
            "reviewed_by": str(review.reviewed_by) if review.reviewed_by else None,
            "comment": review.comment,
        }
    review.reviewed_by = actor.id
    review.reviewed_at = now
    review.comment = comment

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=action,
            target_table="visit_reviews",
            target_id=str(visit_id),
            # 新規作成時は before={} (空 dict) なので None に正規化し、監査ログの
            # before を「変更前なし」と明示する (空 dict と null を取り違えない)。
            before=before or None,
            after={"reviewed_by": str(actor.id), "comment": comment},
        )
    )

    await db.commit()
    await db.refresh(review)

    return VisitReviewResponse(
        visit_id=visit_id,
        reviewed_by=review.reviewed_by,
        reviewed_by_name=_reviewer_name(actor),
        reviewed_at=review.reviewed_at,
        comment=review.comment,
    )


@router.delete(
    "/{visit_id}/review",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="訪問の確認を取り消す (undo) — admin/manager",
)
async def unreview_visit(
    visit_id: UUID,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """確認済みマークを削除する (要対応トレイに復活)."""
    review = await db.scalar(select(VisitReview).where(VisitReview.visit_id == visit_id))
    if review is None:
        # 既に未確認 (べき等)。404 ではなく 204 で冪等に扱う。
        return None

    before = {
        "reviewed_by": str(review.reviewed_by) if review.reviewed_by else None,
        "comment": review.comment,
    }
    await db.delete(review)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="visit_review_delete",
            target_table="visit_reviews",
            target_id=str(visit_id),
            before=before,
            after=None,
        )
    )
    await db.commit()
    return None
