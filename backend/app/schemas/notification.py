"""Pydantic schemas for the per-user notification inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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
    #: 発生源の種類と行 (休みの申請は ``leave_request``・申請の id)。
    reference_type: str | None = None
    reference_id: UUID | None = None
    #: 画面が開く先の材料。休みの通知は ``staff_id``・``date`` (YYYY-MM-DD)。
    #: ``leave_unassigned`` は ``items`` (``staff_id``・``staff_name``・``date``・``visit_count``)
    #: も持つ (``staff_id``・``date`` は先頭の 1 件)。
    data: dict[str, Any] | None = None


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
