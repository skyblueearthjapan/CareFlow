"""Password hashing (bcrypt) and JWT issuance/verification (HS256 by default)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

# bcrypt is the only configured scheme; passlib will warn if more added.
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- Password hashing ----------


def hash_password(plain: str) -> str:
    """Return bcrypt hash for a plaintext password."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify a plaintext password against its bcrypt hash."""
    try:
        return _pwd_ctx.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _build_claims(
    *,
    subject: str | UUID,
    role: str,
    staff_id: str | UUID | None,
    ttl_seconds: int,
    token_type: str,
) -> dict[str, Any]:
    iat = _now()
    exp = iat + timedelta(seconds=ttl_seconds)
    return {
        "sub": str(subject),
        "role": role,
        "staff_id": str(staff_id) if staff_id is not None else None,
        "type": token_type,
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
    }


def create_access_token(
    *,
    subject: str | UUID,
    role: str,
    staff_id: str | UUID | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Issue a short-lived access JWT.

    Claims: {sub, role, staff_id, type=access, iat, exp}
    """
    settings = get_settings()
    claims = _build_claims(
        subject=subject,
        role=role,
        staff_id=staff_id,
        ttl_seconds=ttl_seconds or settings.jwt_access_ttl_seconds,
        token_type="access",
    )
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    *,
    subject: str | UUID,
    role: str,
    staff_id: str | UUID | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Issue a longer-lived refresh JWT (same shape, type=refresh)."""
    settings = get_settings()
    claims = _build_claims(
        subject=subject,
        role=role,
        staff_id=staff_id,
        ttl_seconds=ttl_seconds or settings.jwt_refresh_ttl_seconds,
        token_type="refresh",
    )
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode + validate a JWT, raising JWTError on any failure (signature/exp/etc.)."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


__all__ = [
    "JWTError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
