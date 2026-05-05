"""Schemas for `/api/v1/admin/users` (Wave 4-F).

Mirrors `app/models/user.py` with the Wave 4-F additions (`deleted_at`,
`must_change_password`). Plain CRUD payloads — no rate limiting / lockout
fields are exposed.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import USER_ROLES


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    email: EmailStr
    role: str
    staff_id: UUID | None = None
    must_change_password: bool = False
    failed_login_count: int = 0
    locked_until: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminUserCreate(BaseModel):
    """Caller never supplies a password — server mints a temp_password."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: str = Field(default="staff")
    staff_id: UUID | None = None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    role: str | None = None
    staff_id: UUID | None = None
    must_change_password: bool | None = None


class AdminUserCreateResponse(BaseModel):
    """Returned by POST /admin/users — embeds the freshly minted temp password.

    The plaintext is shown to the admin exactly once; the persisted row stores
    only the bcrypt hash. The companion audit_log entry records the create
    action without the plaintext.
    """

    model_config = ConfigDict(extra="forbid")

    user: AdminUserRead
    temp_password: str


class AdminPasswordResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    temp_password: str


def validate_role(value: str) -> str:
    if value not in USER_ROLES:
        raise ValueError(f"role must be one of {USER_ROLES}, got {value!r}")
    return value
