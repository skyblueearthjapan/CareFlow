"""Schemas for `/api/v1/admin/users` (Wave 4-F).

Mirrors `app/models/user.py` with the Wave 4-F additions (`deleted_at`,
`must_change_password`). Plain CRUD payloads — no rate limiting / lockout
fields are exposed.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import USER_ROLES

# username は小文字英数字 / アンダースコア / ドット / ハイフンのみ許可。
# schema 層で正規化 (strip().lower()) + 文字種検査を行い、DB 側の制約と二重防御する。
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.\-]+$")


def _normalize_username_value(v: object) -> str | None:
    """Normalize username: strip, lowercase, empty → None; validate pattern.

    Shared by AdminUserCreate / AdminUserUpdate so the rule stays in one place.
    """
    if v is None:
        return None
    v = str(v).strip().lower()
    if not v:
        return None
    if len(v) > 32:
        raise ValueError("username must be at most 32 characters")
    if not _USERNAME_PATTERN.match(v):
        raise ValueError("username may only contain a-z, 0-9, hyphen, underscore, dot")
    return v


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    email: EmailStr | None = None
    username: str | None = None
    role: str
    staff_id: UUID | None = None
    staff_name: str | None = None
    must_change_password: bool = False
    failed_login_count: int = 0
    locked_until: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminUserCreate(BaseModel):
    """Caller never supplies a password — server mints a temp_password."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    username: str | None = None
    role: str = Field(default="staff")
    staff_id: UUID | None = None

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, v: object) -> str | None:
        return _normalize_username_value(v)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    username: str | None = None
    role: str | None = None
    staff_id: UUID | None = None
    must_change_password: bool | None = None

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, v: object) -> str | None:
        return _normalize_username_value(v)


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
