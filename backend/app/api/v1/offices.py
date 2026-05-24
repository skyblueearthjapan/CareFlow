"""Office CRUD endpoints (Phase 2 domain router).

Staff role is read-only (GET list/detail). Mutations are admin/manager;
soft-delete is admin only.

W1-BE3 (v2): adds `POST /offices/resolve` — patient address → office auto
assignment using `OfficeAssigner`. The CRUD handlers below are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import DbDep, require_role
from app.models.office import Office, OfficeCity
from app.models.user import User
from app.schemas.office import OfficeCreate, OfficeRead, OfficeUpdate
from app.services.office_assigner import OfficeAssigner

router = APIRouter()


# ---------------------------------------------------------------------------
# W1-BE3: Address → Office resolver
# ---------------------------------------------------------------------------


class OfficeResolveRequest(BaseModel):
    """`POST /offices/resolve` 入力。

    `docs/plans/v2-api-contracts.md` §3.1 に対応。
    """

    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, description="患者住所（市区町村名を含む）")


class OfficeResolveResponse(BaseModel):
    """`POST /offices/resolve` 出力。

    `docs/plans/v2-api-contracts.md` §3.1 に対応。
    """

    model_config = ConfigDict(extra="forbid")

    office_id: UUID | None
    office_name: str | None
    matched_city_id: UUID | None
    confidence: Literal["exact", "fuzzy", "none"]


@router.post(
    "/resolve",
    response_model=OfficeResolveResponse,
    summary="Resolve office for a patient address (W1-BE3)",
)
async def resolve_office(
    payload: OfficeResolveRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> OfficeResolveResponse:
    """住所文字列から該当する拠点を自動判定する。

    `OfficeAssigner.resolve_with_details` の薄いラッパ。エンドポイントは
    患者作成・更新画面の補助 UI（自動判定ヒント表示）から呼ばれる想定。
    患者マスタへの実際の `primary_office_id` 書込みは W1-BE1 (`patients.py`)
    が担当する。
    """
    result = await OfficeAssigner.resolve_with_details(db, payload.address)
    return OfficeResolveResponse(
        office_id=result.office.id if result.office is not None else None,
        office_name=result.office.name if result.office is not None else None,
        matched_city_id=result.matched_city_id,
        confidence=result.confidence,
    )


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into 409/422 (see patients.py)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


def _to_read(office: Office) -> OfficeRead:
    """Construct OfficeRead and inject allowed_cities from M2M relationship."""
    data = OfficeRead.model_validate(office, from_attributes=True).model_dump()
    data["allowed_cities"] = [oc.city_id for oc in office.cities]
    return OfficeRead.model_validate(data)


@router.get("", response_model=list[OfficeRead], summary="List offices")
async def list_offices(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    q: Annotated[str | None, Query(description="Substring filter on name/code")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OfficeRead]:
    stmt = (
        select(Office)
        .where(Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
        # 登録ナンバー (code) 昇順で常に固定表示。code 未設定は末尾、同コードは登録順で安定化。
        .order_by(Office.code.asc().nulls_last(), Office.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Office.name.ilike(like), Office.code.ilike(like)))
    rows = (await db.scalars(stmt)).all()
    return [_to_read(o) for o in rows]


@router.get("/{office_id}", response_model=OfficeRead, summary="Get office by id")
async def get_office(
    office_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
) -> OfficeRead:
    office = await db.scalar(
        select(Office)
        .where(Office.id == office_id, Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_read(office)


async def _replace_office_cities(db, office_id: UUID, city_ids: list[UUID]) -> None:
    """Delete existing M2M rows then re-add the given city ids."""
    await db.execute(delete(OfficeCity).where(OfficeCity.office_id == office_id))
    for city_id in city_ids:
        db.add(OfficeCity(office_id=office_id, city_id=city_id))


@router.post(
    "",
    response_model=OfficeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create office",
)
async def create_office(
    payload: OfficeCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> OfficeRead:
    data = payload.model_dump(exclude={"allowed_cities"})
    office = Office(**data)
    db.add(office)
    await db.flush()  # populate office.id

    if payload.allowed_cities:
        await _replace_office_cities(db, office.id, payload.allowed_cities)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(Office).where(Office.id == office.id).options(selectinload(Office.cities))
    )
    assert refreshed is not None
    return _to_read(refreshed)


@router.patch("/{office_id}", response_model=OfficeRead, summary="Update office")
async def update_office(
    office_id: UUID,
    payload: OfficeUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> OfficeRead:
    office = await db.scalar(
        select(Office)
        .where(Office.id == office_id, Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(exclude_unset=True)
    allowed_cities = update_data.pop("allowed_cities", None)
    # Phase G-45: ``operating_weekdays`` は NOT NULL カラムなので、 明示的に
    # None で送られても無視する (= 既存値を保護). 同様に他カラムも None で送られた
    # 場合は触らない方が安全な振る舞いだが、後方互換のため operating_weekdays のみ
    # 限定的にガードする (= 旧来 name/code/note 等の None 上書きは許容のまま).
    for field, value in update_data.items():
        if field == "operating_weekdays" and value is None:
            continue
        setattr(office, field, value)

    if allowed_cities is not None:
        await _replace_office_cities(db, office.id, allowed_cities)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(Office).where(Office.id == office.id).options(selectinload(Office.cities))
    )
    assert refreshed is not None
    return _to_read(refreshed)


@router.delete(
    "/{office_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete office (admin only)",
)
async def delete_office(
    office_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    office.deleted_at = func.now()
    await db.commit()
    return None
