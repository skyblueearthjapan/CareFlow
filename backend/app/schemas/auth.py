"""Auth-related request/response schemas."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=255)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """Public-safe view of a User row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr | None = None
    username: str | None = None
    role: str
    staff_id: UUID | None = None
    # Wave 40: surfaced so the frontend can force a password-change flow on
    # next sign-in. Defaults to False so older callers stay unaffected.
    must_change_password: bool = False


class LoginResponse(BaseModel):
    user: UserOut
    tokens: TokenPair


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Wave 40 — self-service password change
# ---------------------------------------------------------------------------

# Same shape as the FE Zod schema (`/settings/password`): min 8 chars and at
# least one letter + one digit. The pattern is enforced server-side as the
# source of truth — the FE check is purely a UX nicety.
_PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


class PasswordChangeRequest(BaseModel):
    """Body of `POST /api/v1/auth/change-password`.

    The new password must:
      * be at least 8 characters long
      * contain at least one letter and one digit
      * differ from `current_password` (rejected at the route layer so the
        message can mention the comparison without leaking the current value
        through Pydantic error context)
    """

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)

    @field_validator("new_password")
    @classmethod
    def _validate_complexity(cls, value: str) -> str:
        if not _PASSWORD_RULE.match(value):
            raise ValueError(
                "new_password must be at least 8 characters and contain both letters and digits",
            )
        return value
