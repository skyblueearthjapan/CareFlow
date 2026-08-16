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

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.course import Course
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.schemas.v2.enums import CourseStatus
from app.services.accompaniment import (
    AccompanimentDutyWarning,
    collect_accompaniment_duty_warnings,
    notify_accompaniment_duty_conflict,
)
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings,
    notify_constraint_override,
)
from app.services.op_log_service import record_op
from app.services.scheduling import (
    CourseProposal,
    Layer2Clusterer,
    Layer2ClusterError,
    Layer3Assigner,
    Layer3AssignmentError,
    StaffAssignment,
)

logger = logging.getLogger(__name__)

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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
) -> CourseRead:
    # CourseCreate (=CourseV2Create) は Pydantic 側でフィールド型を担保しているため、
    # そのまま ORM へ流し込む。CourseStatus enum -> str.
    data = payload.model_dump(mode="json")
    # CourseStatus は v2 schema では enum (StrEnum). model_dump(mode='json') で str に。
    # UUID 列は model_dump(mode='json') が str 化するため、ORM 列が期待する UUID
    # オブジェクトへ戻す (W15-BE-FIXPATTERN: office_id を NOT NULL 化したことに伴う対応)。
    for uuid_field in ("office_id", "assigned_staff_id"):
        val = data.get(uuid_field)
        if isinstance(val, str):
            try:
                data[uuid_field] = UUID(val)
            except (ValueError, TypeError):
                # Pydantic で弾けるはずだが念のため None 化 (FK 違反で 422 経由)
                data[uuid_field] = None
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
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> CourseRead:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(mode="json", exclude_unset=True)
    # Wave U-3: op_group_id は Course モデルに存在しない — 取り出してから適用
    _op_group_id_raw: str | None = update_data.pop("op_group_id", None)
    _op_group_id: UUID | None = UUID(_op_group_id_raw) if _op_group_id_raw else None
    # NG スタッフ / 性別制限の確認フロー (§7-2): 入力専用フィールドなので同様に取り出す。
    _acknowledge: bool = bool(update_data.pop("acknowledge_constraint_warnings", False))

    # model_dump(mode="json") で UUID が str 化されるため、ORM 列用に UUID オブジェクトへ戻す
    # (create_course と同じ対応: PG_UUID(as_uuid=True) は uuid.UUID を要求する)
    for uuid_field in ("assigned_staff_id", "office_id"):
        val = update_data.get(uuid_field)
        if isinstance(val, str):
            try:
                update_data[uuid_field] = UUID(val)
            except (ValueError, TypeError):
                # Pydantic で弾けるはずの不正 UUID。黙って null 化せず痕跡を残す
                # (レビュー指摘・実害時の調査用)。
                logger.warning("update_course: 不正な %s をスキップ: %r", uuid_field, val)
                update_data[uuid_field] = None

    # 新人同行 §8: 新人 (is_trainee=true) はコース担当にできない。
    # マスタ駆動なのでフラグ OFF で自動復帰する。担当は「同行」で割り当てる。
    _new_assigned = update_data.get("assigned_staff_id")
    _cand: Staff | None = None
    _constraint_warnings: list[ConstraintWarning] = []
    _accompaniment_warnings: list[AccompanimentDutyWarning] = []
    if "assigned_staff_id" in update_data and _new_assigned is not None:
        _cand = await db.scalar(
            select(Staff).where(Staff.id == _new_assigned, Staff.deleted_at.is_(None))
        )
        if _cand is not None and _cand.is_trainee:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="新人はコース担当にできません（同行で割り当ててください）",
            )

        # NG スタッフ / 性別制限の確認フロー (docs/plans/patient-ng-staff-design.md §7-2)。
        # 新人 422 の **後** に置く: あちらは override 不可の絶対ブロック、こちらは
        # acknowledge で通せる確認付き警告。絶対ブロックを先に効かせる方が自然で、
        # 弾かれる担当に対して患者スキャンの追加クエリも走らない。
        # 担当解除 (None 化) と、assigned_staff_id を含まない PATCH は無検査。
        if _cand is not None:
            _constraint_warnings = await collect_constraint_warnings(
                db, course_id=course.id, staff=_cand
            )
        if _constraint_warnings and not _acknowledge:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "constraint_confirmation_required",
                    "warnings": [w.to_detail() for w in _constraint_warnings],
                },
            )

        # 逆方向の警告 (一般化 決定#1 後段): 同行を先に登録してから担当を割り当てる
        # 向きは**ブロックしない** (エンジンのハード対応は別案件)。このコースの訪問と
        # その人の同行が**時間帯で交差**するときだけ警告 + 管理者通知を出す
        # (同日というだけで鳴らすとノイズになる — レビュー M-4)。
        # 新人 422 / NG・性別 422 の**後** = 弾かれる担当に対して余計なクエリを走らせない。
        if _cand is not None:
            _accompaniment_warnings = await collect_accompaniment_duty_warnings(
                db, staff_id=_cand.id, course_ids=[course.id]
            )

    # assigned_staff_id 変更を op_log に記録するため変更前の値を保存
    _old_staff_id: UUID | None = course.assigned_staff_id
    _staff_id_changing = "assigned_staff_id" in update_data

    for field, value in update_data.items():
        setattr(course, field, value)

    # 担当変更を「このコースの visits」へ伝播する。
    # 訪問モニター / モバイル「今日の訪問」/ ダッシュボード等は visits.primary_staff_id を
    # 参照するため、course.assigned_staff_id だけ変えると担当変更後に表示がズレる
    # (PO報告 2026-07-09: モニターとスケジュールの担当が食い違う)。手動上書き visit は尊重。
    if _staff_id_changing:
        _new_staff_id: UUID | None = update_data.get("assigned_staff_id")
        # モニター/モバイル/ダッシュボードが読む primary_staff_id を一括更新 (bulk UPDATE)。
        # VSA(正典) は連携週スケジュール(コース担当優先)・layer3 再実行で吸収されるため
        # ここでは触らない (相方や async セッションを巻き込まない・単純で確実)。
        await db.execute(
            sa_update(Visit)
            .where(
                Visit.course_id == course.id,
                Visit.deleted_at.is_(None),
                Visit.manual_staff_override.is_(False),
            )
            .values(primary_staff_id=_new_staff_id)
        )

    # §7-3: acknowledge 付きで制約を通した事実を管理者へお知らせする。
    # commit 前に add = 担当変更と同一トランザクション (適用されたものだけ通知される)。
    if _constraint_warnings and _cand is not None:
        await notify_constraint_override(
            db,
            kind_summary={w.kind for w in _constraint_warnings},
            course=course,
            staff=_cand,
            patient_warnings=_constraint_warnings,
            actor=current_user,
            op_group_id=_op_group_id,
        )

    # 一般化 決定#1 後段: 同行と担当が同日に重なった事実を管理者へお知らせする
    # (ブロックはしない)。commit 前に add = 担当変更と同一トランザクション。
    if _accompaniment_warnings:
        await notify_accompaniment_duty_conflict(
            db,
            warnings=_accompaniment_warnings,
            actor=current_user,
            op_group_id=_op_group_id,
        )

    await _commit_or_409(db)
    await db.refresh(course)

    # Wave U-3: assigned_staff_id 変更のみ記録（ベストエフォート）
    if _staff_id_changing:
        _new_staff_id = course.assigned_staff_id
        _new_staff_str = str(_new_staff_id) if _new_staff_id else None
        _old_staff_str = str(_old_staff_id) if _old_staff_id else None
        _label = f"コース担当者を変更（→{'未割当' if _new_staff_str is None else _new_staff_str[:8] + '...'}）"
        await record_op(
            db,
            user_id=current_user.id,
            iso_year=course.iso_year,
            iso_week=course.iso_week,
            op_group_id=_op_group_id,
            op_kind="patch_course_staff",
            label=_label,
            forward_payload={
                "op": "set_course_staff",
                "course_id": str(course_id),
                "staff_id": _new_staff_str,
            },
            inverse_payload={
                "op": "set_course_staff",
                "course_id": str(course_id),
                "staff_id": _old_staff_str,
            },
        )
        await db.commit()

    # 逆方向の警告は**レスポンスにも**載せる (非破壊追加・既定 [])。
    # 通知に加えてレスポンスにも載せる (FE のトースト表示は未配線 = Phase E 予定。
    # 現時点で本人への即時提示は管理者ベル通知のみ)。
    return _to_read(course).model_copy(
        update={"accompaniment_warnings": [w.to_payload() for w in _accompaniment_warnings]}
    )


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
