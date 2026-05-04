"""Admin-only endpoints (RBAC scaffold).

The concrete user-management API lands in Phase 2 — this module exists so
`require_role()` is exercised end-to-end (Codex/Critic review ask) and the
admin role boundary is verifiable today.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import require_role
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/users",
    summary="List users (admin only — stub)",
    response_model=list[dict],
)
async def list_users(
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> list[dict]:
    """Stub admin endpoint: returns an empty list.

    Real implementation (filters, pagination, soft-delete handling) ships in
    Phase 2. The role guard is the load-bearing piece today.
    """
    return []
