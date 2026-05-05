"""Schemas for the 連携センター (integrations) endpoints — Phase 5-1.

Covers:
  - KaipokeJob / KaipokeJobItem (fetch/push background jobs)
  - GeocodingCache (admin read-only listing)
  - AiInterpretLog (admin read-only listing)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --- Kaipoke jobs ----------------------------------------------------------

KaipokeJobType = Literal["fetch", "push"]
KaipokeJobStatus = Literal[
    "pending", "running", "completed", "failed", "cancelled"
]
KaipokeJobItemStatus = Literal[
    "pending", "running", "completed", "failed", "skipped"
]


class KaipokeJobBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: KaipokeJobType
    week_start: date
    params: dict[str, Any] = Field(default_factory=dict)


class KaipokeJobCreate(KaipokeJobBase):
    pass


class KaipokeJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: KaipokeJobStatus | None = None
    result_summary: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class KaipokeJobItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    job_id: UUID
    seq: int
    status: KaipokeJobItemStatus
    content: dict[str, Any] = Field(default_factory=dict)
    error_msg: str | None = None
    created_at: datetime
    updated_at: datetime


class KaipokeJobRead(KaipokeJobBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    status: KaipokeJobStatus
    result_summary: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    items: list[KaipokeJobItemRead] = Field(default_factory=list)


# --- Geocoding cache -------------------------------------------------------


class GeocodingCacheRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    address_hash: str
    address: str
    lat: float
    lng: float
    provider: str
    looked_up_at: datetime
    created_at: datetime
    updated_at: datetime


# --- AI interpret logs -----------------------------------------------------


class AiInterpretLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    prompt: str
    response: dict[str, Any] = Field(default_factory=dict)
    model: str
    latency_ms: int
    user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
