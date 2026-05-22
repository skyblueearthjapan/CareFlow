"""Office feature-flag endpoints (Phase G-21 T2).

GET    /office-feature-flags[?feature_key=X]
POST   /office-feature-flags                       (UPSERT, admin only)
DELETE /office-feature-flags/{office_id}/{feature_key}  (admin only)

* 拠点単位の canary 3 phase 制御に使う.
* UPSERT は ``(office_id, feature_key)`` を一意とみなす.
* ``enabled=true`` で ``enabled_at=NOW()``, ``enabled=false`` で ``enabled_at=NULL``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.user import User
from app.schemas.office_feature_flag import (
    OfficeFeatureFlagRead,
    OfficeFeatureFlagSet,
)

router = APIRouter()


@router.get(
    "",
    response_model=list[OfficeFeatureFlagRead],
    summary="Phase G-21: office feature flag 一覧 (admin/manager)",
)
async def list_flags(
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin", "manager"))],
    feature_key: str | None = Query(default=None, description="feature_key で絞り込み"),
) -> list[OfficeFeatureFlagRead]:
    stmt = select(OfficeFeatureFlag).order_by(
        OfficeFeatureFlag.feature_key, OfficeFeatureFlag.office_id
    )
    if feature_key is not None:
        stmt = stmt.where(OfficeFeatureFlag.feature_key == feature_key)
    rows = (await db.scalars(stmt)).all()
    return [OfficeFeatureFlagRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=OfficeFeatureFlagRead,
    summary="Phase G-21: office feature flag UPSERT (admin only)",
)
async def set_flag(
    payload: OfficeFeatureFlagSet,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> OfficeFeatureFlagRead:
    """``(office_id, feature_key)`` を UPSERT.

    ``enabled=true`` の場合は ``enabled_at=NOW()`` / ``enabled_by_user_id=actor.id``.
    ``enabled=false`` の場合は ``enabled_at=NULL`` / ``enabled_by_user_id=NULL``.
    audit_logs に ``action="office_feature_flag_set"`` を記録.
    """
    existing = await db.scalar(
        select(OfficeFeatureFlag).where(
            OfficeFeatureFlag.office_id == payload.office_id,
            OfficeFeatureFlag.feature_key == payload.feature_key,
        )
    )

    if existing is None:
        flag = OfficeFeatureFlag(
            office_id=payload.office_id,
            feature_key=payload.feature_key,
            enabled_at=func.now() if payload.enabled else None,
            enabled_by_user_id=actor.id if payload.enabled else None,
            note=payload.note,
        )
        db.add(flag)
        # Phase G-21 final H2: audit_logs.target_id は String(64). 旧実装は
        # f"{office_id}:{feature_key}" (= UUID 36 + ":" + 最大 64 = 101 char) で
        # overflow リスクがあったため、target_id は office_id のみとし、
        # feature_key は after JSONB に持たせる.
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="office_feature_flag_set",
                target_table="office_feature_flags",
                target_id=str(payload.office_id),
                before=None,
                after={
                    "feature_key": payload.feature_key,
                    "enabled": payload.enabled,
                    "note": payload.note,
                },
            )
        )
        await db.commit()
        await db.refresh(flag)
        return OfficeFeatureFlagRead.model_validate(flag)

    before = {
        "feature_key": payload.feature_key,
        "enabled": existing.enabled_at is not None,
        "note": existing.note,
    }
    if payload.enabled:
        existing.enabled_at = func.now()
        existing.enabled_by_user_id = actor.id
    else:
        existing.enabled_at = None
        existing.enabled_by_user_id = None
    existing.note = payload.note
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="office_feature_flag_set",
            target_table="office_feature_flags",
            target_id=str(payload.office_id),
            before=before,
            after={
                "feature_key": payload.feature_key,
                "enabled": payload.enabled,
                "note": payload.note,
            },
        )
    )
    await db.commit()
    await db.refresh(existing)
    return OfficeFeatureFlagRead.model_validate(existing)


@router.delete(
    "/{office_id}/{feature_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Phase G-21: office feature flag 削除 (admin only)",
)
async def delete_flag(
    office_id: UUID,
    feature_key: str,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """flag 行を物理 DELETE する. 行が無くても 204 (冪等)."""
    existing = await db.scalar(
        select(OfficeFeatureFlag).where(
            OfficeFeatureFlag.office_id == office_id,
            OfficeFeatureFlag.feature_key == feature_key,
        )
    )
    if existing is None:
        return None

    before = {
        "feature_key": feature_key,
        "enabled": existing.enabled_at is not None,
        "note": existing.note,
    }
    await db.delete(existing)
    # Phase G-21 final H2: target_id overflow 回避 (String(64) 制約).
    # feature_key は before JSONB に格納する.
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="office_feature_flag_delete",
            target_table="office_feature_flags",
            target_id=str(office_id),
            before=before,
            after=None,
        )
    )
    await db.commit()
    return None
