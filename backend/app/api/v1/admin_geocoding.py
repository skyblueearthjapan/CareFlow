"""Admin-only endpoint — 「lat/lng 整合性 audit job」 (Task #144 / Phase G-5).

定期 cron (host VPS 側) が叩く想定:

  0 3 * * *  curl -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \\
             https://carelink.kaipoke-api.net/api/v1/admin/geocoding/audit

dry_run=true で差分プレビューのみ取得可能.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.services.geocoding.audit import run_lat_lng_audit

router = APIRouter(prefix="/admin/geocoding", tags=["admin", "geocoding"])


class GeocodingAuditRequest(BaseModel):
    """``POST /admin/geocoding/audit`` の request body."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = Field(
        default=False,
        description="True なら DB 更新せず差分プレビューのみ集計する.",
    )
    limit_patients: int | None = Field(
        default=None,
        ge=1,
        description="テスト用に対象患者数を絞る (None = 無制限).",
    )


class GeocodingAuditCorrection(BaseModel):
    code: str
    name: str
    drift_km: float | None
    before: dict[str, Any]
    after: dict[str, Any]


class GeocodingAuditResponse(BaseModel):
    """``POST /admin/geocoding/audit`` の response."""

    model_config = ConfigDict(extra="forbid")

    locked: bool = Field(
        description="他のジョブが advisory lock を保持している場合 True (= no-op)."
    )
    checked: int = Field(description="audit を試みた患者数 (address 空 patient は skip).")
    corrected: int = Field(description="100m 以上のズレで補正した件数.")
    skipped_unchanged: int = Field(description="100m 未満ズレで補正不要だった件数.")
    errors: int = Field(description="Geocoding API エラーで処理できなかった件数.")
    details: list[GeocodingAuditCorrection] = Field(
        description="補正した患者の before/after 詳細 (= dry_run でも入る).",
    )


@router.post(
    "/audit",
    summary="lat/lng 整合性 audit (定期 cron 推奨、 100m 以上ズレで自動補正)",
    response_model=GeocodingAuditResponse,
)
async def geocoding_audit(
    db: DbDep,
    admin: Annotated[User, Depends(require_role("admin"))],
    body: GeocodingAuditRequest | None = None,
) -> GeocodingAuditResponse:
    """全 alive 患者の住所を Geocoding 再評価し、 100m 以上ずれた lat/lng を補正.

    定期 cron (= host VPS root の crontab で 0 3 * * * ) から叩く想定. 並列実行は
    pg_try_advisory_lock で排他する (= 重複起動しても二重 update しない).

    Phase G-3 で 86 名一括 geocoding した. 今後は本 endpoint を毎日叩くことで、
    User が住所だけ手で書き換えて lat/lng が旧値のまま残るケースを自動 catch.
    """
    payload = body or GeocodingAuditRequest()
    result = await run_lat_lng_audit(
        db,
        actor_user_id=admin.id,
        dry_run=payload.dry_run,
        limit_patients=payload.limit_patients,
    )
    return GeocodingAuditResponse(**result)
