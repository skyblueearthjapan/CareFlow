"""Visit review (訪問「確認済み」) API スキーマ — QR チェックイン Phase 5-3.

``POST /api/v1/visits/{visit_id}/review`` (upsert) / ``DELETE`` (undo) の契約。
review は **visit 単位** (未訪問でもクリアできるよう)。admin / manager 限定。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class VisitReviewRequest(BaseModel):
    """確認済みにする (upsert) のリクエスト body."""

    # 確認理由 (任意)。空文字は None と同等に扱う。
    comment: str | None = Field(default=None, max_length=2000)


class VisitReviewResponse(BaseModel):
    """確認済みマークの射影 (POST のレスポンス)."""

    visit_id: UUID
    reviewed_by: UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime
    comment: str | None = None


__all__ = ["VisitReviewRequest", "VisitReviewResponse"]
