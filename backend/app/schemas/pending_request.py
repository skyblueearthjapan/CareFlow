"""Pending request schemas — W2-BE5 API surface.

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 §7 に対応する API 層スキーマ。
v2 で確定した型 (`app.schemas.v2.pending_request`) をそのまま再エクスポートし、
業務 API (`app/api/v1/pending_requests.py`) はこのモジュールから読み込む。

監査要件 (§3.5.3):
  **即時反映** の場合も `status="approved"` で同時作成し、業務反映と
  同一トランザクションで処理する (冪等性確保) — 詳細は ``PendingRequestApplier`` 参照。
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.enums import (
    RequestScope,
    RequestStatus,
    RequestType,
)
from app.schemas.v2.pending_request import (
    PendingRequestApprove,
    PendingRequestReject,
    PendingRequestV2Base,
    PendingRequestV2Create,
    PendingRequestV2Read,
    PendingRequestV2Update,
)

T = TypeVar("T")


class PaginatedPendingRequest[T](BaseModel):
    """`GET /api/v1/pending-requests` のページング応答 (`Paginated[PendingRequestV2Read]`)."""

    model_config = ConfigDict(extra="forbid")

    items: list[T] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


__all__ = [
    "PaginatedPendingRequest",
    "PendingRequestApprove",
    "PendingRequestReject",
    "PendingRequestV2Base",
    "PendingRequestV2Create",
    "PendingRequestV2Read",
    "PendingRequestV2Update",
    "RequestScope",
    "RequestStatus",
    "RequestType",
]
