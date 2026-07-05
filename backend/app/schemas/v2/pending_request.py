"""PendingRequestV2 schemas (Wave 0-C).

設計仕様書 v0.9 §3.5 / §4.4 に基づく申請履歴 (`pending_requests`) v2。
業務上の承認ワークフロー記録。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.enums import RequestScope, RequestStatus, RequestType


class PendingRequestV2Base(BaseModel):
    """申請 (pending_request) v2 (§4.4).

    `payload` JSONB に request_type ごとの依頼内容を構造化して格納する。
    """

    model_config = ConfigDict(extra="forbid")

    request_type: RequestType = Field(description="9 種類 (§4.4 / API 契約 §9 対応表)")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="構造化された依頼内容 (手動入力)",
    )

    target_staff_id: UUID | None = Field(
        default=None,
        description="影響先スタッフ (該当するもの)",
    )
    target_patient_id: UUID | None = Field(
        default=None,
        description="影響先患者 (該当するもの)",
    )
    target_date: date | None = Field(
        default=None,
        description="影響日 (スケジュール画面の日付別フィルタ用)",
    )
    scope: RequestScope | None = Field(
        default=None,
        description=(
            "適用範囲. patient_reschedule のときのみ必須 (§3.5.6). "
            "それ以外の request_type では NULL"
        ),
    )


class PendingRequestV2Create(PendingRequestV2Base):
    """POST /api/v1/pending-requests リクエスト (W2-BE5).

    要求者 (`requester_user_id`) はサーバ側で `request.state.user` から自動付与する。
    """


class PendingRequestV2Update(BaseModel):
    """PATCH /api/v1/pending-requests/{id} 用 (内部利用).

    通常はクライアントは approve / reject エンドポイントを使う。
    本 schema は applier の内部状態更新用。
    """

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] | None = None
    edited_payload: dict[str, Any] | None = None
    target_staff_id: UUID | None = None
    target_patient_id: UUID | None = None
    target_date: date | None = None
    scope: RequestScope | None = None
    status: RequestStatus | None = None
    rejection_reason: str | None = None


class PendingRequestApprove(BaseModel):
    """PATCH /api/v1/pending-requests/{id}/approve リクエスト (W2-BE5)."""

    model_config = ConfigDict(extra="forbid")

    edited_payload: dict[str, Any] | None = Field(
        default=None,
        description="編集して承認の場合の編集後内容 (§3.5.4). NULL = 内容そのまま承認",
    )


class PendingRequestReject(BaseModel):
    """PATCH /api/v1/pending-requests/{id}/reject リクエスト (W2-BE5)."""

    model_config = ConfigDict(extra="forbid")

    rejection_reason: str = Field(
        min_length=1,
        max_length=2000,
        description="却下理由 (§3.5.4 で必須化)",
    )


class PendingRequestV2Read(PendingRequestV2Base):
    """GET /api/v1/pending-requests/{id} レスポンス (W2-BE5)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    requester_user_id: UUID = Field(description="入力者 (§4.4)")
    created_at: datetime = Field(description="申請日時 (§4.4)")
    updated_at: datetime
    status: RequestStatus = Field(default=RequestStatus.PENDING)

    edited_payload: dict[str, Any] | None = Field(
        default=None,
        description="編集して承認した場合の編集後内容 (§4.4)",
    )

    approved_by: UUID | None = Field(default=None, description="承認者 (§4.4)")
    approved_at: datetime | None = None
    applied_at: datetime | None = Field(
        default=None,
        description="業務反映完了タイムスタンプ (W7-BE3 冪等性). NULL = 未反映",
    )

    rejected_by: UUID | None = Field(default=None, description="却下者 (§4.4)")
    rejected_at: datetime | None = None
    rejection_reason: str | None = Field(
        default=None,
        description="却下理由 (rejected の場合必須, §4.4)",
    )


class PendingRequestCreateAndApplyRequest(PendingRequestV2Base):
    """POST /api/v1/pending-requests/create-and-apply リクエスト (W7-BE3 Codex #3).

    admin / manager が申請と承認・業務反映を 1 アクションで実行するフロー。
    payload 構造は通常の Create と同一。
    """


class PendingRequestCreateAndApplyResponse(PendingRequestV2Read):
    """POST /api/v1/pending-requests/create-and-apply レスポンス (W7-BE3 Codex #3).

    作成された PendingRequest の全フィールドを返す。
    applied_at は必ずセットされている (同一 TX 内で設定される)。
    """
