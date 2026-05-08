"""Schedule endpoints (W3-BE-FIX / W4-BE7 / W9-BE2 / W15-BE-FIXPATTERN / W16-BE3 / W17-BE-A).

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6 に対応する
HTTP 層。

実装エンドポイント:
    - POST /api/v1/schedule/fix                  (W3-BE-FIX): 週レイアウト確定
    - POST /api/v1/schedule/generate-week        (W4-BE7): Layer 1 アルゴリズム
    - POST /api/v1/schedule/fix-or-pattern       (W9-BE2): **既存 visit の時刻変更**
    - POST /api/v1/schedule/place-and-fix        (W15-BE-FIXPATTERN): ドロップ即固定枠化
    - POST /api/v1/schedule/generate-and-assign  (W16-BE3): 週生成 + Layer 3 一括実行 [Deprecated since W17]
    - POST /api/v1/schedule/generate-week-only   (W17-BE-A1): Layer 1 のみ (staff 未割当)
    - POST /api/v1/schedule/assign-staff-only    (W17-BE-A2): Layer 3 のみ (staff 割付)

## RBAC (API 契約 §6)

- POST /schedule/fix                  — admin / manager のみ (staff は 403)
- POST /schedule/generate-week        — admin / manager のみ (staff は 403)
- POST /schedule/fix-or-pattern       — admin / manager のみ (staff は 403)
- POST /schedule/place-and-fix        — admin / manager のみ (staff は 403)
- POST /schedule/generate-and-assign  — admin / manager のみ (staff は 403)
- POST /schedule/generate-week-only   — admin / manager のみ (staff は 403)
- POST /schedule/assign-staff-only    — admin / manager のみ (staff は 403)

## トランザクション

各サービス層 (``ScheduleFixService`` / ``Layer1Expander``) は
``await db.flush()`` のみを呼び、``commit()`` / ``rollback()`` は呼ばない。
本 HTTP 層が ``try/except`` で 1 リクエストを 1 トランザクションに包み、
例外時に rollback する。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from app.models.staff import Staff, StaffEvent
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
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

logger = logging.getLogger(__name__)

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

            # 削除対象 pfv の course_template_id を先に取得して引継ぎ
            # W37 Phase 1: slot_index=0 のみを対象 (1 visit 配置の既存挙動を維持).
            old_fv = await db.scalar(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == old_weekday,
                    PatientFixedVisit.slot_index == 0,
                )
            )
            preserved_ct_id = old_fv.course_template_id if old_fv is not None else None

            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == old_weekday,
                    PatientFixedVisit.slot_index == 0,
                )
            )
            # new_weekday の既存行も削除 (upsert)
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == body.new_weekday,
                    PatientFixedVisit.slot_index == 0,
                )
            )

            new_fv = PatientFixedVisit(
                patient_id=patient_id,
                mode=fv_mode,
                weekday=body.new_weekday,
                start_time=body.new_start_time,
                duration_min=body.new_duration_min,
                course_template_id=preserved_ct_id,  # W22: 旧 pfv の course_template_id を引継ぎ
                # W37 Phase 1: 1 visit 配置の挙動を維持するため slot_index=0 を明示.
                slot_index=0,
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

    W37 Phase 2-A: 複数スタッフ対応.
        - ``staff_count == 2`` (または ``patient.requires_multiple_staff``)
          のとき、同 patient × 同 visit_date × 同 start_time に
          ``visit_group_id`` を共有する 2 つの visit を作成する.
        - リクエスト形式は ``course_template_ids: list[UUID]`` (推奨) で
          2 つの異なる course_template を受ける. 後方互換として旧形式
          ``course_template_id: UUID`` も受け付ける (= staff_count=1 と同等).

    挙動:
        1. ``visits`` に 1 または 2 行を作成 (1 トランザクション内)
           - patient_id, visit_date = ISO 週から計算, status='planned',
             source='manual'
           - staff_count=2 の場合は ``visit_group_id = uuid4()`` を 2 visit で共有
        2. ``fix_pattern=True`` のとき: ``patient_fixed_visits`` を upsert
           - staff_count=1: slot_index=0 の 1 行
           - staff_count=2: slot_index=0/1 の 2 行 (各 course_template を持つ)
           - mode は ``_is_special_week_active(patient, iso_year, iso_week)``
             で判定 ('special' / 'normal')
        3. 1 トランザクション (``db.commit()`` 1 回). 失敗時は全 rollback.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    # W15-codex-fix (1) → W37 Phase 2-A 拡張:
    #   - 後方互換: 旧形式 ``course_template_id`` (単一 UUID) を引き続き受ける.
    #     これは staff_count=1 と同等扱い (slot_index=0 のみ).
    #   - 推奨: ``course_template_ids`` (list[UUID]; 1 or 2 件).
    #     staff_count に整合する件数を渡す。staff_count=2 なら 2 件必須で
    #     かつ 2 件が異なる UUID であること (同一は 400).
    # 2 つを同時送信した場合は 400.
    course_template_id: UUID | None = Field(
        default=None,
        description=(
            "ドロップ先の course_templates.id (旧形式; W15-codex-fix). "
            "後方互換のため staff_count=1 のみ。staff_count=2 のときは "
            "course_template_ids を使うこと。"
        ),
    )
    course_template_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "ドロップ先の course_templates.id 配列 (W37 Phase 2-A; 推奨). "
            "1 件 = 1 名体制 (slot_index=0 のみ), 2 件 = 2 名体制 "
            "(slot_index=0/1 の 2 visit を visit_group_id 共有で作成). "
            "件数は staff_count と一致しなければならない."
        ),
        min_length=1,
        max_length=2,
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

    @model_validator(mode="after")
    def _validate_course_templates_shape(self) -> PlaceAndFixRequest:
        """``course_template_id`` / ``course_template_ids`` の形状を検証する.

        ルール (W37 Phase 2-A):
          - 両方同時指定は 400 相当 (= 422; pydantic ValueError).
          - どちらも未指定は 400 相当.
          - ``course_template_ids`` の件数は staff_count と一致.
          - staff_count=2 のとき 2 件が異なる UUID であること (同一は 400).
        """
        has_single = self.course_template_id is not None
        has_list = self.course_template_ids is not None

        if has_single and has_list:
            raise ValueError("course_template_id と course_template_ids は同時に指定できません")
        if not has_single and not has_list:
            raise ValueError(
                "course_template_id または course_template_ids のいずれかを指定してください"
            )

        # 旧形式 (course_template_id) は staff_count=1 のみ許容.
        if has_single and self.staff_count != 1:
            raise ValueError(
                "course_template_id (旧形式) は staff_count=1 のみ. "
                "staff_count=2 のときは course_template_ids を使ってください"
            )

        # 新形式: 件数 == staff_count を要求.
        if has_list:
            assert self.course_template_ids is not None  # for type-narrowing
            if len(self.course_template_ids) != self.staff_count:
                raise ValueError(
                    f"course_template_ids の件数 ({len(self.course_template_ids)}) "
                    f"が staff_count ({self.staff_count}) と一致しません"
                )
            if self.staff_count == 2 and len(set(self.course_template_ids)) != 2:
                raise ValueError(
                    "staff_count=2 では course_template_ids は 2 つの異なる UUID "
                    "である必要があります (同一コース 2 visit は不可)"
                )

        return self

    def resolved_template_ids(self) -> list[UUID]:
        """旧形式 / 新形式を吸収して course_template_id 配列を返す.

        Returns:
            staff_count == 1 → 1 要素の list, staff_count == 2 → 2 要素の list.
        """
        if self.course_template_ids is not None:
            return list(self.course_template_ids)
        assert self.course_template_id is not None  # validator で保証
        return [self.course_template_id]


class PlaceAndFixResponse(BaseModel):
    """``POST /api/v1/schedule/place-and-fix`` のレスポンス.

    W37 Phase 2-A:
        staff_count=2 のときは ``visits`` / ``fixed_visits`` がそれぞれ 2 件返る.
        後方互換のため、staff_count=1 (旧挙動) では従来どおり ``visit`` /
        ``fixed_visit`` 単数フィールドにも 1 件目を入れる.
    """

    model_config = ConfigDict(from_attributes=True)

    # 後方互換 (staff_count=1 の従来クライアント向け; 1 visit / 1 fixed_visit を入れる).
    # staff_count=2 のときも 1 件目を入れる. None には絶対にならない (visit は必ず作る).
    visit: VisitV2Read
    fixed_visit: PatientFixedVisitV2Read | None = None
    # W37 Phase 2-A: 複数スタッフ対応で配列形式も追加 (Phase 3 FE で使用).
    visits: list[VisitV2Read] = Field(default_factory=list)
    fixed_visits: list[PatientFixedVisitV2Read] = Field(default_factory=list)
    # 2 名体制で共有される visit_group_id (staff_count=1 のときは None).
    visit_group_id: UUID | None = None


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

    解決順序 (W18 Phase 0-b でフォールバック SELECT を追加):
        1. (template_id, iso_year, iso_week, weekday) で SELECT (1st try)
        2. miss なら (office_id, code, iso_year, iso_week, weekday) で SELECT
           — UNIQUE 制約 ``uq_courses_year_week_weekday_code_office`` と同じ
             find key にすることで、INSERT 前に確実に既存行を検出する。
             template_id が NULL / 別 template に紐付いている既存行がある
             ケース (W16 デプロイ時の helper bug 残骸) も拾える。
        3. 既存行が見つかった場合は ``template_id`` を補正して return
           (race-safe: 別 TX で先行 INSERT された行を引き継ぐ)。
        4. 無ければ INSERT を savepoint 内で試みる (race-safe).
            - code        = template.label (1 文字へ正規化; 不一致は 'M')
            - course_status = 'proposed'
            - office_id   = template.office_id
            - template_id = course_template_id
        5. INSERT が IntegrityError になったら、もう一度 (office_id, code,
           year, week, weekday) で SELECT して引き継ぐ (UNIQUE 衝突回復).

    UNIQUE 制約は ``(iso_year, iso_week, weekday, code, office_id)`` の
    5-tuple なので、step 1 の (template_id, ...) だけでは衝突を事前検出できない
    (例: ``code`` が同じだが ``template_id`` が NULL/別の course が既に
    入っているケース)。step 2 / 5 はその穴埋め。
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

    # code は template.label の先頭 1 文字を使い、courses.code CHECK 制約
    # ('A','B','C','D','E','M' — W16 codex fix 中 2 / migration 0023 で 'E' 追加) を
    # 満たさない場合は 'M' (マネージャー枠 = オーバーフロー) に丸める。
    label_first = (template.label or "").strip()[:1].upper()
    code = label_first if label_first in ("A", "B", "C", "D", "E", "M") else "M"

    # 1st try: (template_id, iso_year, iso_week, weekday) で SELECT.
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

    # 2nd try (W18 Phase 0-b): UNIQUE 制約と同じ key で SELECT.
    # template_id が NULL / 別 template の course が既にある場合を拾う。
    course = await db.scalar(
        select(Course).where(
            Course.office_id == template.office_id,
            Course.code == code,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )
    if course is not None:
        # template_id が未設定 / 別の template を指していた場合は補正する.
        # これにより 1st SELECT 経路でも次回以降ヒットするようになり、
        # 不整合が自動で解消されていく (但し本番の根治は cleanup script
        # ``scripts/cleanup_w16_course_code_mismatch.py`` で行う).
        if course.template_id != course_template_id:
            # W18 Codex-fix 中-5: 自動補正は不整合行の存在を示すサイン。
            # 本番でも追える形で warn ログに残す。cleanup script で抜本対応する目印。
            logger.warning(
                "course.template_id auto-correction: course_id=%s old_template=%s "
                "new_template=%s (via 2nd SELECT: office=%s code=%s year=%s "
                "week=%s weekday=%s)",
                course.id,
                course.template_id,
                course_template_id,
                template.office_id,
                code,
                iso_year,
                iso_week,
                weekday,
            )
            course.template_id = course_template_id
            await db.flush()
        return course

    # 無ければ INSERT を savepoint 内で試みる (race-safe).
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
        # 別トランザクションが同時に INSERT、または既存の不整合 row との UNIQUE
        # 衝突 (W18 Phase 0). savepoint のみ rollback して UNIQUE key で再 SELECT。
        # 1st SELECT (template_id) では拾えない不整合行も、UNIQUE key (office_id,
        # code, year, week, weekday) で必ず見つかる。
        course = await db.scalar(
            select(Course).where(
                Course.office_id == template.office_id,
                Course.code == code,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.weekday == weekday,
                Course.deleted_at.is_(None),
            )
        )
        if course is None:
            raise  # 想定外の IntegrityError — 上位へ再送出
        # 拾えた既存行の template_id が未設定 / 別の template を指していた場合は補正.
        if course.template_id != course_template_id:
            # W18 Codex-fix 中-5: INSERT 衝突 → 既存行引き継ぎ時の補正。
            # 同時 INSERT race か旧 helper bug の残骸。warn で運用観測可能化。
            logger.warning(
                "course.template_id auto-correction (post-IntegrityError): "
                "course_id=%s old_template=%s new_template=%s "
                "(via UNIQUE-key SELECT: office=%s code=%s year=%s week=%s weekday=%s)",
                course.id,
                course.template_id,
                course_template_id,
                template.office_id,
                code,
                iso_year,
                iso_week,
                weekday,
            )
            course.template_id = course_template_id
            await db.flush()
        return course


# ---------------------------------------------------------------------------
# Endpoint: /place-and-fix (W15-BE-FIXPATTERN)
# ---------------------------------------------------------------------------


@router.post(
    "/place-and-fix",
    response_model=PlaceAndFixResponse,
    status_code=status.HTTP_200_OK,
    summary="ドロップ即固定枠化 (W15-BE-FIXPATTERN / W37 Phase 2-A multi-staff)",
)
async def place_and_fix(
    body: PlaceAndFixRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PlaceAndFixResponse:
    """新規 visit を作成し、必要に応じて patient_fixed_visits を upsert する.

    Wave 15 主フロー: ScheduleChangeDialog (今週のみ / 固定枠変更) を廃止し、
    ドロップ即固定枠化に統一。「今週のみ」運用は ``fix_pattern=False`` で表現。

    W37 Phase 2-A: 複数スタッフ対応 (2 名体制).
        - staff_count=2 (または patient.requires_multiple_staff=True) の場合
          2 visit + 2 fixed_visit を visit_group_id 共有で作成する.
        - course_template_ids[0] / [1] が異なる course_template_id を持つ.
        - 2 つの visit は (patient_id, visit_date, start_time) が同じだが、
          異なる course_id (= 異なる course_template から派生) と
          共通 visit_group_id を持つ. partial UNIQUE
          ``uq_visits_pds_group_active`` (visit_group_id 込み) により共存可能.

    挙動:
        1. patient 存在チェック (404 if not found)
        2. ISO (iso_year, iso_week) の月曜 + weekday から visit_date を算出
        3. duration_min から end_time を算出 (24:00 越えは 422)
        4. ``Visit`` を staff_count 件作成 (status='planned', source='manual',
           required_staff_count=staff_count, visit_group_id 共有)
        5. ``fix_pattern=True`` のとき:
           - special_week_active 判定で mode を決定
           - 同一 (patient_id, mode, weekday, slot_index) の既存行を DELETE
             → INSERT (upsert) して固定枠を更新
           - staff_count=2 では slot_index=0 / 1 を 2 行 INSERT
        6. 1 トランザクションで commit。例外時は全 rollback (片方だけ残らない)。

    Returns:
        後方互換: ``visit`` / ``fixed_visit`` (1 件目を入れる) と、
        新形式: ``visits`` / ``fixed_visits`` (配列) と ``visit_group_id``.
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

    template_ids = body.resolved_template_ids()

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

        # ----- W37 Phase 2-A: requires_multiple_staff フラグとの整合チェック (案 X) -----
        # 「requires_multiple_staff=True フラグ ON → 2 コース必須」(BE 側で守る).
        # FE はフラグ ON 患者には staff_count=2 を送る前提だが、誤って 1 で来た
        # ときに BE が止めることでデータ不整合 (1 コース固定枠化で翌週 1 名展開)
        # を防ぐ.
        if patient.requires_multiple_staff and body.staff_count != 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "patient.requires_multiple_staff=true の患者は staff_count=2 "
                    "で配置する必要があります"
                ),
            )

        # ----- W18 Codex-fix 中-4: クロス-office チェック (全 template に対して) -----
        # patient.primary_office_id と各 course_template.office_id が全て一致する
        # 必要がある. 1 つでも違えば 422.
        # primary_office_id が NULL の患者は対象外 (任意設定運用).
        templates_by_id: dict[UUID, CourseTemplate] = {}
        for tpl_id in template_ids:
            tpl = await db.scalar(
                select(CourseTemplate).where(
                    CourseTemplate.id == tpl_id,
                    CourseTemplate.deleted_at.is_(None),
                )
            )
            if tpl is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"CourseTemplate not found: {tpl_id}",
                )
            templates_by_id[tpl_id] = tpl

        if patient.primary_office_id is not None:
            for tpl_id, tpl in templates_by_id.items():
                if tpl.office_id != patient.primary_office_id:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            "patient.primary_office_id と course_template.office_id "
                            f"が一致しません (template={tpl_id}, "
                            "cross-office drop is not allowed)"
                        ),
                    )

        # ----- W37 Phase 2-A: staff_count=2 のとき既存 slot_index=1 行と衝突しないか先行チェック -----
        # 既存に slot_index=1 がある状態で「新たに staff_count=2 で別 weekday/time に
        # 配置」しようとした場合、本 endpoint は当該 (mode, weekday) の slot 0/1 を
        # DELETE→INSERT で upsert するので問題ない (上書き). ただし
        # 念のためテストで動作を担保する目的の早期検出用に明示の検出はここでは
        # 行わない (upsert で吸収). 過不足整合性は単体テストで担保する.

        # ----- 1a) Course (週次インスタンス) の find/create (W15-codex-fix) -----
        # FE のセル = course_template × weekday に対応する週次 Course を確保し、
        # Visit.course_id に紐付ける. staff_count=2 では 2 つの異なる template から
        # 2 つの異なる Course を確保する.
        courses: list[Course] = []
        for tpl_id in template_ids:
            c = await _get_or_create_course_for_template_week(
                db,
                course_template_id=tpl_id,
                iso_year=body.iso_year,
                iso_week=body.iso_week,
                weekday=body.weekday,
            )
            courses.append(c)

        # ----- 1) visits に新規行を作成 (staff_count 件; 共通 visit_group_id) -----
        # W37 Phase 2-A: 2 名体制では visit_group_id を共有することで partial UNIQUE
        # uq_visits_pds_group_active (visit_group_id 込み) を通過させる.
        # 1 名体制 (旧挙動) では visit_group_id=None.
        shared_visit_group_id: UUID | None = uuid.uuid4() if body.staff_count == 2 else None
        new_visits: list[Visit] = []
        for course in courses:
            v = Visit(
                patient_id=body.patient_id,
                visit_date=visit_date,
                start_time=body.start_time,
                end_time=end_time,
                type="regular",
                status="planned",
                source="manual",
                required_staff_count=body.staff_count,
                course_id=course.id,
                visit_group_id=shared_visit_group_id,
            )
            db.add(v)
            new_visits.append(v)
        await db.flush()

        # ----- 2) fix_pattern=True のとき patient_fixed_visits を upsert -----
        new_fvs: list[PatientFixedVisit] = []
        if body.fix_pattern:
            is_special = _is_special_week_active(patient, body.iso_year, body.iso_week)
            fv_mode: PatientFixedVisitMode = "special" if is_special else "normal"

            # 同一 (patient_id, mode, weekday) の既存 slot 0/1 を全て DELETE して
            # INSERT (upsert). staff_count=1 のときは slot_index=0 のみ. ただし
            # 「以前 staff_count=2 で配置 → 今回 staff_count=1 で再配置」のケースを
            # 考慮し、staff_count=1 でも slot_index=1 行が残っていると整合性が崩れる
            # (= 1 名体制患者なのに固定枠 2 件)。本 endpoint では「現在の staff_count
            # に対応する slot_index 範囲」のみ DELETE し、不要 slot は触らない方針:
            #   - staff_count=1 → slot_index=0 のみ DELETE (Phase 1 互換挙動を維持).
            #   - staff_count=2 → slot_index=0 と 1 を DELETE.
            slot_targets = list(range(body.staff_count))  # [0] or [0, 1]
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == body.patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == body.weekday,
                    PatientFixedVisit.slot_index.in_(slot_targets),
                )
            )
            for slot_idx, tpl_id in enumerate(template_ids):
                fv = PatientFixedVisit(
                    patient_id=body.patient_id,
                    mode=fv_mode,
                    weekday=body.weekday,
                    start_time=body.start_time,
                    duration_min=body.duration_min,
                    # W22 Phase A: course_template_id を保存し、翌週以降の Layer 1
                    # でこのテンプレートが優先される (コース継承).
                    course_template_id=tpl_id,
                    # W37 Phase 2-A: slot_index は 0 から順番に割り当てる.
                    slot_index=slot_idx,
                )
                db.add(fv)
                new_fvs.append(fv)
            await db.flush()

        # ----- 3) 1 トランザクション commit -----
        await db.commit()

        for v in new_visits:
            await db.refresh(v)
        for fv in new_fvs:
            await db.refresh(fv)

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    visits_read = [VisitV2Read.model_validate(v) for v in new_visits]
    fixed_visits_read = [PatientFixedVisitV2Read.model_validate(fv) for fv in new_fvs]

    return PlaceAndFixResponse(
        # 後方互換: 1 件目をスカラフィールドにも入れる.
        visit=visits_read[0],
        fixed_visit=fixed_visits_read[0] if fixed_visits_read else None,
        # 新形式: 配列.
        visits=visits_read,
        fixed_visits=fixed_visits_read,
        visit_group_id=shared_visit_group_id,
    )


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

    Deprecated since Wave 17: Use ``POST /generate-week-only`` + ``POST /assign-staff-only``
    instead for staged operation (confirm visits before assigning staff).
    This endpoint is preserved for backward compatibility.

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

        now_utc = datetime.now(UTC)
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


# ---------------------------------------------------------------------------
# /generate-week-only Request / Response schemas (W17-BE-A1)
# ---------------------------------------------------------------------------


class GenerateWeekOnlyRequest(BaseModel):
    """``POST /api/v1/schedule/generate-week-only`` のリクエストボディ (W17-BE-A1).

    Wave 17: 管理者が「週を生成」ボタンで Layer 1 のみ実行する。
    visits を展開してスタッフは未割当のままテーブルに並べる。
    内容を確認してから ``POST /assign-staff-only`` で staff 割付。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_id: UUID | None = Field(
        default=None,
        description="対象拠点 (None=全拠点合算で実行).",
    )


class GenerateWeekOnlyResponse(BaseModel):
    """``POST /api/v1/schedule/generate-week-only`` のレスポンス (W17-BE-A1)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    visits_created: int
    courses_touched: int
    message: str


# ---------------------------------------------------------------------------
# /assign-staff-only Request / Response schemas (W17-BE-A2)
# ---------------------------------------------------------------------------


class AssignStaffOnlyRequest(BaseModel):
    """``POST /api/v1/schedule/assign-staff-only`` のリクエストボディ (W17-BE-A2).

    Wave 17: 管理者が「自動割付」ボタンで Layer 3 のみ実行する。
    既存 visits を保持しつつ courses に staff を曜日別ローテで割付する。
    ``course_status='proposed'`` のコースを ``'course_fixed'`` へ昇格してから
    Layer 3 を実行する。``'staff_assigned'`` コースは保護される。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_id: UUID | None = Field(
        default=None,
        description="対象拠点 (None=全拠点合算で実行).",
    )


class AssignStaffWarning(BaseModel):
    """W27 Phase A: assign-staff-only で検出された event×visit 重複の警告.

    Layer 3 のハード除外でほぼ抑止されるが、固定割当 (manager / 都賀) や
    既に staff_assigned 状態だったコース (再実行時保護) が event と重なる
    ケースは残るため、API レスポンスで管理者に通知する。
    """

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    staff_name: str | None = None
    event_id: UUID
    event_type: str
    visit_id: UUID
    visit_start_time: time
    message: str


class AssignStaffOnlyResponse(BaseModel):
    """``POST /api/v1/schedule/assign-staff-only`` のレスポンス (W17-BE-A2)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    courses_assigned: int
    message: str
    # W27 Phase A: event 時間帯と既存 visit (assigned_staff) の重複警告
    warnings: list[AssignStaffWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoint: /generate-week-only (W17-BE-A1)
# ---------------------------------------------------------------------------


@router.post(
    "/generate-week-only",
    response_model=GenerateWeekOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="W17-BE-A1: Layer 1 のみ — visit 展開 (staff 未割当)",
)
async def generate_week_only(
    payload: GenerateWeekOnlyRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> GenerateWeekOnlyResponse:
    """Layer 1 のみ実行し visits を展開する。staff 割付は行わない (W17-BE-A1).

    Wave 17 の 3 段階運用:
        1. ``POST /generate-week-only``  — 本 endpoint (Layer 1 のみ)
        2. 管理者が内容を確認
        3. ``POST /assign-staff-only``   — Layer 3 のみ

    冪等性:
        - 当該週の ``source='auto'`` visit は全て削除されてから再生成される
        - ``courses.assigned_staff_id`` は変更しない
        - エラー時は全 rollback

    制約:
        - RBAC: admin / manager only
        - office_id が指定された場合は当該拠点の患者のみ Layer 1 対象
        - office_id 不存在時は 404
    """
    # ----- ISO 週バリデーション (Layer 1 でも行うが先行 422 のため) -----
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # ----- office_id 指定時は存在確認 (404 if not found) -----
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

    try:
        # ----- Layer 1 のみ (visit 展開; staff 割付は行わない) -----
        l1_result = await expander.expand_week(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
        )

        await db.commit()
    except Layer1ExpandError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    visits_created_count = l1_result.visits_created_count
    # courses_touched: Layer 1 が展開した visits から一意な course_id の数を算出する。
    courses_touched = len(set(v.course_id for v in l1_result.visits_created if v.course_id))

    return GenerateWeekOnlyResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        visits_created=visits_created_count,
        courses_touched=courses_touched,
        message=(
            f"Generated {visits_created_count} visits (staff not yet assigned) "
            f"for ISO {payload.iso_year}-W{payload.iso_week}"
            + (f" (office {payload.office_id})" if payload.office_id else "")
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint: /assign-staff-only (W17-BE-A2)
# ---------------------------------------------------------------------------


@router.post(
    "/assign-staff-only",
    response_model=AssignStaffOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="W17-BE-A2: Layer 3 のみ — 既存 visits 保持で staff 割付",
)
async def assign_staff_only(
    payload: AssignStaffOnlyRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> AssignStaffOnlyResponse:
    """Layer 3 のみ実行し既存 visits を保持したまま courses に staff を割付する (W17-BE-A2).

    Wave 17 の 3 段階運用:
        1. ``POST /generate-week-only``  — Layer 1 のみ
        2. 管理者が内容を確認
        3. ``POST /assign-staff-only``   — 本 endpoint (Layer 3 のみ)

    冪等性:
        - ``course_status='proposed'`` のコースを ``'course_fixed'`` へ昇格する
        - ``'staff_assigned'`` コースは昇格対象から除外され Layer 3 でも保護される
        - 再実行で admin 手動割付が巻き戻ることはない

    制約:
        - RBAC: admin / manager only
        - office_id が指定された場合は当該拠点のコースのみ Layer 3 対象
        - office_id 不存在時は 404
    """
    # ----- ISO 週バリデーション -----
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # ----- office_id 指定時は存在確認 (404 if not found) -----
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

    assigner = Layer3Assigner()

    try:
        # ----- proposed -> course_fixed への昇格 (staff_assigned コース保護) -----
        promote_where = [
            Course.iso_year == payload.iso_year,
            Course.iso_week == payload.iso_week,
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_PROPOSED,
        ]
        if payload.office_id is not None:
            promote_where.append(Course.office_id == payload.office_id)
        proposed_courses = list((await db.scalars(select(Course).where(*promote_where))).all())

        now_utc = datetime.now(UTC)
        for c in proposed_courses:
            c.course_status = COURSE_STATUS_COURSE_FIXED
            c.course_fixed_at = now_utc
        if proposed_courses:
            await db.flush()

        # ----- Layer 3 のみ (staff 割付) -----
        l3_result = await assigner.assign(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
        )

        # ----- W27 Phase A: event × visit 重複の warnings 収集 -----
        warnings = await _collect_event_visit_warnings(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            office_id=payload.office_id,
        )

        await db.commit()
    except Layer3AssignmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    courses_assigned = len(l3_result.assignments)

    return AssignStaffOnlyResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        courses_assigned=courses_assigned,
        message=(
            f"Assigned staff to {courses_assigned} courses "
            f"for ISO {payload.iso_year}-W{payload.iso_week}"
            + (f" (office {payload.office_id})" if payload.office_id else "")
        ),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# W27 Phase A: event × visit 重複検出 (warnings 用)
# ---------------------------------------------------------------------------


def _strip_tz_naive(dt: datetime) -> datetime:
    """tz-aware/naive どちらでも naive (壁時計) に揃える (PostgreSQL/SQLite 両対応)."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def _collect_event_visit_warnings(
    db,
    *,
    iso_year: int,
    iso_week: int,
    office_id: UUID | None,
) -> list[AssignStaffWarning]:
    """assign-staff-only 完了後、event 時間帯と visit 時間帯の重複を検出する.

    Layer 3 のハード除外で大部分は事前抑止されるが、以下のケースで重複が
    残り得るため、管理者向けに warnings を返す:
        - 固定割当 (manager / 都賀 staff) が event と重なるケース
        - 既に ``course_status='staff_assigned'`` だったコースの保護スタッフ
          (再実行で上書きされず、event があっても剥がされない)
    """
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError:
        return []
    week_sunday_plus1 = week_monday + timedelta(days=7)

    # 当該週の visits を course 経由で staff 紐付けして取得.
    # Visit は iso_year/iso_week カラムを持たないため、visit_date で範囲指定.
    visit_stmt = (
        select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status == VISIT_STATUS_PLANNED,
            Visit.visit_date >= week_monday,
            Visit.visit_date < week_sunday_plus1,
            Course.assigned_staff_id.isnot(None),
        )
    )
    if office_id is not None:
        visit_stmt = visit_stmt.where(Course.office_id == office_id)
    rows = (await db.execute(visit_stmt)).all()
    if not rows:
        return []

    # 関係 staff_id 集合
    staff_ids = {course.assigned_staff_id for _, course, _ in rows if course.assigned_staff_id}
    if not staff_ids:
        return []

    # 当該週の StaffEvent を一括取得
    range_start = datetime.combine(week_monday, time(0, 0))
    range_end = datetime.combine(week_sunday_plus1, time(0, 0))
    event_stmt = select(StaffEvent).where(
        StaffEvent.staff_id.in_(list(staff_ids)),
        StaffEvent.starts_at < range_end,
        StaffEvent.ends_at >= range_start,
    )
    events_rows = list((await db.scalars(event_stmt)).all())
    if not events_rows:
        return []
    events_by_staff: dict[UUID, list[StaffEvent]] = {}
    for ev in events_rows:
        events_by_staff.setdefault(ev.staff_id, []).append(ev)

    warnings: list[AssignStaffWarning] = []
    for visit, course, staff in rows:
        sid = course.assigned_staff_id
        if sid is None:
            continue
        events = events_by_staff.get(sid)
        if not events:
            continue
        visit_start_dt = datetime.combine(visit.visit_date, visit.start_time)
        visit_end_dt = datetime.combine(visit.visit_date, visit.end_time)
        staff_name = staff.name if staff is not None else None
        for ev in events:
            ev_start = _strip_tz_naive(ev.starts_at)
            ev_end = _strip_tz_naive(ev.ends_at)
            if ev_start < visit_end_dt and ev_end > visit_start_dt:
                warnings.append(
                    AssignStaffWarning(
                        staff_id=sid,
                        staff_name=staff_name,
                        event_id=ev.id,
                        event_type=ev.event_type,
                        visit_id=visit.id,
                        visit_start_time=visit.start_time,
                        message=(
                            f"{staff_name or sid} は "
                            f"{ev_start.isoformat()}〜{ev_end.isoformat()} に "
                            f"{ev.event_type} 予定。"
                            f"{visit.visit_date.isoformat()} {visit.start_time.isoformat()} "
                            f"の visit と重複"
                        ),
                    )
                )
    return warnings


# Suppress F401 for LAYER1_VISIT_SOURCE (kept for documentation / future use)
_ = LAYER1_VISIT_SOURCE


__all__ = ["router"]
