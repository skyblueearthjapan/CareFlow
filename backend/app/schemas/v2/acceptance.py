"""AcceptanceCalendar v2 schemas (Wave 15 Phase 1 / W15-BE1).

受入カレンダー (`acceptance_calendar` テーブル) の API 入出力型。

- weekday: 0=Mon ... 6=Sun
- time_slot: time (1 時間刻み運用想定)
- status: 'available' (○) / 'consult' (△) / 'unavailable' (×)
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 受入ステータス
AcceptanceStatus = Literal["available", "consult", "unavailable"]


class AcceptanceCalendarEntry(BaseModel):
    """受入カレンダー 1 エントリ (PUT bulk 入力 + GET レスポンス共通)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="曜日 (0=月, 6=日)")
    time_slot: time = Field(description="時間帯 (例: 10:00:00)")
    status: AcceptanceStatus = Field(description="available / consult / unavailable")
    notes: str | None = Field(default=None, description="自由記述")


class AcceptanceCalendarRead(AcceptanceCalendarEntry):
    """GET /api/v1/acceptance-calendar の単一エントリ."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    office_id: UUID
    created_at: datetime
    updated_at: datetime


class AcceptanceCalendarBulkUpsert(BaseModel):
    """PUT /api/v1/acceptance-calendar リクエスト.

    1 TX で当該 office_id の全既存削除 → entries を INSERT (冪等).
    """

    model_config = ConfigDict(extra="forbid")

    office_id: UUID = Field(description="対象拠点 (offices.id)")
    entries: list[AcceptanceCalendarEntry] = Field(
        default_factory=list,
        description="受入カレンダーエントリ一覧 (0..168 件 = 7 曜日 × 24 時間)",
    )
