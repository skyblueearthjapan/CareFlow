"""Business-profile endpoints (事業所の情報・mig 0089).

GET /business-profile  事業所の情報 (全ログインユーザ・QR カードの印刷に使う)
PUT /business-profile  部分更新 (admin)

* DB 単位の単一行 (シングルトン)。行が無ければ全項目 null (初回 PUT で行を作る)。
* 省略した項目は変更しない。明示 null / 空文字は未設定に戻す (カードに載らない)。
* audit_logs に ``business_profile_update`` を記録する。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.business_profile import BusinessProfile
from app.models.user import User
from app.schemas.business_profile import (
    PROFILE_FIELDS,
    BusinessProfileRead,
    BusinessProfileUpdate,
)

router = APIRouter()


async def _load_singleton(db: DbDep) -> BusinessProfile | None:
    return await db.scalar(select(BusinessProfile).where(BusinessProfile.is_singleton.is_(True)))


def _build_read(row: BusinessProfile | None) -> BusinessProfileRead:
    if row is None:
        return BusinessProfileRead()
    return BusinessProfileRead(**{f: getattr(row, f) for f in PROFILE_FIELDS})


@router.get("", response_model=BusinessProfileRead, summary="事業所の情報を取得")
async def get_business_profile(db: DbDep, _actor: CurrentActiveUser) -> BusinessProfileRead:
    return _build_read(await _load_singleton(db))


@router.put("", response_model=BusinessProfileRead, summary="事業所の情報を部分更新 (admin)")
async def update_business_profile(
    payload: BusinessProfileUpdate,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> BusinessProfileRead:
    updates = payload.model_dump(include=payload.model_fields_set)
    row = await _load_singleton(db)
    before: dict[str, object | None]
    if row is None:
        # 初回。最初の PUT が同時に 2 本来ると、後の方はシングルトンの部分 UNIQUE に
        # 当たる。そのときは相手が作った行を読み直して更新として扱う (500 にしない)。
        row = BusinessProfile(is_singleton=True, **updates)
        before = dict.fromkeys(PROFILE_FIELDS)
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            row = await _load_singleton(db)
            if row is None:
                raise
            before = {f: getattr(row, f) for f in PROFILE_FIELDS}
            for field, value in updates.items():
                setattr(row, field, value)
            await db.flush()
    else:
        before = {f: getattr(row, f) for f in PROFILE_FIELDS}
        for field, value in updates.items():
            setattr(row, field, value)
        await db.flush()
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="business_profile_update",
            target_table="business_profile",
            target_id=str(row.id),
            before=before,
            after={f: getattr(row, f) for f in PROFILE_FIELDS},
        )
    )
    await db.commit()
    await db.refresh(row)
    return _build_read(row)
