"""Audit-log read API (Wave 4-F).

`GET /api/v1/audit-logs` — admin-only listing with the standard pagination
envelope. Filters: time range, actor, role, path substring, method,
status_code. Rows are returned with the request body already PII-redacted
(the middleware persists them that way), so the response is safe to render
in the admin UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.audit_log import AuditLogRead

router = APIRouter()


@router.get(
    "",
    response_model=Paginated[AuditLogRead],
    summary="List audit log rows (admin only)",
)
async def list_audit_logs(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
    since: Annotated[datetime | None, Query(description="created_at >= since")] = None,
    until: Annotated[datetime | None, Query(description="created_at < until")] = None,
    user_id: Annotated[UUID | None, Query(description="Filter by actor user id")] = None,
    role: Annotated[str | None, Query()] = None,
    path: Annotated[str | None, Query(description="Substring match on URL path")] = None,
    method: Annotated[str | None, Query()] = None,
    status_code: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[AuditLogRead]:
    conds = []
    if since is not None:
        conds.append(AuditLog.created_at >= since)
    if until is not None:
        conds.append(AuditLog.created_at < until)
    if user_id is not None:
        conds.append(AuditLog.actor_user_id == user_id)
    if role is not None:
        conds.append(AuditLog.role == role)
    if path is not None:
        conds.append(AuditLog.path.like(f"%{path}%"))
    if method is not None:
        conds.append(AuditLog.method == method.upper())
    if status_code is not None:
        conds.append(AuditLog.status_code == status_code)

    base = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)
    if conds:
        base = base.where(and_(*conds))
        count_stmt = count_stmt.where(and_(*conds))

    rows = (
        await db.scalars(
            base.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[AuditLogRead](
        items=[AuditLogRead.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
