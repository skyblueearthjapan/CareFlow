"""Course CRUD + Layer 2 (generate) + Layer 3 (assign-staff) endpoints.

`docs/plans/v2-allocation-redesign.md` v0.9 §4.5 / §5.3 / §5.4 に対応する Course
(コース) のリソース API。

- W2-BE4: CRUD (`GET / POST / PATCH / DELETE`)
- W4-BE8: `POST /generate` (Layer 2: K-means + 制約後処理)
- W4-BE9: `POST /assign-staff` (Layer 3: ハンガリアン法 + ローテーション)

## RBAC (API 契約 §4)

- GET (list / detail)  — admin / manager
- POST / PATCH         — admin / manager
- DELETE (soft delete) — admin only
- POST /generate       — admin / manager (W4-BE8)
- POST /assign-staff   — admin / manager (W4-BE9)

## UNIQUE 制約

(iso_year, iso_week, weekday, code) UNIQUE — DB 側で担保。
重複した POST / PATCH は 409 Conflict を返す。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.course import Course
from app.models.user import User
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.schemas.v2.enums import CourseStatus
from app.services.scheduling import (
    CourseProposal,
    Layer2Clusterer,
    Layer2ClusterError,
    Layer3Assigner,
    Layer3AssignmentError,
    StaffAssignment,
)

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Translate IntegrityError into 409 / 422 (see patients.py / offices.py)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate course (iso_year, iso_week, weekday, code)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key or check constraint",
        ) from exc


def _to_read(course: Course) -> CourseRead:
    return CourseRead.model_validate(course, from_attributes=True)


@router.get("", response_model=list[CourseRead], summary="List courses")
async def list_courses(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    iso_week: Annotated[int | None, Query(ge=1, le=53)] = None,
    weekday: Annotated[int | None, Query(ge=0, le=6)] = None,
    course_status: Annotated[CourseStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CourseRead]:
    stmt = (
        select(Course)
        .where(Course.deleted_at.is_(None))
        .order_by(Course.iso_year, Course.iso_week, Course.weekday, Course.code)
        .limit(limit)
        .offset(offset)
    )
    if iso_year is not None:
        stmt = stmt.where(Course.iso_year == iso_year)
    if iso_week is not None:
        stmt = stmt.where(Course.iso_week == iso_week)
    if weekday is not None:
        stmt = stmt.where(Course.weekday == weekday)
    if course_status is not None:
        stmt = stmt.where(Course.course_status == course_status.value)

    rows = (await db.scalars(stmt)).all()
    return [_to_read(c) for c in rows]


# ---------------------------------------------------------------------------
# W4-BE8 — POST /generate (Layer 2 アルゴリズム)
# ---------------------------------------------------------------------------


class CourseGenerateRequest(BaseModel):
    """``POST /api/v1/courses/generate`` のリクエストボディ (W4-BE8).

    Layer 2 (§5.3) を当該曜日に対して実行する。``staff_count`` は省略時 4。
    ``random_state`` を指定すると K-means の初期化が再現可能になる
    (受入基準 2 で fixture 評価に使用)。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    weekday: int = Field(ge=0, le=6)
    staff_count: int = Field(default=4, ge=1, le=4)
    random_state: int | None = Field(default=None)


class CourseGenerateResponse(BaseModel):
    """``POST /api/v1/courses/generate`` のレスポンス (W4-BE8).

    `proposals` は案 (proposed) のみ。DB へのコース確定は別エンドポイント
    (将来の `POST /api/v1/courses/{id}/fix`) で行う。
    """

    model_config = ConfigDict(extra="forbid")

    proposals: list[CourseProposal]
    total_distance_km: float
    validity_score: float


@router.post(
    "/generate",
    response_model=CourseGenerateResponse,
    status_code=status.HTTP_200_OK,
    summary="Layer 2: generate course proposals (W4-BE8)",
)
async def generate_courses(
    payload: CourseGenerateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseGenerateResponse:
    """Layer 2 アルゴリズムを実行し、コース分け案を返す.

    本エンドポイントは案を返すのみで DB を変更しない (commit しない)。
    確定は別途 ``POST /api/v1/courses`` (CRUD) でコース作成する。
    """
    clusterer = Layer2Clusterer()
    try:
        result = await clusterer.generate_proposals(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            weekday=payload.weekday,
            staff_count=payload.staff_count,
            random_state=payload.random_state,
        )
    except Layer2ClusterError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    return CourseGenerateResponse(
        proposals=result.proposals,
        total_distance_km=result.total_distance_km,
        validity_score=result.validity_score,
    )


# ---------------------------------------------------------------------------
# W4-BE9 — POST /assign-staff (Layer 3 アルゴリズム)
# ---------------------------------------------------------------------------


class CourseAssignStaffRequest(BaseModel):
    """``POST /api/v1/courses/assign-staff`` のリクエストボディ (W4-BE9).

    Layer 3 (§5.4) を当該週に対して実行する。週単位の処理 — 内部では曜日ごとに
    独立にハンガリアン法を解き、結果をマージする。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)


class CourseAssignStaffResponse(BaseModel):
    """``POST /api/v1/courses/assign-staff`` のレスポンス (W4-BE9).

    `assignments` は割当結果のリスト (1 件 = 1 (weekday, course_code, staff_id)).
    `rotation_score` は Gini 係数 (0.0=完全均等, 1.0=1 人独占).
    `total_distance_km` は (主拠点 → コース重心) の Haversine 合計。
    """

    model_config = ConfigDict(extra="forbid")

    assignments: list[StaffAssignment]
    rotation_score: float
    total_distance_km: float


@router.post(
    "/assign-staff",
    response_model=CourseAssignStaffResponse,
    status_code=status.HTTP_200_OK,
    summary="Layer 3: assign staff to courses (W4-BE9)",
)
async def assign_staff_to_courses(
    payload: CourseAssignStaffRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseAssignStaffResponse:
    """Layer 3 アルゴリズムを実行し、当該週の確定済みコースにスタッフを割り付ける.

    対象は ``course_status='course_fixed'`` のコースのみ。実行成功時、対象コース
    は ``staff_assigned`` に遷移し、``assigned_staff_id`` / ``staff_assigned_at``
    が埋まる (1 トランザクションで commit)。

    ハード制約 (性別 / 勤務曜日 / 1 コース 1 スタッフ / マネージャー除外) を満た
    せない場合、該当 (weekday × course) は割当結果に含まれない (= スキップ)。
    呼び出し側は returned ``assignments`` の件数を確認すること。
    """
    assigner = Layer3Assigner()
    try:
        result = await assigner.assign(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
        )
    except Layer3AssignmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    # サービス層は flush のみ。HTTP 層が commit を担う。
    await db.commit()

    return CourseAssignStaffResponse(
        assignments=result.assignments,
        rotation_score=result.rotation_score,
        total_distance_km=result.total_distance_km,
    )


@router.get("/{course_id}", response_model=CourseRead, summary="Get course by id")
async def get_course(
    course_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_read(course)


@router.post(
    "",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create course",
)
async def create_course(
    payload: CourseCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    # CourseCreate (=CourseV2Create) は Pydantic 側でフィールド型を担保しているため、
    # そのまま ORM へ流し込む。CourseStatus enum -> str.
    data = payload.model_dump(mode="json")
    # CourseStatus は v2 schema では enum (StrEnum). model_dump(mode='json') で str に。
    course = Course(**data)
    db.add(course)
    await _commit_or_409(db)
    await db.refresh(course)
    return _to_read(course)


@router.patch("/{course_id}", response_model=CourseRead, summary="Update course")
async def update_course(
    course_id: UUID,
    payload: CourseUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(mode="json", exclude_unset=True)
    for field, value in update_data.items():
        setattr(course, field, value)

    await _commit_or_409(db)
    await db.refresh(course)
    return _to_read(course)


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete course (admin only)",
)
async def delete_course(
    course_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    course.deleted_at = func.now()
    await db.commit()
    return None
