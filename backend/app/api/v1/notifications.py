"""Notification endpoints (Wave 4-D).

Routes (under ``/api/v1/notifications``):

  * ``GET    /``                       paginated list (current user)
  * ``PATCH  /{id}/read``              mark single notification read
  * ``POST   /mark-all-read``          mark every unread row read
  * ``POST   /admin-create``           admin-only manual create (test seed)

The producer-side (event listeners) lands in W6; the read-side here is
self-contained and serves the mobile bell-icon UX.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.notification import Notification
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.notification import (
    NotificationAdminCreate,
    NotificationMarkAllReadResponse,
    NotificationRead,
)

router = APIRouter()


@router.get(
    "",
    response_model=Paginated[NotificationRead],
    summary="List notifications for the current user",
)
async def list_notifications(
    db: DbDep,
    user: CurrentActiveUser,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[NotificationRead]:
    base_filters = [Notification.user_id == user.id]
    if unread_only:
        base_filters.append(Notification.read_at.is_(None))

    count_stmt = select(func.count()).select_from(Notification).where(*base_filters)
    list_stmt = (
        select(Notification)
        .where(*base_filters)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.scalars(list_stmt)).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[NotificationRead](
        items=[NotificationRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationRead,
    summary="Mark a single notification as read",
)
async def mark_notification_read(
    notification_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Notification:
    row = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
    return row


@router.post(
    "/mark-all-read",
    response_model=NotificationMarkAllReadResponse,
    summary="Mark every unread notification as read for the current user",
)
async def mark_all_notifications_read(
    db: DbDep,
    user: CurrentActiveUser,
) -> NotificationMarkAllReadResponse:
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()
    return NotificationMarkAllReadResponse(updated=int(result.rowcount or 0))


@router.post(
    "/admin-create",
    response_model=NotificationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Admin-only manual notification create (W6 producers will replace this)",
)
async def admin_create_notification(
    payload: NotificationAdminCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> Notification:
    row = Notification(
        user_id=payload.user_id,
        type=payload.type,
        title=payload.title,
        body=payload.body,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
