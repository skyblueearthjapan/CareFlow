"""Schemas for `staff_events` (研修 / イベント / 会議 etc.).

The DB stores `event_type` as a short free-form string (≤16 chars). Existing
import scripts use English canonical values (meeting / training / errand /
interview / office / other). The HTTP API is permissive: any short string
is accepted, with Japanese aliases normalised to canonical English.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Public canonical event types (writes accept any short string + JP aliases).
_CANONICAL_EVENT = {
    "会議": "meeting",
    "打合せ": "meeting",
    "meeting": "meeting",
    "研修": "training",
    "training": "training",
    "イベント": "event",
    "event": "event",
    "役所同行": "errand",
    "errand": "errand",
    "初回面談": "interview",
    "interview": "interview",
    "事務": "office",
    "office": "office",
    "その他": "other",
    "other": "other",
}


def _normalise_event_type(value: str) -> str:
    if not value:
        raise ValueError("event_type must not be empty")
    canon = _CANONICAL_EVENT.get(value, value)
    if len(canon) > 16:
        raise ValueError("event_type must be ≤16 characters")
    return canon


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Annotated[str, Field(min_length=1, max_length=32)]
    starts_at: datetime
    ends_at: datetime
    title: str | None = Field(default=None, max_length=255)
    note: str | None = None

    @field_validator("event_type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return _normalise_event_type(v)

    @model_validator(mode="after")
    def _check_range(self) -> "EventBase":
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be < ends_at")
        return self


class EventCreate(EventBase):
    pass


class EventUpdate(BaseModel):
    """Partial update — every field optional."""

    model_config = ConfigDict(extra="forbid")

    event_type: str | None = Field(default=None, min_length=1, max_length=32)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    title: str | None = Field(default=None, max_length=255)
    note: str | None = None

    @field_validator("event_type")
    @classmethod
    def _norm_type(cls, v: str | None) -> str | None:
        return None if v is None else _normalise_event_type(v)


class EventRead(EventBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    staff_id: UUID
    created_at: datetime
    updated_at: datetime
