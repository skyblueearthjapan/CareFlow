"""訪問モニター集計 API — QR チェックイン Phase 3.

GET /api/v1/monitor?date=YYYY-MM-DD&office_id=（任意）
    その日の visits を visit_checkins と突き合わせ、スタッフ (= 1 日 1 コース) ごとに
    予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態 (phase + alert_level) を返す。

GET /api/v1/monitor/nearby?lat=&lng=&radius_m=（既定150）&limit=（既定5）
    指定座標付近の active 患者を距離付きで返す (場所違いの「〇〇様宅？」候補表示用)。

RBAC: admin / manager のみ (staff は 403)。集計ロジックは
``app.services.checkin.monitor``。
"""

from __future__ import annotations

from datetime import date as date_cls
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.visit_monitor import MonitorResponse, NearbyResponse
from app.services.checkin.monitor import build_monitor, find_nearby_patients

router = APIRouter()


@router.get(
    "",
    response_model=MonitorResponse,
    summary="訪問モニター集計 (スタッフ×コースの予定/実績/実効状態) — admin/manager",
)
async def get_monitor(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    date: Annotated[date_cls, Query(description="対象日 (YYYY-MM-DD)")],
    office_id: Annotated[UUID | None, Query(description="拠点 ID (未指定なら全拠点)")] = None,
) -> MonitorResponse:
    """指定日の訪問モニター集計を返す (DB 書込なし)."""
    return await build_monitor(db, date, office_id=office_id)


@router.get(
    "/nearby",
    response_model=NearbyResponse,
    summary="近隣患者宅候補 (場所違いの「〇〇様宅？」表示用) — admin/manager",
)
async def get_nearby(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    radius_m: Annotated[float, Query(ge=1, le=2000, description="探索半径 (m)")] = 150.0,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> NearbyResponse:
    """指定座標付近の active 患者 (lat/lng 有) を距離順に返す."""
    return await find_nearby_patients(db, lat=lat, lng=lng, radius_m=radius_m, limit=limit)
