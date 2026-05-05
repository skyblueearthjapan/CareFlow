"""Schemas for `/api/v1/audit-logs` (Wave 4-F).

Read-only projection — caller cannot mutate audit rows by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    actor_user_id: UUID | None = None
    role: str | None = None
    action: str
    target_table: str
    target_id: str
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    latency_ms: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_body: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
