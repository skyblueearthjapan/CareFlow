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
from app.models.city import City
from app.models.office import Office, OfficeCity
from app.models.office_area_prompt_dismissal import OfficeAreaPromptDismissal
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


class MatchedCity(BaseModel):
    """resolve が特定した市区町村 (W-7 地域ルールの学習)。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    prefecture: str


class OfficeResolveResponse(BaseModel):
    """`POST /offices/resolve` 出力。

    `docs/plans/v2-api-contracts.md` §3.1 に対応。

    W-7 拡張 (`docs/plans/region-rule-learning-design.md` §2): `confidence='none'`
    かつ住所から City を特定できた場合に `matched_city` と `prompt_dismissed` を返す。
    カバー済み地域 (`exact`/`fuzzy`) では従来どおり `matched_city=None` /
    `prompt_dismissed=False` (後方互換)。
    """

    model_config = ConfigDict(extra="forbid")

    office_id: UUID | None
    office_name: str | None
    matched_city_id: UUID | None
    confidence: Literal["exact", "fuzzy", "none"]
    # W-7: 未カバー地域で City を特定できたときのみ非 None。
    matched_city: MatchedCity | None = None
    # W-7: matched_city が非 None のときのみ意味を持つ (却下済みなら true)。
    prompt_dismissed: bool = False


@router.post(
    "/resolve",
    response_model=OfficeResolveResponse,
    summary="Resolve office for a patient address (W1-BE3)",
)
async def resolve_office(
    payload: OfficeResolveRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> OfficeResolveResponse:
    """住所文字列から該当する拠点を自動判定する。

    `OfficeAssigner.resolve_with_details` の薄いラッパ。エンドポイントは
    患者作成・更新画面の補助 UI（自動判定ヒント表示）から呼ばれる想定。
    患者マスタへの実際の `primary_office_id` 書込みは W1-BE1 (`patients.py`)
    が担当する。

    W-7: `confidence='none'` かつ住所から City を特定できた場合、その City の
    表示情報 (`matched_city`) と却下記憶の有無 (`prompt_dismissed`) を付与する。
    """
    result = await OfficeAssigner.resolve_with_details(db, payload.address)

    matched_city: MatchedCity | None = None
    prompt_dismissed = False
    # 未カバー地域 (confidence=none) かつ City を特定できたときだけ学習用情報を返す。
    # カバー済み地域のレスポンスは完全に不変に保つ (後方互換)。
    if result.confidence == "none" and result.matched_city_id is not None:
        matched_city = MatchedCity(
            id=result.matched_city_id,
            name=result.matched_city_name or "",
            prefecture=result.matched_city_prefecture or "",
        )
        prompt_dismissed = (
            await db.scalar(
                select(func.count())
                .select_from(OfficeAreaPromptDismissal)
                .where(OfficeAreaPromptDismissal.city_id == result.matched_city_id)
            )
        ) > 0

    return OfficeResolveResponse(
        office_id=result.office.id if result.office is not None else None,
        office_name=result.office.name if result.office is not None else None,
        matched_city_id=result.matched_city_id,
        confidence=result.confidence,
        matched_city=matched_city,
        prompt_dismissed=prompt_dismissed,
    )


# ---------------------------------------------------------------------------
# W-7: 地域ルールの学習 (担当エリアの追加 / 呼びかけ却下記憶)
# ---------------------------------------------------------------------------


class AreaCityAddRequest(BaseModel):
    """`POST /offices/{office_id}/area-cities` 入力 (W-7)。"""

    model_config = ConfigDict(extra="forbid")

    city_id: UUID


class AreaCityAddResponse(BaseModel):
    """`POST /offices/{office_id}/area-cities` 出力 (W-7)。"""

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    city_id: UUID
    city_name: str


class AreaPromptDismissalRequest(BaseModel):
    """`POST /offices/area-prompt-dismissals` 入力 (W-7)。"""

    model_config = ConfigDict(extra="forbid")

    city_id: UUID


@router.post(
    "/area-prompt-dismissals",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Dismiss the area-registration prompt for a city (W-7)",
)
async def dismiss_area_prompt(
    payload: AreaPromptDismissalRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """未カバー地域の担当エリア登録呼びかけを組織として却下し、記憶する (W-7)。

    冪等: 既に却下済み (同 city_id が存在) の場合も 204 を返す。以後その City は
    resolve の `prompt_dismissed=true` として全ユーザー・全画面で呼びかけない。
    """
    city_exists = await db.scalar(
        select(City.id).where(City.id == payload.city_id, City.deleted_at.is_(None))
    )
    if city_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="City not found")
    existing = await db.scalar(
        select(OfficeAreaPromptDismissal.id).where(
            OfficeAreaPromptDismissal.city_id == payload.city_id
        )
    )
    if existing is None:
        db.add(OfficeAreaPromptDismissal(city_id=payload.city_id))
        await _commit_or_409(db)
    return None


@router.post(
    "/{office_id}/area-cities",
    response_model=AreaCityAddResponse,
    summary="Add a single city to an office's coverage area (W-7)",
)
async def add_office_area_city(
    office_id: UUID,
    payload: AreaCityAddRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> AreaCityAddResponse:
    """拠点の担当エリアに City を 1 件追加する (additive / 冪等) (W-7)。

    - 全置換 PUT (`PATCH /offices/{id}` の allowed_cities) とは別の追加専用 API。
      患者登録の途中で拠点フォーム全体を触らせないための最小操作。
    - 既に紐付済みなら 200 冪等。別拠点が既に同 City を担当していても追加を許す
      (複数拠点担当は既存モデルで合法)。
    - 追加成功時、その City の却下記憶があれば削除する (登録された地域に「聞かない」
      記憶が残らないように)。
    """
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    city = await db.scalar(
        select(City).where(City.id == payload.city_id, City.deleted_at.is_(None))
    )
    if city is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="City not found")

    existing = await db.scalar(
        select(OfficeCity).where(
            OfficeCity.office_id == office_id,
            OfficeCity.city_id == payload.city_id,
        )
    )
    if existing is None:
        db.add(OfficeCity(office_id=office_id, city_id=payload.city_id))

    # 登録された地域の却下記憶は削除 (二度と聞かない記憶が残ると混乱するため)。
    await db.execute(
        delete(OfficeAreaPromptDismissal).where(
            OfficeAreaPromptDismissal.city_id == payload.city_id
        )
    )

    await _commit_or_409(db)

    return AreaCityAddResponse(
        office_id=office_id,
        city_id=payload.city_id,
        city_name=city.name,
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
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
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
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
