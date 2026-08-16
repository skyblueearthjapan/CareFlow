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

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_PROPOSED,
    COURSE_STATUS_STAFF_ASSIGNED,
    Course,
)
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffEvent
from app.models.user import User
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit
from app.schemas.v2.auto_allocate import (
    AutoAllocateRequest,
    AutoAllocateResponse,
    ProposalApplyResponse,
    ProposalDiscardResponse,
)
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitMode, PatientFixedVisitV2Read
from app.schemas.v2.visit import VisitV2Read
from app.services.accompaniment import (
    AccompanimentDutyWarning,
    collect_accompaniment_duty_warnings,
    expand_accompaniment_defaults,
    notify_accompaniment_duty_conflict,
)
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings,
    collect_constraint_warnings_for_patients,
    constraint_confirmation_detail,
    notify_constraint_override,
    notify_constraint_override_for_course,
)
from app.services.op_log_service import fmt_time, fmt_weekday, record_op
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
    UnplacedMultiStaffEntry,
    _is_special_week_active,
)
from app.services.scheduling.layer3_assignment import (
    PATIENT_RECENT_DEPTH,
    AutoCommittedNotice,
    Layer3Assigner,
    Layer3AssignmentError,
    Layer3Result,
    ReviewItem,
    RotationConflict,
    SecondaryConstraintWarning,
    StaffAssignment,
    UnresolvedGenderWarning,
    UnresolvedNgStaffWarning,
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
    # W37 hotfix C-2: multi-staff 患者で slot 0/1 のうち片方のみ設定された
    # 行は visit を生成せず保留扱いにする (silent 1-staff 化を廃止). この
    # リストは管理者向けに「どの患者・曜日が片側不備で生成されなかったか」を
    # 通知するために返す.
    unplaced_multi_staff_patients: list[UnplacedMultiStaffEntry] = []


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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
        # W37 hotfix C-2: multi-staff 片側欠けの保留リスト
        unplaced_multi_staff_patients=result.unplaced_multi_staff_patients,
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
            # Phase E-5 (項目 ⑥B): sub_office_id も引継ぎ.
            preserved_sub_office_id = old_fv.sub_office_id if old_fv is not None else None

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
                # Phase E-5 (項目 ⑥B): 旧 PFV の sub_office_id を引継ぎ.
                sub_office_id=preserved_sub_office_id,
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
    # 定員超過の管理者相談プロセス (方式b): 定員超過を承知で配置したときの理由 (監査用).
    # 値があれば logger.info で記録するだけで、検証や挙動変更は一切しない. 後方互換の任意項目.
    capacity_override_reason: str | None = Field(default=None, max_length=500)
    # Wave U-3 操作ジャーナル: FE が 1 ドラッグ = 1グループで同値を送る。省略時は単発グループ。
    op_group_id: UUID | None = Field(
        default=None,
        description="操作グループ ID（FE が DnD の delete+place に同値を送る）。省略で自動発行。",
    )
    # NG スタッフ / 性別制限の確認フロー (docs/plans/patient-ng-staff-design.md §7-2)。
    # 既定 False = 従来どおり。違反があれば 422 (code=constraint_confirmation_required)。
    acknowledge_constraint_warnings: bool = Field(
        default=False,
        description="NGスタッフ/性別制限の警告を確認済みとして続行する (422 の再送用)。",
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
    current_user: Annotated[User, Depends(require_role("admin"))],
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

    NG スタッフ / 性別制限 (§7-2): Course 確保の直後・visit 作成の **前** に
    「配置する患者 × 配置先コースの現担当」(2 名体制なら 2 コース分) を検査し、
    違反があれば ``code=constraint_confirmation_required`` の 422。
    ``acknowledge_constraint_warnings=true`` の再送で続行し、管理者へお知らせを飛ばす。

    Returns:
        後方互換: ``visit`` / ``fixed_visit`` (1 件目を入れる) と、
        新形式: ``visits`` / ``fixed_visits`` (配列) と ``visit_group_id``.
    """
    # 定員超過の管理者相談プロセス (方式b): 配置時の理由記録 (監査はミドルウェア任せ).
    # 値があれば info ログに残すだけで、検証や挙動変更はしない.
    if body.capacity_override_reason:
        logger.info(
            "capacity_override on place-and-fix: patient_id=%s reason=%s",
            body.patient_id,
            body.capacity_override_reason,
        )

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

        # W-6: 主担当拠点が未設定 (NULL) の患者は週次 visit 生成から除外されるため、
        # place-and-fix で固定配置を確定させても毎週現れない「ねじれ」になる.
        # NULL のときは cross-office チェックをスキップせず、明確に 422 でブロックする.
        if patient.primary_office_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "主担当拠点が未設定のため配置できません。"
                    "患者マスタで主担当拠点を設定してください。"
                ),
            )
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

        # ----- 1b) NG スタッフ / 性別制限の確認フロー (§7-2) -----
        # 「患者を動かす」方向の検査: 配置する患者 × 配置先コースの現担当。
        # 2 名体制では 2 コース (= 2 担当) 全てを対象にする。visit を作る **前** に
        # 置き、422 なら何も書かずに rollback して返す (行だけ作って弾く事故を防ぐ)。
        _cw: list[ConstraintWarning] = []
        _cw_by_course: list[tuple[Course, list[ConstraintWarning]]] = []
        for course in courses:
            _w = await collect_constraint_warnings_for_patients(
                db, course=course, patient_ids=[body.patient_id]
            )
            if _w:
                _cw.extend(_w)
                _cw_by_course.append((course, _w))
        if _cw and not body.acknowledge_constraint_warnings:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=constraint_confirmation_detail(_cw),
            )

        # ----- 1) visits に新規行を作成 (staff_count 件; 共通 visit_group_id) -----
        # W37 Phase 2-A: 2 名体制では visit_group_id を共有することで partial UNIQUE
        # uq_visits_pds_group_active (visit_group_id 込み) を通過させる.
        # 1 名体制 (旧挙動) では visit_group_id=None.
        shared_visit_group_id: UUID | None = uuid.uuid4() if body.staff_count == 2 else None
        # Wave U-1 (§2.2 反映先の統一 B 経路 = プール採用「この週だけ配置」):
        #   - fix_pattern=True (型も更新)  → source='manual' (従来どおり).
        #   - fix_pattern=False (今週だけ) → source='manual_week'. 週生成・固定枠戻の
        #     両方で保護される値 (U-0 で保証) なので、来週は元の固定枠に戻る一方、
        #     今週は消えない. FE は現状 fix_pattern=true 固定送信のため既存挙動は不変.
        visit_source = "manual" if body.fix_pattern else VISIT_SOURCE_MANUAL_WEEK
        new_visits: list[Visit] = []
        for course in courses:
            v = Visit(
                patient_id=body.patient_id,
                visit_date=visit_date,
                start_time=body.start_time,
                end_time=end_time,
                type="regular",
                status="planned",
                source=visit_source,
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
            # PO 決定 (2026-08-09): 完全固定 (旧 pinned) の 422 ブロックは撤廃。
            # 人手の配置変更は常に正当 (FE が「これは完全固定です」確認を出す)。
            # 置換前の完全固定判断は下の INSERT で引き継ぐ。
            prior_movability_paf: dict[int, str] = {
                row.slot_index: row.movability
                for row in (
                    await db.scalars(
                        select(PatientFixedVisit).where(
                            PatientFixedVisit.patient_id == body.patient_id,
                            PatientFixedVisit.mode == fv_mode,
                            PatientFixedVisit.weekday == body.weekday,
                            PatientFixedVisit.slot_index.in_(slot_targets),
                        )
                    )
                ).all()
            }
            await db.execute(
                delete(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == body.patient_id,
                    PatientFixedVisit.mode == fv_mode,
                    PatientFixedVisit.weekday == body.weekday,
                    PatientFixedVisit.slot_index.in_(slot_targets),
                )
            )
            for slot_idx, tpl_id in enumerate(template_ids):
                # 統合 (2026-08-09): 置換前の完全固定判断を引き継ぐ (人手の時刻変更で
                # 「完全固定である」という判断まで消さない)。is_pinned はミラー。
                _mov = prior_movability_paf.get(slot_idx, "unknown")
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
                    movability=_mov,
                    is_pinned=(_mov == "locked"),
                )
                db.add(fv)
                new_fvs.append(fv)
            await db.flush()

        # ----- 2b) §7-3: acknowledge を通した事実を管理者へお知らせ (commit 前 = 同一 TX) -----
        for _c, _w in _cw_by_course:
            await notify_constraint_override_for_course(
                db, course=_c, warnings=_w, actor=current_user, op_group_id=body.op_group_id
            )

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

    # Wave U-3: fix_pattern=False のみ操作ジャーナルに記録（ベストエフォート）
    if not body.fix_pattern and new_visits:
        visit_ids_str = [str(v.id) for v in new_visits]
        _patient_name = getattr(patient, "name", None) or str(body.patient_id)
        _wd_jp = fmt_weekday(body.weekday)
        _st_str = fmt_time(body.start_time)
        _label = f"{_patient_name}様を {_wd_jp}{_st_str} に配置（この週のみ）"
        await record_op(
            db,
            user_id=current_user.id,
            iso_year=body.iso_year,
            iso_week=body.iso_week,
            op_group_id=body.op_group_id,
            op_kind="place_and_fix",
            label=_label,
            forward_payload={"op": "restore_visits", "visit_ids": visit_ids_str},
            inverse_payload={"op": "soft_delete_visits", "visit_ids": visit_ids_str},
        )
        await db.commit()

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
    _user: Annotated[User, Depends(require_role("admin"))],
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
    # W37 hotfix C-2: multi-staff 片側欠けで visit 生成をスキップした保留リスト.
    unplaced_multi_staff_patients: list[UnplacedMultiStaffEntry] = Field(default_factory=list)


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


class RotationConflictWarning(BaseModel):
    """Phase G-89: ローテ衝突警告 (= 人手不足で直近担当者を再割り当てした).

    最終割当で患者の担当が「直近担当者リスト」に入っていたケース。 人手不足のため
    「同じ人を避ける」 を維持できず、 訪問を確保するため直近担当者を再度当てた。
    管理者は内容を見て、 必要なら担当 dropdown で手動変更する (= 埋めて事後警告).
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str | None = None
    staff_id: UUID
    staff_name: str | None = None
    course_id: UUID
    course_code: str
    weekday: int = Field(ge=0, le=6)
    visit_start_time: time | None = None
    # working_recent リスト内 index. 0 = 1 つ前と同じ (= 連続), 1 = 2 個前と同じ.
    # working_recent は PATIENT_RECENT_DEPTH で truncate されるため有効域は 0..DEPTH-1。
    # 衝突集合は cost 関数の penalty map と同一ドメイン (= 同じ working_recent) を見る。
    recent_index: int = Field(ge=0, le=PATIENT_RECENT_DEPTH - 1)
    # 連続性 (= 前回と同じ担当). recent_index==0 のとき True.
    is_consecutive: bool


class UnassignedCourseWarning(BaseModel):
    """Phase G-89: 未割当コース警告 (= 人手不足で担当を確保できなかった).

    visits>0 なのに担当が 1 人も割り当てられなかったコース。 管理者は対象患者を見て、
    必要なら担当 dropdown で手動補填する.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    course_code: str
    weekday: int = Field(ge=0, le=6)
    visit_start_time: time | None = None
    patient_ids: list[UUID] = Field(default_factory=list)
    patient_names: list[str] = Field(default_factory=list)


class ReviewVisitSchema(BaseModel):
    """Phase G-91: レビューカード内の 1 visit (= コース所属の 1 患者訪問)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str
    start_time: time
    sex_restriction: str | None = None
    # 当該 review item の原因患者 (連続になる患者 / 性別 NG 患者) なら True.
    is_cause: bool


class ReviewItemSchema(BaseModel):
    """Phase G-91: 確認レビューフローの 1 カード (= レビュー対象の 1 コース).

    「自動スタッフ割付」 実行時、 クリーンなコースは自動確定 (commit) するが、
    以下 2 種はレビュー対象として返す (= DB 未割当のまま):
      - reason='consecutive' (🟡 連続/軽度): 直近担当者 (recent_index==0) と
        同じスタッフになるケース. candidate は本来割り当たるスタッフ.
      - reason='gender' (🔴 性別/重度): 適合性別の同拠点スタッフが居ないケース.
        candidate は性別制約を無視したら割り当たる候補スタッフ.
      - reason='ng_staff' (⛔ NG スタッフ/重度): コース所属患者の NG スタッフ指定で
        適格者が居ないケース. candidate は NG を無視したら割り当たる候補スタッフ.
        (正典: ``docs/plans/patient-ng-staff-design.md`` §4-2 / §5)
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    office_name: str
    course_code: str
    weekday: int = Field(ge=0, le=6)
    reason: Literal["consecutive", "gender", "ng_staff"]
    candidate_staff_id: UUID
    candidate_staff_name: str
    candidate_staff_sex: str | None = None
    visits: list[ReviewVisitSchema] = Field(default_factory=list)
    # Phase G-91 (修正4): 2 名体制で連動する partner course_id 一覧.
    # X と Y は片方だけ apply すると half-assigned になるため、 apply 時に同一
    # _persist へ一緒に渡す (BE 側 apply-staff-review が group 単位で自動補完する).
    linked_course_ids: list[UUID] = Field(default_factory=list)
    # NG スタッフ: ``reason`` 以外にも同時に違反している制約の種類 (例: ['ng_staff']).
    # 性別と NG が同時に候補ゼロを作ったときのみ立ち、 FE が確認文言に併記する.
    # **通常は空配列** (= 追加のみ・default 空で後方互換).
    also_violates: list[str] = Field(default_factory=list)


class AutoCommittedNoticeSchema(BaseModel):
    """Wave N-1: 不可避連続の「お知らせ」(= 代替候補 0 名でレビュー無し自動確定した連続コース).

    設計書 R-2: 代替候補が存在しない連続 (体制上避けられない) は、 レビュー (承認ゲート)
    に出さず自動確定し、 管理者に理由つきで伝える情報提示として返す.
    ``review_items`` とは排他 (= 同一コースが両方に出ることはない).
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    course_code: str
    weekday: int = Field(ge=0, le=6)
    office_name: str
    staff_name: str
    cause_patient_names: list[str] = Field(default_factory=list)
    reason_kind: str  # 'single_staff' | 'all_recent'
    reason_text: str


class UnresolvedGenderWarningSchema(BaseModel):
    """W-11: 性別制約を満たす候補ゼロで自動解消できなかった残留違反の警告.

    性別ブロックで未割当になったコースに override 候補が 1 名も居らず、 かつ現在の
    ``assigned_staff_id`` が性別制約を満たしていない (= 過去の割付で残った違反) 場合、
    「黙って消さない」原則のもと自動クリアはせず可視化のみを行う。 ``review_items``
    (承認可) とも ``auto_committed_notices`` (確定済みお知らせ) とも別枠で、
    管理者に手動調整を促すアクション不能の警告として返す。
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    course_code: str
    weekday: int = Field(ge=0, le=6)
    office_name: str
    current_staff_name: str
    reason_text: str


class UnresolvedNgWarningSchema(BaseModel):
    """NG スタッフ制約を満たす候補ゼロで自動解消できなかった残留違反の警告.

    ``UnresolvedGenderWarningSchema`` (W-11) と同型 + 原因患者名リスト.
    既存 ``unresolved_warnings`` (性別) のスキーマは一切変えず、 NG は
    ``unresolved_ng_warnings`` として **別フィールド**で返す (FE zod 後方互換).

    現在の ``assigned_staff_id`` がコース所属患者の NG スタッフのまま残っている
    ことを可視化するのみ (= 自動クリアしない・アクション不能・手動調整を促す).
    正典: ``docs/plans/patient-ng-staff-design.md`` §4-2 / §5.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    course_code: str
    weekday: int = Field(ge=0, le=6)
    office_name: str
    current_staff_name: str
    # 当該スタッフを NG 指定している患者名の一覧 (= 原因患者. 決定的順).
    patient_names: list[str] = Field(default_factory=list)
    reason_text: str


class SecondaryConstraintWarningSchema(BaseModel):
    """PO 決定4: 2 名体制 secondary の性別 / NG 違反警告 1 件.

    ``_persist`` の secondary 解決 (= partner course の担当をそのまま secondary に
    据える経路) は従来 性別も NG も未検証だった. 本警告はその穴を可視化する.

    **違反しても割当自体は行う** (= 2 名体制の構造的ペアリングを壊さない). 管理者が
    後から手動で是正するための情報提示.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    course_code: str
    office_name: str
    weekday: int = Field(ge=0, le=6)
    staff_id: UUID
    staff_name: str
    patient_id: UUID
    patient_name: str
    kind: Literal["gender", "ng_staff"]


class StageAssignmentNoticeSchema(BaseModel):
    """4段ソルバ Stage 2: マネージャー動員で確定した割当のお知らせ 1 件.

    ``manager_mobilized_notices`` (= マネージャー不足救済) のスキーマ.
    「誰がどのコースを埋めたか」を伝える情報通知 (確定済み・アクション不要).
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    weekday: int = Field(ge=0, le=6)
    course_code: str
    staff_id: UUID
    staff_name: str


class CrossOfficeNoticeSchema(BaseModel):
    """4段ソルバ Stage 3: 拠点を跨いで救援に入った割当のお知らせ 1 件 (警告).

    自拠点の要員だけでは埋まらないコースに、 隣接拠点の要員が越境して入ったことを
    伝える. course の拠点名と staff の所属拠点名を併記し、 越境の事実を明示する
    (= PO 必須要件「お知らせ・警告・報告」の警告).
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    weekday: int = Field(ge=0, le=6)
    course_code: str
    course_office_name: str
    staff_id: UUID
    staff_name: str
    staff_office_name: str


class RescueSwapNoticeSchema(BaseModel):
    """4段ソルバ Stage 3: 拠点跨ぎ救援に伴う担当の入れ替えお知らせ 1 件 (報告).

    救援のため当日を全再解した結果、 担当が入れ替わったコースを 1 件 1 record で
    報告する. ``before_staff_name`` は **同一実行内の Stage 1+2 暫定解** の担当であり、
    DB の既存確定値ではない点に注意 (= 「今回の割付でこう入れ替えた」報告).
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    weekday: int = Field(ge=0, le=6)
    course_code: str
    before_staff_name: str
    after_staff_name: str


class AssignStaffOnlyResponse(BaseModel):
    """``POST /api/v1/schedule/assign-staff-only`` のレスポンス (W17-BE-A2)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    courses_assigned: int
    message: str
    # W27 Phase A: event 時間帯と既存 visit (assigned_staff) の重複警告
    warnings: list[AssignStaffWarning] = Field(default_factory=list)
    # Phase G-89: ローテ衝突 (埋めたが直近担当者を再割り当て) 警告
    rotation_warnings: list[RotationConflictWarning] = Field(default_factory=list)
    # Phase G-89: 未割当 (担当を確保できなかった) コース警告
    unassigned_warnings: list[UnassignedCourseWarning] = Field(default_factory=list)
    # Phase G-91: 確認レビューフロー (= 連続 index0 / 性別ブロック) のカード一覧.
    # クリーンなコースは自動確定し、 本 list のコースは DB 未割当のまま管理者が
    # レビューして apply (= PATCH /courses/{id}) で最終判断する.
    review_items: list[ReviewItemSchema] = Field(default_factory=list)
    # Wave N-1: 不可避連続の「お知らせ」(確定済み・アクション不要).
    # 後方互換 (= 既存クライアントは本フィールドを無視できる).
    auto_committed_notices: list[AutoCommittedNoticeSchema] = Field(default_factory=list)
    # W-11: 性別制約を満たす候補ゼロで残った違反 (アクション不能・手動調整を促す警告).
    # 後方互換 (= 既存クライアントは本フィールドを無視できる).
    unresolved_warnings: list[UnresolvedGenderWarningSchema] = Field(default_factory=list)
    # NG スタッフ: NG 制約を満たす候補ゼロで残った違反 (アクション不能・手動調整を促す).
    # 追加のみ (additive)・default 空配列で後方互換 (= 既存 FE zod は無視できる).
    unresolved_ng_warnings: list[UnresolvedNgWarningSchema] = Field(default_factory=list)
    # PO 決定4: 2 名体制 secondary の性別 / NG 違反警告 (割当は済・見える化のみ).
    # 追加のみ (additive)・default 空配列で後方互換.
    secondary_constraint_warnings: list[SecondaryConstraintWarningSchema] = Field(
        default_factory=list
    )
    # 4段ソルバ Stage 2: スタッフ不足でマネージャーを動員して埋めたコース一覧.
    # 追加のみ (additive)・default 空配列で後方互換.
    manager_mobilized_notices: list[StageAssignmentNoticeSchema] = Field(default_factory=list)
    # 4段ソルバ Stage 3: 拠点を跨いで救援に入った割当一覧 (警告).
    # 追加のみ (additive)・default 空配列で後方互換.
    cross_office_notices: list[CrossOfficeNoticeSchema] = Field(default_factory=list)
    # 4段ソルバ Stage 3: 拠点跨ぎ救援に伴う担当入れ替え一覧 (報告).
    # 追加のみ (additive)・default 空配列で後方互換.
    rescue_swap_notices: list[RescueSwapNoticeSchema] = Field(default_factory=list)


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
    _user: Annotated[User, Depends(require_role("admin"))],
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

        # 新人同行 (§5.1-1): 展開成功後・commit 前に既定を当該週へ物質化する (冪等)。
        await expand_accompaniment_defaults(db, payload.iso_year, payload.iso_week)

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
        # W37 hotfix C-2: multi-staff 片側欠けの保留リスト
        unplaced_multi_staff_patients=l1_result.unplaced_multi_staff_patients,
    )


# ---------------------------------------------------------------------------
# Endpoint: /assign-staff-only (W17-BE-A2)
# ---------------------------------------------------------------------------

# 2026-07-11 二度押しガード (handoff §4.7): assign-staff-only は再実行で別解を出す
# 非冪等性があり、実行中にもう一度叩くと「1回目の割当済みスタッフを busy 扱いで除外
# → manager/trainee に流れる」事故が起きた (2026-07-10 04:47 に 7 秒差で 2 回実行)。
# 週単位のプロセス内ロックで実行中の重複リクエストを 409 で拒否する。
# 注意: uvicorn 複数 worker 構成ではプロセス間を跨げない (現行本番は単一プロセス)。
_assign_staff_only_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _get_assign_staff_only_lock(iso_year: int, iso_week: int) -> asyncio.Lock:
    """(iso_year, iso_week) 単位のロックを返す (office 指定の有無に関わらず週全体で排他)."""
    key = (iso_year, iso_week)
    lock = _assign_staff_only_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _assign_staff_only_locks[key] = lock
    return lock


@router.post(
    "/assign-staff-only",
    response_model=AssignStaffOnlyResponse,
    status_code=status.HTTP_200_OK,
    summary="W17-BE-A2: Layer 3 のみ — 既存 visits 保持で staff 割付",
)
async def assign_staff_only(
    payload: AssignStaffOnlyRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> AssignStaffOnlyResponse:
    """assign-staff-only の HTTP 入口 — 週単位の二度押しガード付き (2026-07-11).

    実行中の週に対する重複リクエストは 409 で即時拒否する (実装は
    ``_assign_staff_only_impl``)。非冪等な再実行が 7 秒差で走って
    コース担当が別解で上書きされた事故 (2026-07-10) の再発防止。
    """
    lock = _get_assign_staff_only_lock(payload.iso_year, payload.iso_week)
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "自動スタッフ割当を実行中です。完了までお待ちください"
                "（二度押し防止のため同じ週への同時実行はできません）。"
            ),
        )
    async with lock:
        return await _assign_staff_only_impl(payload, db)


async def _assign_staff_only_impl(
    payload: AssignStaffOnlyRequest,
    db: AsyncSession,
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

        # 割付 (visits 確保) を先に commit する。 Phase G-89 のローテ/未割当警告は
        # 「埋めて事後警告」 の化粧処理であり、 その構築失敗で割付を巻き戻してはならない。
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

    # 新人同行 (§5.1-3): proposed→確定 に遷移したコースへ既定を展開する (取りこぼし
    # 回収)。割付 commit は済んでいるため、直下の G-89 警告収集と同じく best-effort
    # とする (ここで 500 を返すと「割付成功なのに失敗に見える」→ 二度押しを誘発する。
    # 展開は冪等で、失敗分は次回の週生成 / 固定枠に戻す / 本 API 再実行で回収される)。
    try:
        await expand_accompaniment_defaults(db, payload.iso_year, payload.iso_week)
        await db.commit()
    except Exception:  # noqa: BLE001 — 事後展開の失敗で確定済み割付を壊さない
        logger.warning(
            "trainee accompaniment expand after assign-staff-only failed (week %s-W%02d)",
            payload.iso_year,
            payload.iso_week,
            exc_info=True,
        )
        await db.rollback()

    # ----- Phase G-89: ローテ衝突 / 未割当コースの warnings 収集 (commit 後・graceful) -----
    # commit 済みなので以降は読み取り専用 SELECT のみ。 警告構築が失敗しても
    # 確保済みの割付は壊さず、 空 warnings で 200 を返す (= 事後警告は best-effort).
    # Phase G-91 (修正5): review 化したコース (= 確認レビューフローのカードに
    # 出すコース) は rotation/unassigned warnings から除外する (= 同一事象の
    # 二重出力解消). review_item.course_id がそのまま除外集合になる.
    review_course_ids = {item.course_id for item in l3_result.review_items}
    try:
        rotation_warnings, unassigned_warnings = await _build_rotation_and_unassigned_warnings(
            db,
            l3_result=l3_result,
            exclude_course_ids=review_course_ids,
        )
    except Exception:
        logger.exception("warning build failed post-commit; returning empty warnings")
        rotation_warnings, unassigned_warnings = [], []

    # Phase G-91: 確認レビューフローのカードをレスポンス schema へ変換する.
    # ``l3_result.review_items`` は assign() が commit 前に構築済み (= solve 結果 +
    # DB 読み). 連続 index0 コース + その partner は _persist 対象から除外済 (DB 未割当).
    review_items = _build_review_items_response(l3_result.review_items)

    # Wave N-1: 不可避連続の「お知らせ」をスキーマ変換.
    auto_committed_notices = _build_auto_committed_notices_response(
        l3_result.auto_committed_notices
    )

    # W-11: 性別残留違反 (候補ゼロ) の警告をスキーマ変換.
    unresolved_warnings = _build_unresolved_warnings_response(l3_result.unresolved_warnings)

    # NG スタッフ: 残留違反 / secondary 制約違反の警告をスキーマ変換 (commit 後・
    # best-effort). 変換自体は純粋写像だが、 事後警告は「構築失敗で確定済み割付を
    # 壊さない」既存方針 (= 空リストで 200) に揃える.
    try:
        unresolved_ng_warnings = _build_unresolved_ng_warnings_response(
            l3_result.unresolved_ng_warnings
        )
        secondary_constraint_warnings = _build_secondary_constraint_warnings_response(
            l3_result.secondary_constraint_warnings
        )
    except Exception:
        logger.exception("ng warning build failed post-commit; returning empty warnings")
        unresolved_ng_warnings, secondary_constraint_warnings = [], []

    # 4段ソルバ: Stage 2 動員 / Stage 3 跨ぎ救援のお知らせを via 集計で構築
    # (commit 後・graceful). 事後お知らせは best-effort とし、構築失敗で確定済み割付を
    # 壊さない (空リストで 200).
    try:
        (
            manager_mobilized_notices,
            cross_office_notices,
            rescue_swap_notices,
        ) = await _build_stage_mobilization_notices(db, l3_result=l3_result)
    except Exception:
        logger.exception("stage mobilization notice build failed post-commit; returning empty")
        manager_mobilized_notices, cross_office_notices, rescue_swap_notices = [], [], []

    # Phase G-91 (修正2): 自動確定 (commit) したコース数 = ``committed_course_ids``
    # (= _persist へ実際に渡した assignments の course). 連続コースだけでなく、
    # その visit_group partner (clean 含む) も非 commit のため、 単純な
    # assignments - consecutive では partner を過大計上していた. assign() が
    # 構築する commit 実数 list を単一ソースにする.
    courses_assigned = len(l3_result.committed_course_ids)
    review_count = len(review_items)
    notice_count = len(auto_committed_notices)

    return AssignStaffOnlyResponse(
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        courses_assigned=courses_assigned,
        message=(
            f"Assigned staff to {courses_assigned} courses "
            f"({review_count} courses need review"
            + (f", {notice_count} unavoidable consecutive auto-committed" if notice_count else "")
            + f") for ISO {payload.iso_year}-W{payload.iso_week}"
            + (f" (office {payload.office_id})" if payload.office_id else "")
        ),
        warnings=warnings,
        rotation_warnings=rotation_warnings,
        unassigned_warnings=unassigned_warnings,
        review_items=review_items,
        auto_committed_notices=auto_committed_notices,
        unresolved_warnings=unresolved_warnings,
        unresolved_ng_warnings=unresolved_ng_warnings,
        secondary_constraint_warnings=secondary_constraint_warnings,
        manager_mobilized_notices=manager_mobilized_notices,
        cross_office_notices=cross_office_notices,
        rescue_swap_notices=rescue_swap_notices,
    )


def _build_review_items_response(items: list[ReviewItem]) -> list[ReviewItemSchema]:
    """Phase G-91: Layer3 の ``ReviewItem`` dataclass をレスポンス schema へ変換.

    DB I/O は ``Layer3Assigner._build_review_items`` が commit 前に済ませているため、
    本関数は純粋なフィールド写像のみ (= 失敗しない). 並びは Layer3 側で決定的に
    ソート済み (weekday, course_code, 代表 start_time) なので保持する.
    """
    return [
        ReviewItemSchema(
            course_id=item.course_id,
            office_name=item.office_name,
            course_code=item.course_code,
            weekday=item.weekday,
            reason=item.reason,  # type: ignore[arg-type]  # 'consecutive'|'gender' を Literal へ
            candidate_staff_id=item.candidate_staff_id,
            candidate_staff_name=item.candidate_staff_name,
            candidate_staff_sex=item.candidate_staff_sex,
            visits=[
                ReviewVisitSchema(
                    patient_id=v.patient_id,
                    patient_name=v.patient_name,
                    start_time=v.start_time,
                    sex_restriction=v.sex_restriction,
                    is_cause=v.is_cause,
                )
                for v in item.visits
            ],
            linked_course_ids=item.linked_course_ids,
            also_violates=list(item.also_violates),
        )
        for item in items
    ]


def _build_auto_committed_notices_response(
    notices: list[AutoCommittedNotice],
) -> list[AutoCommittedNoticeSchema]:
    """Wave N-1: Layer3 の ``AutoCommittedNotice`` dataclass をレスポンス schema へ変換.

    純粋なフィールド写像 (= DB I/O なし). 並びは Layer3 側で (weekday, course_code)
    昇順にソート済みなので保持する.
    """
    return [
        AutoCommittedNoticeSchema(
            course_id=n.course_id,
            course_code=n.course_code,
            weekday=n.weekday,
            office_name=n.office_name,
            staff_name=n.staff_name,
            cause_patient_names=list(n.cause_patient_names),
            reason_kind=n.reason_kind,
            reason_text=n.reason_text,
        )
        for n in notices
    ]


def _build_unresolved_warnings_response(
    warnings: list[UnresolvedGenderWarning],
) -> list[UnresolvedGenderWarningSchema]:
    """W-11: Layer3 の ``UnresolvedGenderWarning`` dataclass をレスポンス schema へ変換.

    純粋なフィールド写像 (= DB I/O なし). 並びは Layer3 側で (weekday, course_code)
    昇順にソート済みなので保持する.
    """
    return [
        UnresolvedGenderWarningSchema(
            course_id=w.course_id,
            course_code=w.course_code,
            weekday=w.weekday,
            office_name=w.office_name,
            current_staff_name=w.current_staff_name,
            reason_text=w.reason_text,
        )
        for w in warnings
    ]


def _build_unresolved_ng_warnings_response(
    warnings: list[UnresolvedNgStaffWarning],
) -> list[UnresolvedNgWarningSchema]:
    """NG 残留違反 (候補ゼロ) の dataclass をレスポンス schema へ変換.

    ``_build_unresolved_warnings_response`` (性別版) と同型の純粋なフィールド写像
    (= DB I/O なし). 並びは Layer3 側で (weekday, course_code) 昇順にソート済み.
    """
    return [
        UnresolvedNgWarningSchema(
            course_id=w.course_id,
            course_code=w.course_code,
            weekday=w.weekday,
            office_name=w.office_name,
            current_staff_name=w.current_staff_name,
            patient_names=list(w.patient_names),
            reason_text=w.reason_text,
        )
        for w in warnings
    ]


def _build_secondary_constraint_warnings_response(
    warnings: list[SecondaryConstraintWarning],
) -> list[SecondaryConstraintWarningSchema]:
    """PO 決定4: secondary の性別 / NG 違反警告をレスポンス schema へ変換.

    純粋なフィールド写像 (= DB I/O は ``Layer3Assigner._persist`` が commit 前に完了).
    並びは Layer3 側で決定的にソート済みなので保持する.
    """
    return [
        SecondaryConstraintWarningSchema(
            course_id=w.course_id,
            course_code=w.course_code,
            office_name=w.office_name,
            weekday=w.weekday,
            staff_id=w.staff_id,
            staff_name=w.staff_name,
            patient_id=w.patient_id,
            patient_name=w.patient_name,
            kind=w.kind,  # type: ignore[arg-type]  # 'gender'|'ng_staff' を Literal へ
        )
        for w in warnings
    ]


# ---------------------------------------------------------------------------
# Endpoint: /apply-staff-review (Phase G-91 修正1)
# ---------------------------------------------------------------------------


class ApplyStaffReviewItem(BaseModel):
    """``POST /api/v1/schedule/apply-staff-review`` のリクエスト 1 件 (= 1 コース)."""

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    staff_id: UUID
    # NG スタッフ §7-3: 承認元 review_item の reason / also_violates。
    # 'gender' / 'ng_staff' を承認した = 制約 override なので管理者へお知らせを出す
    # (確認 UI は既存の 2 ステップがあるため 422 フローは不要・通知のみ)。
    # **後方互換**: 省略 (= 旧 FE) のときは通知を作らず従来どおり通す。
    reason: Literal["consecutive", "gender", "ng_staff"] | None = None
    also_violates: list[str] = Field(default_factory=list)


class ApplyStaffReviewRequest(BaseModel):
    """``POST /api/v1/schedule/apply-staff-review`` のリクエストボディ (Phase G-91 修正1).

    確認レビューフローで管理者が承認したカードを、 自動スタッフ割付と **完全に
    同じ反映経路** (= ``Layer3Assigner._persist``) で DB に書き込むためのペイロード.
    ``items`` の各要素 = (course_id, 割り当てる staff_id).
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    items: list[ApplyStaffReviewItem] = Field(min_length=1)
    # 同行×担当の逆方向警告 (一般化 決定#1 後段) の通知冪等キー。
    # **非破壊追加** (省略可): 与えられれば同一 apply の再送で通知が増えない。
    # 省略時はサーバが毎回発行する (= 再送のたびに 1 通)。
    op_group_id: UUID | None = None


class ApplyStaffReviewResultItem(BaseModel):
    """apply 結果 1 件 (= 1 コース). 成功/失敗を FE に返す (部分失敗対応)."""

    model_config = ConfigDict(extra="forbid")

    course_id: UUID
    ok: bool
    error: str | None = None


class ApplyStaffReviewResponse(BaseModel):
    """``POST /api/v1/schedule/apply-staff-review`` のレスポンス (Phase G-91 修正1)."""

    model_config = ConfigDict(extra="forbid")

    applied_count: int
    results: list[ApplyStaffReviewResultItem] = Field(default_factory=list)
    message: str
    # 一般化 決定#1 後段 (docs/plans/general-accompaniment-design.md):
    # 担当を付けた日に、その人の同行リンクが入っていた場合の警告 (**非ブロック**)。
    # 非破壊追加 (既定 []) — 旧 FE は無視できる。
    accompaniment_warnings: list[dict] = Field(default_factory=list)


@router.post(
    "/apply-staff-review",
    response_model=ApplyStaffReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase G-91: 確認レビューフローの承認カードを _persist 経由で反映",
)
async def apply_staff_review(
    payload: ApplyStaffReviewRequest,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> ApplyStaffReviewResponse:
    """確認レビューフローで承認されたコースを ``_persist`` 経由で DB 反映する.

    **なぜ専用 endpoint か (修正1)**:
        従来の apply は ``PATCH /courses/{id}`` (= assigned_staff_id のみ更新) を
        呼んでいたが、 これだと自動割付 (``_persist``) がやる
          (a) course_status='staff_assigned'
          (b) VisitStaffAssignment INSERT (= スタッフの visit 可視性・子アプリ表示)
          (c) visits.primary_staff_id / secondary_staff_id 同期
          (d) 2 名体制 secondary 解決
          (e) trainee companion 自動付与
        を一切行わず、 apply 済コースの visit が未割当表示のまま・子アプリに
        スタッフが見えない機能リグレッションになる. 本 endpoint は承認カードから
        ``StaffAssignment`` を組み、 自動割付と **同一の ``_persist``** を呼ぶ.

    **2 名体制ペア (修正4)**:
        承認 item の course が visit_group (2 名体制) に属する場合、 同 group の
        partner course も自動で本割付対象に含める (= group 単位で同一 ``_persist``
        呼出に渡し、 secondary を正しく解決する). partner の staff は items で
        与えられた値 (= review_item の candidate) を優先し、 無ければ現 DB 値を使う.

    冪等性 / トランザクション:
        - 各 item の course を存在確認 (当該週・非削除) し、 不正 item は results に
          ok=False で記録するが他 item は反映する (= best-effort 部分適用).
        - 反映可能な assignments が 1 件でもあれば ``_persist`` → ``commit``.
        - 監査はミドルウェアが POST を自動記録する (= 追加実装不要).

    RBAC: admin / manager only.
    """
    # ----- ISO 週バリデーション -----
    try:
        date.fromisocalendar(payload.iso_year, payload.iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={payload.iso_year} week={payload.iso_week}",
        ) from exc

    # ----- 重複 course_id 排除 (= 同一 course が複数 item にある場合は先勝ち) -----
    requested_staff_by_course: dict[UUID, UUID] = {}
    # NG スタッフ §7-3: 制約 override として通知すべき course → 種別集合。
    # reason 省略 (旧 FE) の item は空のまま = 通知なし (後方互換)。
    override_kinds_by_course: dict[UUID, set[str]] = {}
    for it in payload.items:
        requested_staff_by_course.setdefault(it.course_id, it.staff_id)
        _kinds = ({it.reason} if it.reason else set()) | set(it.also_violates)
        override_kinds_by_course.setdefault(it.course_id, set()).update(
            _kinds & {"gender", "ng_staff"}
        )

    # 新人同行 §8: payload の staff_id に新人 (is_trainee=true) が含まれる場合は 422。
    # 新人はコースを持たない運用 (PO確定)。無検証で _persist に流すと手動レビュー承認
    # 経由で新人がコース担当になり得る穴を塞ぐ。1 クエリでバッチ検証 (N+1 禁止)。
    # 文言は PATCH /courses (courses.py) と統一。
    _payload_staff_ids = set(requested_staff_by_course.values())
    if _payload_staff_ids:
        _trainee_staff_ids = set(
            (
                await db.scalars(
                    select(Staff.id).where(
                        Staff.id.in_(list(_payload_staff_ids)),
                        Staff.is_trainee.is_(True),
                        Staff.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if _trainee_staff_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="新人はコース担当にできません（同行で割り当ててください）",
            )

    results: list[ApplyStaffReviewResultItem] = []
    accompaniment_warnings: list[dict] = []

    try:
        # ----- 対象 course を当該週・非削除でロード -----
        course_rows = list(
            (
                await db.scalars(
                    select(Course).where(
                        Course.id.in_(list(requested_staff_by_course.keys())),
                        Course.iso_year == payload.iso_year,
                        Course.iso_week == payload.iso_week,
                        Course.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        course_by_id: dict[UUID, Course] = {c.id: c for c in course_rows}

        # 不正 item (= 当該週に存在しない course) は ok=False で記録.
        valid_course_ids: list[UUID] = []
        for course_id in requested_staff_by_course:
            if course_id in course_by_id:
                valid_course_ids.append(course_id)
            else:
                results.append(
                    ApplyStaffReviewResultItem(
                        course_id=course_id,
                        ok=False,
                        error="course not found in the specified week",
                    )
                )

        if not valid_course_ids:
            await db.rollback()
            return ApplyStaffReviewResponse(
                applied_count=0,
                results=results,
                message="No valid courses to apply",
            )

        # ----- 修正4: 2 名体制 partner course を group 単位で自動補完 -----
        # 承認 course の visit_group partner も同一 _persist へ渡し、 secondary を
        # 正しく解決する (= 片方だけ反映の half-assigned を防ぐ).
        apply_staff_by_course: dict[UUID, UUID] = {
            cid: requested_staff_by_course[cid] for cid in valid_course_ids
        }
        group_id_rows = (
            await db.execute(
                select(Visit.visit_group_id, Visit.course_id).where(
                    Visit.course_id.in_(valid_course_ids),
                    Visit.visit_group_id.isnot(None),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        group_ids = {gid for gid, _ in group_id_rows if gid is not None}
        if group_ids:
            grp_member_rows = (
                await db.execute(
                    select(Visit.course_id).where(
                        Visit.visit_group_id.in_(list(group_ids)),
                        Visit.status == VISIT_STATUS_PLANNED,
                        Visit.deleted_at.is_(None),
                    )
                )
            ).all()
            partner_course_ids = {c_id for (c_id,) in grp_member_rows if c_id is not None}
            # 未指定の partner course は items に staff があれば使い、 無ければ現 DB 値.
            missing_partner_ids = [
                c_id
                for c_id in partner_course_ids
                if c_id not in apply_staff_by_course and c_id not in valid_course_ids
            ]
            if missing_partner_ids:
                partner_rows = list(
                    (
                        await db.scalars(
                            select(Course).where(
                                Course.id.in_(missing_partner_ids),
                                Course.iso_year == payload.iso_year,
                                Course.iso_week == payload.iso_week,
                                Course.deleted_at.is_(None),
                            )
                        )
                    ).all()
                )
                for pc in partner_rows:
                    course_by_id.setdefault(pc.id, pc)
                    if pc.id in requested_staff_by_course:
                        apply_staff_by_course[pc.id] = requested_staff_by_course[pc.id]
                    elif (
                        pc.course_status == COURSE_STATUS_STAFF_ASSIGNED
                        and pc.assigned_staff_id is not None
                    ):
                        # 既に確定済みの clean partner は本割付に含めない (= 不要な VSA
                        # 再生成・staff_assigned_at 更新を避ける). _persist は X の visit
                        # を処理する際、 未指定 partner course の現 DB assigned_staff_id を
                        # 自前で読んで secondary を解決するため、 含めなくても整合する.
                        pass
                    elif pc.assigned_staff_id is not None:
                        # 未確定 (proposed/course_fixed) だが既に staff が付いている partner は
                        # その staff で本割付対象に含める (= status を staff_assigned に確定させる).
                        apply_staff_by_course[pc.id] = pc.assigned_staff_id
                    # assigned_staff_id も無い partner は解決不能 → 含めない
                    # (= secondary 無しで反映; _persist は partner 無し扱いになる).

        # ----- StaffAssignment 構築 -----
        assignments: list[StaffAssignment] = []
        for course_id, staff_id in apply_staff_by_course.items():
            course = course_by_id.get(course_id)
            if course is None:
                continue
            assignments.append(
                StaffAssignment(
                    weekday=course.weekday,
                    course_code=course.code,
                    course_id=course.id,
                    staff_id=staff_id,
                )
            )

        # ----- NG スタッフ §7-3: 制約 override の承認を管理者へお知らせ -----
        # 確認 UI は既存の 2 ステップ (AssignWarningDialog) があるため 422 フローは
        # 不要。ここは「通した事実」の通知のみ。commit 前に add = 反映と同一 TX。
        # apply 単位の識別子が無いため reference_id=None (= 毎回通知)。
        # 通知本文の明細 (患者名 / NG メモ) は course × staff から算出する。
        _notify_course_ids = [
            cid
            for cid, kinds in override_kinds_by_course.items()
            if kinds and cid in apply_staff_by_course
        ]
        if _notify_course_ids:
            _notify_staff_ids = {apply_staff_by_course[cid] for cid in _notify_course_ids}
            _staff_by_id = {
                s.id: s
                for s in (
                    await db.scalars(select(Staff).where(Staff.id.in_(list(_notify_staff_ids))))
                ).all()
            }
            for cid in _notify_course_ids:
                _course = course_by_id.get(cid)
                _staff = _staff_by_id.get(apply_staff_by_course[cid])
                if _course is None or _staff is None:
                    continue
                await notify_constraint_override(
                    db,
                    kind_summary=override_kinds_by_course[cid],
                    course=_course,
                    staff=_staff,
                    patient_warnings=await collect_constraint_warnings(
                        db, course_id=cid, staff=_staff
                    ),
                    actor=current_user,
                    op_group_id=None,
                )

        # ----- 一般化 決定#1 後段: 逆方向 (同行が先・担当が後) の警告 + 管理者通知 -----
        # ブロックはしない (エンジンのハード対応は別案件)。「担当する訪問と同行が
        # **時間帯で交差**する」ものだけを FE のトーストと管理者ベルの両方へ出す
        # (同日というだけで鳴らすとノイズ — レビュー M-4)。
        # 同一スタッフが複数コースに乗る場合はコースをまとめて 1 回で引く (N+1 禁止)。
        _courses_by_staff: dict[UUID, list[UUID]] = {}
        for _cid, _sid in apply_staff_by_course.items():
            if _cid in course_by_id:
                _courses_by_staff.setdefault(_sid, []).append(_cid)
        _all_warns: list[AccompanimentDutyWarning] = []
        for _sid in sorted(_courses_by_staff, key=str):
            _all_warns.extend(
                await collect_accompaniment_duty_warnings(
                    db, staff_id=_sid, course_ids=sorted(_courses_by_staff[_sid], key=str)
                )
            )
        if _all_warns:
            accompaniment_warnings.extend(w.to_payload() for w in _all_warns)
            # apply 1 回分の識別子で **1 通に集約**する (レビュー M-4)。
            # スタッフごとに送ると、同じ op_group_id の 2 通目以降が冪等判定で
            # 落ちて別スタッフの警告が消える — 集約が正しい形。
            # payload に op_group_id が来ていればそれを使い、FE の再送を冪等化する。
            await notify_accompaniment_duty_conflict(
                db,
                warnings=_all_warns,
                actor=current_user,
                op_group_id=payload.op_group_id or uuid.uuid4(),
            )

        # ----- 自動割付と同一の _persist 経由で反映 -----
        assigner = Layer3Assigner()
        await assigner._persist(db, assignments)
        await db.commit()
    except Exception:
        # 修正C: HTTPException / 想定外例外いずれも rollback して再 raise (挙動は同一)。
        await db.rollback()
        raise

    # 成功 item を results に追加 (= apply_staff_by_course に乗った course).
    for course_id in apply_staff_by_course:
        # partner 自動補完分も成功として返す (FE は要求した course のみ参照する).
        results.append(ApplyStaffReviewResultItem(course_id=course_id, ok=True))

    applied_count = len(apply_staff_by_course)
    return ApplyStaffReviewResponse(
        applied_count=applied_count,
        results=results,
        message=f"Applied staff to {applied_count} courses",
        accompaniment_warnings=accompaniment_warnings,
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


# ---------------------------------------------------------------------------
# Phase G-89: ローテ衝突 / 未割当コース warnings 構築
# ---------------------------------------------------------------------------


async def _build_stage_mobilization_notices(
    db: AsyncSession,
    *,
    l3_result: Layer3Result,
) -> tuple[
    list[StageAssignmentNoticeSchema],
    list[CrossOfficeNoticeSchema],
    list[RescueSwapNoticeSchema],
]:
    """4段ソルバの Stage 2/3 割当を ``via`` / ``rescue_swaps`` で集計してお知らせ化する.

    振り分け:
      - ``via='manager_mobilized'`` → ``manager_mobilized_notices`` (Stage 2 動員)
      - ``via='cross_office'``      → ``cross_office_notices`` (Stage 3 越境救援・警告)
      - ``l3_result.rescue_swaps``  → ``rescue_swap_notices`` (Stage 3 入れ替え・報告)

    確定済み（``_persist`` 済み）割当のみを通知する。
    ``l3_result.committed_course_ids`` に含まれない course_id はレビュー送り
    （管理者が承認前）であり、本通知の対象外とする（動員・越境・入替の全リストを
    committed_course_ids で絞る）。承認後に再実行される割付フローで改めて生成される。

    §11.3: BE は各リストを独立に返す (= それぞれ別の事実). 不可避連続やレビューとの
    重複整理は FE 側で視覚的に行う. Staff.name / Office.name は bulk load で解決する.
    commit 後の読み取り専用処理として best-effort で呼ばれる.
    """
    committed_set = set(l3_result.committed_course_ids)
    mobilized = [
        a
        for a in l3_result.assignments
        if a.via == "manager_mobilized" and a.course_id in committed_set
    ]
    cross = [
        a for a in l3_result.assignments if a.via == "cross_office" and a.course_id in committed_set
    ]
    swaps = [s for s in l3_result.rescue_swaps if s.course_id in committed_set]
    if not mobilized and not cross and not swaps:
        return [], [], []

    # ----- Staff 名 + 所属拠点 id を bulk load -----
    staff_ids: set[UUID] = {a.staff_id for a in mobilized} | {a.staff_id for a in cross}
    for s in swaps:
        staff_ids.add(s.before_staff_id)
        staff_ids.add(s.after_staff_id)
    staff_name_map: dict[UUID, str] = {}
    staff_office_map: dict[UUID, UUID | None] = {}
    if staff_ids:
        srows = (await db.scalars(select(Staff).where(Staff.id.in_(list(staff_ids))))).all()
        staff_name_map = {s.id: s.name for s in srows}
        staff_office_map = {s.id: s.primary_office_id for s in srows}

    # ----- Course 拠点 id を bulk load (cross_office のコース所属拠点名解決用) -----
    course_office_map: dict[UUID, UUID | None] = {}
    cross_course_ids = {a.course_id for a in cross}
    if cross_course_ids:
        crows = (
            await db.scalars(select(Course).where(Course.id.in_(list(cross_course_ids))))
        ).all()
        course_office_map = {c.id: c.office_id for c in crows}

    # ----- Office 名を bulk load (course 拠点 + staff 所属拠点) -----
    office_ids: set[UUID] = {oid for oid in course_office_map.values() if oid is not None} | {
        oid for oid in staff_office_map.values() if oid is not None
    }
    office_name_map: dict[UUID, str] = {}
    if office_ids:
        orows = (await db.scalars(select(Office).where(Office.id.in_(list(office_ids))))).all()
        office_name_map = {o.id: o.name for o in orows}

    def _office_name(office_id: UUID | None) -> str:
        if office_id is None:
            return ""
        return office_name_map.get(office_id, "")

    mobilized_notices = [
        StageAssignmentNoticeSchema(
            course_id=a.course_id,
            weekday=a.weekday,
            course_code=a.course_code,
            staff_id=a.staff_id,
            staff_name=staff_name_map.get(a.staff_id, ""),
        )
        for a in mobilized
    ]
    mobilized_notices.sort(key=lambda n: (n.weekday, n.course_code, str(n.staff_id)))

    cross_notices = [
        CrossOfficeNoticeSchema(
            course_id=a.course_id,
            weekday=a.weekday,
            course_code=a.course_code,
            course_office_name=_office_name(course_office_map.get(a.course_id)),
            staff_id=a.staff_id,
            staff_name=staff_name_map.get(a.staff_id, ""),
            staff_office_name=_office_name(staff_office_map.get(a.staff_id)),
        )
        for a in cross
    ]
    cross_notices.sort(key=lambda n: (n.weekday, n.course_code, str(n.staff_id)))

    swap_notices = [
        RescueSwapNoticeSchema(
            course_id=s.course_id,
            weekday=s.weekday,
            course_code=s.course_code,
            before_staff_name=staff_name_map.get(s.before_staff_id, ""),
            after_staff_name=staff_name_map.get(s.after_staff_id, ""),
        )
        for s in swaps
    ]
    swap_notices.sort(key=lambda n: (n.weekday, n.course_code, str(n.course_id)))

    return mobilized_notices, cross_notices, swap_notices


async def _build_rotation_and_unassigned_warnings(
    db: AsyncSession,
    *,
    l3_result: Layer3Result,
    exclude_course_ids: set[UUID] | None = None,
) -> tuple[list[RotationConflictWarning], list[UnassignedCourseWarning]]:
    """Phase G-89: solve() の衝突/未割当検出結果を表示用 warnings に変換する.

    ``l3_result.rotation_conflicts`` (= 直近担当者を再割り当てした衝突) と
    ``l3_result.unassigned_course_ids`` (= 担当を確保できなかったコース) を、
    表示に必要なメタ (患者名 / 担当者名 / コースコード / 代表 visit 時刻) を
    DB から引いて payload 化する。

    Phase G-91 (修正5): ``exclude_course_ids`` に含まれる course は warnings から
    除外する. 確認レビューフロー (review_items) に出すコースは同一事象なので
    rotation/unassigned warnings に重複出力しない (= 二重出力解消).

    決定性:
        - rotation_warnings は (weekday, course_code, visit_start_time, patient_id)
          で安定ソート.
        - unassigned_warnings は (weekday, course_code) で安定ソート、
          各コース内 patient は (visit_start_time, patient_id) で安定ソート.
    """
    excluded = exclude_course_ids or set()
    rotation_conflicts: list[RotationConflict] = [
        c for c in l3_result.rotation_conflicts if c.course_id not in excluded
    ]
    unassigned_ids: list[UUID] = [
        cid for cid in l3_result.unassigned_course_ids if cid not in excluded
    ]
    if not rotation_conflicts and not unassigned_ids:
        return [], []

    # ----- 必要な ID 集合を収集 -----
    course_ids: set[UUID] = {c.course_id for c in rotation_conflicts} | set(unassigned_ids)
    patient_ids: set[UUID] = {c.patient_id for c in rotation_conflicts}
    staff_ids: set[UUID] = {c.staff_id for c in rotation_conflicts}

    # ----- コース (code) -----
    course_map: dict[UUID, Course] = {}
    if course_ids:
        course_rows = (
            await db.scalars(select(Course).where(Course.id.in_(list(course_ids))))
        ).all()
        course_map = {c.id: c for c in course_rows}

    # ----- 未割当コースの所属患者 (patient_ids を補完) -----
    # course_id -> [(visit_start_time, patient_id, patient_name), ...]
    unassigned_patients: dict[UUID, list[tuple[time, UUID]]] = {}
    if unassigned_ids:
        # soft-deleted 患者を除外し、 アロケータの _load_course_targets が見た
        # patient 集合 (Patient.deleted_at.is_(None)) と一致させる。
        vp_rows = (
            await db.execute(
                select(Visit.course_id, Visit.start_time, Visit.patient_id)
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.course_id.in_(list(unassigned_ids)),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                    Patient.deleted_at.is_(None),
                )
            )
        ).all()
        for c_id, start_time, p_id in vp_rows:
            if p_id is None:
                continue
            unassigned_patients.setdefault(c_id, []).append((start_time, p_id))
            patient_ids.add(p_id)

    # ----- 代表 visit start_time (rotation conflict 用): (course_id, patient_id) -> 最小 start_time -----
    rep_start: dict[tuple[UUID, UUID], time] = {}
    if rotation_conflicts:
        rc_pairs = {(c.course_id, c.patient_id) for c in rotation_conflicts}
        rc_rows = (
            await db.execute(
                select(Visit.course_id, Visit.patient_id, Visit.start_time).where(
                    Visit.course_id.in_([cid for cid, _ in rc_pairs]),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        for c_id, p_id, start_time in rc_rows:
            key = (c_id, p_id)
            if key not in rc_pairs:
                continue
            cur = rep_start.get(key)
            if cur is None or start_time < cur:
                rep_start[key] = start_time

    # ----- patient / staff 名 -----
    patient_name_map: dict[UUID, str] = {}
    if patient_ids:
        prows = (await db.scalars(select(Patient).where(Patient.id.in_(list(patient_ids))))).all()
        patient_name_map = {p.id: p.name for p in prows}
    staff_name_map: dict[UUID, str] = {}
    if staff_ids:
        srows = (await db.scalars(select(Staff).where(Staff.id.in_(list(staff_ids))))).all()
        staff_name_map = {s.id: s.name for s in srows}

    # ----- rotation_warnings 構築 -----
    rotation_warnings: list[RotationConflictWarning] = []
    for c in rotation_conflicts:
        course = course_map.get(c.course_id)
        if course is None:
            continue
        rotation_warnings.append(
            RotationConflictWarning(
                patient_id=c.patient_id,
                patient_name=patient_name_map.get(c.patient_id),
                staff_id=c.staff_id,
                staff_name=staff_name_map.get(c.staff_id),
                course_id=c.course_id,
                course_code=course.code,
                weekday=c.weekday,
                visit_start_time=rep_start.get((c.course_id, c.patient_id)),
                recent_index=c.recent_index,
                is_consecutive=(c.recent_index == 0),
            )
        )
    rotation_warnings.sort(
        key=lambda w: (
            w.weekday,
            w.course_code,
            w.visit_start_time.isoformat() if w.visit_start_time is not None else "",
            str(w.patient_id),
        )
    )

    # ----- unassigned_warnings 構築 -----
    unassigned_warnings: list[UnassignedCourseWarning] = []
    for c_id in unassigned_ids:
        course = course_map.get(c_id)
        if course is None:
            continue
        patients = unassigned_patients.get(c_id, [])
        # (visit_start_time, patient_id) で安定ソートし、 distinct な patient を抽出.
        patients_sorted = sorted(patients, key=lambda t: (t[0], str(t[1])))
        seen: set[UUID] = set()
        ordered_pids: list[UUID] = []
        rep: time | None = None
        for start_time, p_id in patients_sorted:
            if rep is None:
                rep = start_time
            if p_id in seen:
                continue
            seen.add(p_id)
            ordered_pids.append(p_id)
        unassigned_warnings.append(
            UnassignedCourseWarning(
                course_id=c_id,
                course_code=course.code,
                weekday=course.weekday,
                visit_start_time=rep,
                patient_ids=ordered_pids,
                patient_names=[patient_name_map.get(pid, "") for pid in ordered_pids],
            )
        )
    unassigned_warnings.sort(key=lambda w: (w.weekday, w.course_code))

    return rotation_warnings, unassigned_warnings


# Suppress F401 for LAYER1_VISIT_SOURCE (kept for documentation / future use)
_ = LAYER1_VISIT_SOURCE


# ---------------------------------------------------------------------------
# W41 v1.0: Auto-allocate (proposal generate / apply / discard)
# ---------------------------------------------------------------------------
#
# 設計仕様書 ``docs/plans/auto-schedule-v1.md`` (v0.4) §10 データ書込仕様
# 実装計画書 ``docs/plans/auto-schedule-implementation-plan.md`` (v0.2) §4 Phase 2
#
# RBAC: 全 3 endpoint とも admin / manager のみ (staff は 403).
# Atomic transaction:
#   - auto-allocate: ``auto_allocator.auto_allocate`` 内部で flush のみ呼ばれる.
#     本 HTTP 層が 1 リクエスト = 1 TX で commit / rollback する.
#   - apply: 提案 course を ``SELECT ... FOR UPDATE`` で行ロックしてから
#     ``proposed → course_fixed → staff_assigned`` に遷移させ、
#     対応する patient_fixed_visits を UPSERT する.
#   - discard: 提案 course (proposed) + 紐付く visits を物理削除する.


@router.post(
    "/auto-allocate",
    response_model=AutoAllocateResponse,
    status_code=status.HTTP_200_OK,
    summary="W41: 週次自動算出 (proposal を生成・KPI を返却)",
)
async def auto_allocate_endpoint(
    payload: AutoAllocateRequest,  # noqa: ARG001 — payload は schema validation のため受け取る
    db: DbDep,  # noqa: ARG001
    _user: Annotated[User, Depends(require_role("admin"))],
) -> AutoAllocateResponse:
    """指定週・拠点に対し自動算出を実行し、proposed course / visit を永続化する.

    **REMOVED (W41 v2 final cross-review H-Codex-3)**: 410 Gone を返す.
    本 endpoint の v1 ロジック (A コース集中) は v2 で完全に置き換えられた.
    使用先:
      - ``POST /api/v1/schedule/v2/diff-add`` (差分追加; read-only 提案)
      - ``POST /api/v1/schedule/v2/full-optimize`` (全面最適化; read-only 提案)
      - ``POST /api/v1/schedule/v2/apply-individual`` (1 件採用)
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "v1 endpoint is removed. Use POST /api/v1/schedule/v2/diff-add or "
            "POST /api/v1/schedule/v2/full-optimize (W41 v2.0)"
        ),
    )


@router.post(
    "/proposal/{proposal_batch_id}/apply",
    response_model=ProposalApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="W41: 提案バッチを採用 (proposed → staff_assigned + 固定枠 UPSERT)",
)
async def apply_proposal(
    proposal_batch_id: UUID,  # noqa: ARG001
    db: DbDep,  # noqa: ARG001
    _user: Annotated[User, Depends(require_role("admin"))],
) -> ProposalApplyResponse:
    """提案バッチを採用し、course_status を ``staff_assigned`` まで進める.

    **REMOVED (W41 v2 final cross-review H-Codex-3)**: 410 Gone を返す.
    使用先: ``POST /api/v1/schedule/v2/apply-individual`` (W41 v2.0 では
    1 件ずつ採用が基本; 一括採用は廃止).
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=("v1 endpoint is removed. Use POST /api/v1/schedule/v2/apply-individual (W41 v2.0)"),
    )


@router.post(
    "/proposal/{proposal_batch_id}/discard",
    response_model=ProposalDiscardResponse,
    status_code=status.HTTP_200_OK,
    summary="W41: 提案バッチを破棄 (proposed course + 紐付く visits を削除)",
)
async def discard_proposal(
    proposal_batch_id: UUID,  # noqa: ARG001
    db: DbDep,  # noqa: ARG001
    _user: Annotated[User, Depends(require_role("admin"))],
) -> ProposalDiscardResponse:
    """提案バッチに紐付く未採用 (proposed) course と visits を物理削除する.

    **REMOVED (W41 v2 final cross-review H-Codex-3)**: 410 Gone を返す.
    v2 では提案を DB に永続化しないため、本 endpoint は不要.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "v1 endpoint is removed. v2 では提案を DB に永続化しないため discard 操作は不要です."
        ),
    )


__all__ = ["router"]
