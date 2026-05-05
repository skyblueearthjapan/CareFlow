"""Pydantic schemas for the per-user notification inbox."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    user_id: UUID
    type: str
    title: str
    body: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationAdminCreate(BaseModel):
    """Admin-only manual create — used until W6 producers ship."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    type: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    body: str | None = None


class NotificationMarkAllReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated: int
