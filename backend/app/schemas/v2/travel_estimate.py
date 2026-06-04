"""移動時間推定 API (Phase G-84 A-1) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/travel-estimate (admin/manager)

現場ボードの空き枠直接配置 (案2) で「直前訪問から候補住所までの移動時間」を
既存ユーティリティ (``proposal_solver`` の haversine / VISIT_BUFFER_MINUTES) で
推定して返す read-only API のリクエスト / レスポンス型.

実装方針:
  - 距離 / 移動時間 / バッファーは ``proposal_solver`` から import して再利用する
    (新規距離ロジックを定義しない). これにより自動割当 / 提案と挙動が一致する.
  - DB 書込なし (推定のみ).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class TravelEstimateRequest(BaseModel):
    """``POST /v2/travel-estimate`` リクエスト (from / to の座標 or 住所)."""

    model_config = ConfigDict(extra="forbid")

    # --- from (直前訪問) の座標確定 ---
    from_patient_id: uuid.UUID | None = Field(
        default=None, description="直前訪問の患者 (patients.lat/lng を引く)"
    )
    from_lat: float | None = Field(
        default=None, ge=-90.0, le=90.0, description="from_patient_id 無いとき用フォールバック"
    )
    from_lng: float | None = Field(default=None, ge=-180.0, le=180.0)

    # --- to (配置候補) の座標確定 ---
    to_address: str | None = Field(default=None, description="新規患者: 住所 (geocode 対象)")
    to_lat: float | None = Field(
        default=None, ge=-90.0, le=90.0, description="既存患者: 既知座標 (geocode 省略)"
    )
    to_lng: float | None = Field(default=None, ge=-180.0, le=180.0)


class TravelEstimateResponse(BaseModel):
    """``POST /v2/travel-estimate`` レスポンス (移動時間 + バッファー)."""

    model_config = ConfigDict(extra="forbid")

    from_resolved: bool = Field(..., description="from 座標を確定できたか")
    to_resolved: bool = Field(..., description="to 座標を確定できたか")
    travel_minutes: int | None = Field(
        default=None, description="直線距離換算の移動時間 (分). 未確定なら null"
    )
    buffer_minutes: int = Field(..., description="= VISIT_BUFFER_MINUTES")
    total_minutes: int | None = Field(default=None, description="travel + buffer. 未確定なら null")


__all__ = [
    "TravelEstimateRequest",
    "TravelEstimateResponse",
]
