"""Schedule endpoints (W3-BE-FIX / W4-BE7 / W9-BE2 / W15-BE-FIXPATTERN / W16-BE3).

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6 に対応する
HTTP 層。

実装エンドポイント:
    - POST /api/v1/schedule/fix                  (W3-BE-FIX): 週レイアウト確定
    - POST /api/v1/schedule/generate-week        (W4-BE7): Layer 1 アルゴリズム
    - POST /api/v1/schedule/fix-or-pattern       (W9-BE2): **既存 visit の時刻変更**
    - POST /api/v1/schedule/place-and-fix        (W15-BE-FIXPATTERN): ドロップ即固定枠化
    - POST /api/v1/schedule/generate-and-assign  (W16-BE3): 週生成 + Layer 3 一括実行

## RBAC (API 契約 §6)

- POST /schedule/fix                  — admin / manager のみ (staff は 403)
- POST /schedule/generate-week        — admin / manager のみ (staff は 403)
- POST /schedule/fix-or-pattern       — admin / manager のみ (staff は 403)
- POST /schedule/place-and-fix        — admin / manager のみ (staff は 403)
- POST /schedule/generate-and-assign  — admin / manager のみ (staff は 403)

## トランザクション

各サービス層 (``ScheduleFixService`` / ``Layer1Expander``) は
``await db.flush()`` のみを呼び、``commit()`` / ``rollback()`` は呼ばない。
本 HTTP 層が ``try/except`` で 1 リクエストを 1 トランザクションに包み、
例外時に rollback する。
"""

from __future__ import annotations

from datetime import UTC, date, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_PROPOSED,
    Course,
)
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import Visit
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitMode, PatientFixedVisitV2Read
from app.schemas.v2.visit import VisitV2Read
from app.services.schedule_fix_service import (
    FixedVisit,
    ScheduleFixError,
    ScheduleFixService,
    UpdateVisitLayoutResult,
    WeeklyPatternChange,
)
from app.services.scheduling import (
    Layer1Expander,
    Layer1ExpandError,
    PoolEntry,
    VisitCreated,
)
from app.services.scheduling.layer1_expander import (
    LAYER1_VISIT_SOURCE,
    _is_special_week_active,
)
from app.services.scheduling.layer3_assignment import (
    Layer3Assigner,
    Layer3AssignmentError,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# /fix Request / Response schemas (HTTP 層に閉じる)
# ---------------------------------------------------------------------------


class ScheduleFixRequest(BaseModel):
    """``POST /api/v1/schedule/fix`` のリクエストボディ.

    ``layout`` 内の各 FixedVisit は (patient_id × weekday × start_time)
    の単位。同一患者で複数曜日 / 複数時刻スロットを送る場合は要素を増やす。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    layout: list[FixedVisit] = Field(default_factory=list)


class ScheduleFixResponse(BaseModel):
    """``POST /api/v1/schedule/fix`` のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    patients_updated: int
    weekly_pattern_changes: list[WeeklyPatternChange]


# ---------------------------------------------------------------------------
# /generate-week Request / Response schemas (W4-BE7)
# ---------------------------------------------------------------------------


class GenerateWeekRequest(BaseModel):
    """``POST /api/v1/schedule/generate-week`` のリクエストボディ.

    Layer 1 はステートレス (週指定のみで全 active 患者を再展開する)。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)


class GenerateWeekSummary(BaseModel):
    """Layer 1 結果のサマリ (W4-BE8 / W4-BE9 の入力要約)."""

    model_config = ConfigDict(extra="forbid")

    patients_processed: int
    visits_created: int
    pool_count: int
    special_week_applied_count: int


class GenerateWeekResponse(BaseModel):
    """``POST /api/v1/schedule/generate-week`` のレスポンス (W4-BE7)。

    出力 schema は W4-BE8 (Layer 2) が直接消費するため、フィールド名 /
    型は **後方互換** を保つこと。フィールド追加は OK、リネームは禁止。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    visits_created: list[VisitCreated]
    pool: list[PoolEntry]
    summary: GenerateWeekSummary


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/fix",
    response_model=ScheduleFixResponse,
    status_code=status.HTTP_200_OK,
    summary="Fix week layout into patients.weekly_pattern (W3-BE-FIX)",
)
async def fix_schedule(
    payload: ScheduleFixRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ScheduleFixResponse:
    """週レイアウトを各患者の ``weekly_pattern`` に保存する.

    全件 1 トランザクション。1 件でも失敗すれば全 rollback。
    """
    service = ScheduleFixService()
    try:
        result = await service.fix_week_layout(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            layout=payload.layout,
        )
        await db.commit()
    except ScheduleFixError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        # 想定外の例外でも必ず rollback する (受入基準 3: トランザクショナル)
        await db.rollback()
        raise

    return ScheduleFixResponse(
        patients_updated=result.patients_updated,
        weekly_pattern_changes=result.weekly_pattern_changes,
    )


@router.post(
    "/generate-week",
    response_model=GenerateWeekResponse,
    status_code=status.HTTP_200_OK,
    summary="Layer 1: expand weekly_pattern → visits + pool (W4-BE7)",
)
async def generate_week(
    payload: GenerateWeekRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> GenerateWeekResponse:
    """Layer 1 アルゴリズムを実行し、当該週の visits を生成する.

    冪等: 同じ (iso_year, iso_week) で 2 回呼ぶと、当該週の自動生成
    visit (source=auto, status=planned) は削除されてから再生成される。
    completed / cancelled / source != auto の visit は保護される。
    """
    expander = Layer1Expander()
    try:
        result = await expander.expand_week(
            db, iso_year=payload.iso_year, iso_week=payload.iso_week
        )
        await db.commit()
    except Layer1ExpandError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    return GenerateWeekResponse(
        iso_year=result.iso_year,
        iso_week=result.iso_week,
        visits_created=result.visits_created,
        pool=result.pool,
        summary=GenerateWeekSummary(
            patients_processed=result.patients_processed,
            visits_created=result.visits_created_count,
            pool_count=result.pool_count,
            special_week_applied_count=result.special_week_applied_count,
        ),
    )


# ---------------------------------------------------------------------------
# /fix-or-pattern Request / Response schemas (W9-BE2)
# ---------------------------------------------------------------------------


class FixOrPatternRequest(BaseModel):
    """``POST /api/v1/schedule/fix-or-pattern`` のリクエストボディ.

    **既存 visit の時刻変更専用** エンドポイント (W9-BE2)。

    新規配置 (visit が DB に未存在 / 保留プールから初配置) は
    ``POST /api/v1/schedule/place-and-fix`` (W15-BE-FIXPATTERN) を使用すること。
    本 endpoint に空文字列 ``""`` を渡しても Pydantic UUID バリデーションで
    422 となるため、FE 側は visit_id が空のときは place-and-fix に切替える。

    mode='this_week_only'  → 当該 visit のみ更新 (固定枠は不変)
    mode='pattern_change'  → patient_fixed_visits を更新 + 当該週 visit も更新
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["this_week_only", "pattern_change"]
    # NOTE: visit_id は既存 Visit.id (UUID) のみ。空文字列は 422 となる。
    visit_id: UUID
    new_weekday: int = Field(ge=0, le=6)
    new_start_time: time
    new_duration_min: int = Field(ge=1, le=480)
    iso_year: int
    iso_week: int


# ---------------------------------------------------------------------------
# Endpoint: /fix-or-pattern (W9-BE2)
# ---------------------------------------------------------------------------


@router.post(
    "/fix-or-pattern",
    status_code=status.HTTP_200_OK,
    summary="訪問時刻変更: 今週のみ / 固定枠変更 (W9-BE2)",
)
async def fix_or_pattern(
    body: FixOrPatternRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> dict:
    """**既存 visit の時刻変更**専用エンドポイント (W9-BE2).

    Wave 15 主フロー (ドロップ即固定枠化) では
    ``POST /api/v1/schedule/place-and-fix`` を使用する。本 endpoint は
    既存 Visit.id を指定した時刻変更にのみ用いる。

    422 恒久対策 (W15-BE-FIXPATTERN):
        ``visit_id`` は Pydantic UUID 型で受ける。空文字列 ``""`` を渡すと
        FastAPI バリデーションが 422 を返す (= 設計通り)。新規配置 (visit が
        未存在) の場合は place-and-fix を呼ぶこと。

    this_week_only: 当該 visit のみ更新。patient_fixed_visits は変更しない。
                    設計書 §3.5.8「既存 /schedule/fix を呼ぶ」に準拠し、
                    ScheduleFixService.update_visit_layout 経由で更新する。
    pattern_change: patient_fixed_visits を更新し、当該週 visit も更新する。
    """
    service = ScheduleFixService()

    updated_visit: VisitV2Read | None = None
    updated_fixed_visit: PatientFixedVisitV2Read | None = None

    try:
        if body.mode == "this_week_only":
            # 設計書 §3.5.8: service 層で visit を一元変更する
            _layout_result: UpdateVisitLayoutResult = await service.update_visit_layout(
                db,
                visit_id=body.visit_id,
                iso_year=body.iso_year,
                iso_week=body.iso_week,
                new_weekday=body.new_weekday,
                new_start_time=body.new_start_time,
                new_duration_min=body.new_duration_min,
            )
            await db.commit()

            # refresh して最新状態を schema に変換
            visit = await db.scalar(select(Visit).where(Visit.id == body.visit_id))
            if visit is not None:
                updated_visit = VisitV2Read.model_validate(visit)

        elif body.mode == "pattern_change":
            # visit を取得 (patient_id 参照のため)
            visit = await db.scalar(
                select(Visit).where(Visit.id == body.visit_id, Visit.deleted_at.is_(None))
            )
            if visit is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

            patient_id: UUID = visit.patient_id

            # patient を取得して mode を決定
            patient = await db.scalar(
                select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
            )
            if patient is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found"
                )

            is_special = _is_special_week_active(patient, body.iso_year, body.iso_week)
            fv_mode: PatientFixedVisitMode = "special" if is_special else "normal"

            # 古い weekday (もし存在するなら削除してから upsert)
            old_weekday = visit.visit_date.weekday()
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == old_weekday,
                )
            )
            # new_weekday の既存行も削除 (upsert)
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == body.new_weekday,
                )
            )

            new_fv = PatientFixedVisit(
                patient_id=patient_id,
                mode=fv_mode,
                weekday=body.new_weekday,
                start_time=body.new_start_time,
                duration_min=body.new_duration_min,
            )
            db.add(new_fv)
            await db.flush()

            # 当該週の visit も service 経由で更新 (§3.5.8)
            await service.update_visit_layout(
                db,
                visit_id=body.visit_id,
                iso_year=body.iso_year,
                iso_week=body.iso_week,
                new_weekday=body.new_weekday,
                new_start_time=body.new_start_time,
                new_duration_min=body.new_duration_min,
            )
            await db.commit()

            await db.refresh(visit)
            await db.refresh(new_fv)
            updated_visit = VisitV2Read.model_validate(visit)
            updated_fixed_visit = PatientFixedVisitV2Read.model_validate(new_fv)

    except ScheduleFixError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    return {
        "mode": body.mode,
        "updated_visit": updated_visit.model_dump(mode="json") if updated_visit else None,
        "updated_fixed_visit": (
            updated_fixed_visit.model_dump(mode="json") if updated_fixed_visit else None
        ),
    }


# ---------------------------------------------------------------------------
# /place-and-fix Request / Response schemas (W15-BE-FIXPATTERN)
# ---------------------------------------------------------------------------


class PlaceAndFixRequest(BaseModel):
    """``POST /api/v1/schedule/place-and-fix`` のリクエストボディ.

    Wave 15 で新設された「ドロップ即固定枠化」フロー専用 (W15-BE-FIXPATTERN)。

    Phase 1 で導入した 422 バグ恒久対策:
        - 保留プールから初配置 (visit が DB に未存在) のとき、FE は本
          endpoint を呼ぶ (visit_id を送らない)
        - 既存 visit の時刻変更は ``/fix-or-pattern`` を継続使用

    挙動:
        1. ``visits`` に新規行を作成 (1 トランザクション内)
           - patient_id, visit_date = ISO 週から計算, status='planned',
             source='manual'
        2. ``fix_pattern=True`` のとき: ``patient_fixed_visits`` を upsert
           - mode は ``_is_special_week_active(patient, iso_year, iso_week)``
             で判定 ('special' / 'normal')
        3. 1 トランザクション (``db.commit()`` 1 回)
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    # W15-codex-fix (1): ドロップ先のコーステンプレート ID (FE 側のセル =
    # course_template × weekday × HH:MM に紐付く). 必須化することで Visit.course_id
    # を確実に埋めて、ScheduleUnifiedView の cellOccupants 計算 (course_id 経由
    # で template にマップ) で「配置直後に画面から消える」主導線破綻を防ぐ。
    course_template_id: UUID = Field(
        description=(
            "ドロップ先の course_templates.id (W15-codex-fix). "
            "BE 側で (template_id, iso_year, iso_week, weekday) に対応する "
            "courses 行を find/create し、Visit.course_id に紐付ける。"
        ),
    )
    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    weekday: int = Field(ge=0, le=6, description="0=Mon..6=Sun")
    start_time: time
    duration_min: int = Field(ge=1, le=480)
    staff_count: Literal[1, 2] = 1
    fix_pattern: bool = Field(
        default=True,
        description=(
            "True: patient_fixed_visits を upsert (恒久) / "
            "False: 今週のみ visit 作成 (固定枠を作らない)"
        ),
    )


class PlaceAndFixResponse(BaseModel):
    """``POST /api/v1/schedule/place-and-fix`` のレスポンス."""

    model_config = ConfigDict(from_attributes=True)

    visit: VisitV2Read
    fixed_visit: PatientFixedVisitV2Read | None = None


# ---------------------------------------------------------------------------
# helper: course (週次インスタンス) の find / create (W15-codex-fix)
# ---------------------------------------------------------------------------


async def _get_or_create_course_for_template_week(
    db,
    *,
    course_template_id: UUID,
    iso_year: int,
    iso_week: int,
    weekday: int,
) -> Course:
    """``course_template_id`` × (iso_year, iso_week, weekday) に対応する Course
    を取得、無ければ作成する (W15-codex-fix).

    place-and-fix のドロップフローでは「FE のセル = course_template × weekday」
    に対し ``visits.course_id`` を紐付ける必要がある。週次インスタンス
    (``courses`` テーブル) は Layer 2 で生成されるが、Wave 15 のドラッグドロップ
    は Layer 2 を経由しないため、本 helper で必要に応じて proposed 状態の
    Course を生成して紐付ける。

    解決順序:
        1. (template_id, iso_year, iso_week, weekday) で SELECT
        2. 無ければ INSERT
            - code        = template.label (1 文字へ正規化; 不一致は 'M')
            - course_status = 'proposed'
            - office_id   = template.office_id
            - template_id = course_template_id

    NOTE: 既存の ``courses`` UNIQUE 制約 (iso_year, iso_week, weekday, code) は
    Wave 15-codex-fix (migration 0021) で office_id を含めた拡張に切り替わる
    予定。本 helper はそれ以前の単純 SELECT で動作する。
    """
    # template を取得 (office_id / label の参照のため). 削除済みは 422 相当だが、
    # place-and-fix は HTTPException で扱うので呼出側の except で捕捉される。
    template = await db.scalar(
        select(CourseTemplate).where(
            CourseTemplate.id == course_template_id,
            CourseTemplate.deleted_at.is_(None),
        )
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CourseTemplate not found",
        )

    # 既存 Course を SELECT (1st try)
    course = await db.scalar(
        select(Course).where(
            Course.template_id == course_template_id,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )
    if course is not None:
        return course

    # 無ければ INSERT を savepoint 内で試みる (race-safe).
    # code は template.label の先頭 1 文字を使い、courses.code CHECK 制約
    # ('A','B','C','D','E','M' — W16 codex fix 中 2 / migration 0023 で 'E' 追加) を
    # 満たさない場合は 'M' (マネージャー枠 = オーバーフロー) に丸める。
    label_first = (template.label or "").strip()[:1].upper()
    code = label_first if label_first in ("A", "B", "C", "D", "E", "M") else "M"

    try:
        async with db.begin_nested():  # savepoint — PostgreSQL/SQLite 両対応
            new_course = Course(
                iso_year=iso_year,
                iso_week=iso_week,
                weekday=weekday,
                code=code,
                course_status=COURSE_STATUS_PROPOSED,
                template_id=course_template_id,
                office_id=template.office_id,
            )
            db.add(new_course)
            await db.flush()
        return new_course
    except IntegrityError:
        # 別トランザクションが同時に INSERT — savepoint のみ rollback して再 SELECT
        course = await db.scalar(
            select(Course).where(
                Course.template_id == course_template_id,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.weekday == weekday,
                Course.deleted_at.is_(None),
            )
        )
        if course is None:
            raise  # 想定外の IntegrityError — 上位へ再送出
        return course


# ---------------------------------------------------------------------------
# Endpoint: /place-and-fix (W15-BE-FIXPATTERN)
# ---------------------------------------------------------------------------


@router.post(
    "/place-and-fix",
    response_model=PlaceAndFixResponse,
    status_code=status.HTTP_200_OK,
    summary="ドロップ即固定枠化 (W15-BE-FIXPATTERN)",
)
async def place_and_fix(
    body: PlaceAndFixRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PlaceAndFixResponse:
    """新規 visit を作成し、必要に応じて patient_fixed_visits を upsert する.

    Wave 15 主フロー: ScheduleChangeDialog (今週のみ / 固定枠変更) を廃止し、
    ドロップ即固定枠化に統一。「今週のみ」運用は ``fix_pattern=False`` で表現。

    挙動:
        1. patient 存在チェック (404 if not found)
        2. ISO (iso_year, iso_week) の月曜 + weekday から visit_date を算出
        3. duration_min から end_time を算出 (24:00 越えは 422)
        4. ``Visit`` 作成 (status='planned', source='manual',
           required_staff_count=staff_count)
        5. ``fix_pattern=True`` のとき:
           - special_week_active 判定で mode を決定
           - 同一 (patient_id, mode, weekday) の既存行を DELETE → INSERT
             (upsert) して固定枠を更新
        6. 1 トランザクションで commit。例外時は rollback。

    Returns:
        ``{"visit": VisitV2Read, "fixed_visit": PatientFixedVisitV2Read | None}``
    """
    # ----- 入力検証 (Pydantic で済まない範囲) -----
    try:
        week_monday = date.fromisocalendar(body.iso_year, body.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={body.iso_year} week={body.iso_week}",
        ) from exc

    visit_date = week_monday + timedelta(days=body.weekday)

    # 終了時刻計算 (24:00 越え禁止)
    end_minutes = body.start_time.hour * 60 + body.start_time.minute + body.duration_min
    if end_minutes >= 24 * 60:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time + duration_min exceeds 24:00",
        )
    end_time = time(end_minutes // 60, end_minutes % 60)

    try:
        # ----- patient 取得 (存在チェック) -----
        patient = await db.scalar(
            select(Patient).where(
                Patient.id == body.patient_id,
                Patient.deleted_at.is_(None),
            )
        )
        if patient is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found",
            )

        # ----- 1a) Course (週次インスタンス) の find/create (W15-codex-fix) -----
        # FE のセル = course_template × weekday に対応する週次 Course を確保し、
        # Visit.course_id に紐付ける。これがないと cellOccupants 計算 (course_id
        # 経由で template にマップ) に visit が乗らず、配置直後にカードが画面から
        # 消える主導線破綻を起こす。
        course = await _get_or_create_course_for_template_week(
            db,
            course_template_id=body.course_template_id,
            iso_year=body.iso_year,
            iso_week=body.iso_week,
            weekday=body.weekday,
        )

        # ----- 1) visits に新規行を作成 -----
        new_visit = Visit(
            patient_id=body.patient_id,
            visit_date=visit_date,
            start_time=body.start_time,
            end_time=end_time,
            type="regular",
            status="planned",
            source="manual",
            required_staff_count=body.staff_count,
            course_id=course.id,
        )
        db.add(new_visit)
        await db.flush()

        # ----- 2) fix_pattern=True のとき patient_fixed_visits を upsert -----
        new_fv: PatientFixedVisit | None = None
        if body.fix_pattern:
            is_special = _is_special_week_active(patient, body.iso_year, body.iso_week)
            fv_mode: PatientFixedVisitMode = "special" if is_special else "normal"

            # 同一 (patient_id, mode, weekday) を DELETE して INSERT (upsert)
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == body.patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == body.weekday,
                )
            )
            new_fv = PatientFixedVisit(
                patient_id=body.patient_id,
                mode=fv_mode,
                weekday=body.weekday,
                start_time=body.start_time,
                duration_min=body.duration_min,
            )
            db.add(new_fv)
            await db.flush()

        # ----- 3) 1 トランザクション commit -----
        await db.commit()

        await db.refresh(new_visit)
        if new_fv is not None:
            await db.refresh(new_fv)

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    visit_read = VisitV2Read.model_validate(new_visit)
    fixed_visit_read = (
        PatientFixedVisitV2Read.model_validate(new_fv) if new_fv is not None else None
    )

    return PlaceAndFixResponse(visit=visit_read, fixed_visit=fixed_visit_read)


# ---------------------------------------------------------------------------
# /generate-and-assign Request / Response schemas (W16-BE3)
# ---------------------------------------------------------------------------


class GenerateAndAssignRequest(BaseModel):
    """``POST /api/v1/schedule/generate-and-assign`` のリクエストボディ (W16-BE3).

    Wave 16: スタッフ別テーブル UI から「週を生成」連動で叩く。
    1 トランザクションで:
      1. 当該週の auto-source visit を全削除
      2. Layer 1 (= /generate-week 相当) を実行し visits を再生成
      3. Layer 3 (= /assign-staff 相当) を実行し courses.assigned_staff_id を確定
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_id: UUID | None = Field(
        default=None,
        description="対象拠点 (None=全拠点合算で実行).",
    )


class GenerateAndAssignResponse(BaseModel):
    """``POST /api/v1/schedule/generate-and-assign`` のレスポンス (W16-BE3)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    visits_created: int
    courses_assigned: int
    message: str


@router.post(
    "/generate-and-assign",
    response_model=GenerateAndAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="W16-BE3: 週生成 + Layer 3 一括実行",
)
async def generate_and_assign(
    payload: GenerateAndAssignRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> GenerateAndAssignResponse:
    """Layer 1 (visit 展開) と Layer 3 (staff 割付) を 1 TX で連続実行する.

    冪等性:
        - 当該週の ``source='auto'`` visit は全て削除されてから再生成される
        - Layer 3 は既存 ``courses.assigned_staff_id`` を上書き
        - エラー時は全 rollback で partial state を残さない

    制約:
        - RBAC: admin / manager only
        - office_id が指定された場合は当該拠点のコースのみ Layer 3 対象
    """
    # ----- ISO 週バリデーション (Layer 1 でも行うが先行 422 のため) -----
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # ----- W16 codex fix (中 1): office_id 指定時は存在確認 (404 if not found) -----
    # 不存在の office_id を渡されても処理が黙って no-op になるのを防ぐため、
    # 早期に 404 を返す。論理削除済み office も存在しない扱い。
    if payload.office_id is not None:
        office = await db.scalar(
            select(Office).where(
                Office.id == payload.office_id,
                Office.deleted_at.is_(None),
            )
        )
        if office is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Office not found: {payload.office_id}",
            )

    expander = Layer1Expander()
    assigner = Layer3Assigner()

    try:
        # ----- 1) Layer 1 (= 既存 auto visit 削除 → 再生成) -----
        # Layer1Expander.expand_week が当該週の auto-visit 削除と再生成の双方を
        # 担当する (W4-BE7).
        # W16 codex fix (重大 2): office_id を Layer 1 にも渡し、対象 patient を
        # primary_office_id 一致でフィルタする。これがないと別拠点の visit が
        # 巻き込まれて削除・再生成され、別拠点のデータが破壊される。
        l1_result = await expander.expand_week(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
        )

        # ----- 2) proposed -> course_fixed への自動昇格 (W16-BE3) -----
        # Layer 3 は course_status='course_fixed' のコースしか対象にしない.
        # 「週を生成」連動フローでは Layer 2 を経由しないため、
        # 当該週 (+ optional office) の proposed コースを course_fixed へ昇格する.
        #
        # 保護: staff_assigned コース (admin が手動割付済み) は昇格対象から明示除外する.
        # promote_where の COURSE_STATUS_PROPOSED 絞り込みにより staff_assigned コースは
        # course_fixed へ戻されず、Layer 3 の SELECT (course_fixed のみ) でも対象外となる.
        # これにより再実行しても admin 手動割付が巻き戻ることはない.
        promote_where = [
            Course.iso_year == payload.iso_year,
            Course.iso_week == payload.iso_week,
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_PROPOSED,  # staff_assigned は触れない
        ]
        if payload.office_id is not None:
            promote_where.append(Course.office_id == payload.office_id)
        proposed_courses = list((await db.scalars(select(Course).where(*promote_where))).all())
        from datetime import datetime as _dt

        now_utc = _dt.now(UTC)
        for c in proposed_courses:
            c.course_status = COURSE_STATUS_COURSE_FIXED
            c.course_fixed_at = now_utc
        if proposed_courses:
            await db.flush()

        # ----- 3) Layer 3 (= staff 割付) — staff_assigned コース保護 -----
        # Layer3Assigner._load_course_targets は course_status='course_fixed' のみ SELECT する.
        # staff_assigned コースは上記 promote_where で昇格対象に含まれず、かつ Layer 3 の
        # SELECT でも除外されるため、再実行で admin 手動割付が上書きされることはない.
        l3_result = await assigner.assign(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
        )

        await db.commit()
    except Layer1ExpandError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Layer3AssignmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    visits_created_count = l1_result.visits_created_count
    courses_assigned = len(l3_result.assignments)

    return GenerateAndAssignResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        visits_created=visits_created_count,
        courses_assigned=courses_assigned,
        message=(
            f"Generated {visits_created_count} visits and assigned "
            f"{courses_assigned} courses for ISO {payload.iso_year}-W{payload.iso_week}"
            + (f" (office {payload.office_id})" if payload.office_id else "")
        ),
    )


# Suppress F401 for LAYER1_VISIT_SOURCE (kept for documentation / future use)
_ = LAYER1_VISIT_SOURCE


__all__ = ["router"]
