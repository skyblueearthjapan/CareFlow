"""Pydantic schemas (auth + minimal entities for skeleton).

Full Read/Create/Update schemas for every entity will be added incrementally
as endpoints come online. Auth-related schemas live in `auth.py`.
"""

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TokenPair,
    UserOut,
)

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "RefreshRequest",
    "TokenPair",
    "UserOut",
]
