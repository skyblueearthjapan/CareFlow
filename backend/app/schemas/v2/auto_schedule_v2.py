"""Auto-schedule v2.0 (Wave 41) Pydantic schemas.

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

エンドポイント:
  - POST /api/v1/schedule/v2/diff-add        (機能 A: 差分追加)
  - POST /api/v1/schedule/v2/full-optimize   (機能 B: 全面最適化)
  - POST /api/v1/schedule/v2/apply-individual (機能 A/B 共通: 1 件採用)
  - POST /api/v1/schedule/v2/reset-to-fixed  (機能 D: 固定枠に戻す)

実装方針:
  - auto_allocator_v2 の戻り値 dict と 1:1 対応する shape にする.
  - フロントエンド (frontend/lib/schemas/v2/auto_schedule_v2.ts; 後続作成) と
    完全一致させる。値を変える場合は両側同時に更新.
  - 採用フローは Q2 確定で **1 件ずつ** (一括採用なし).
"""

from __future__ import annotations

import uuid
from datetime import time as time_cls
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------

AmPmV2 = Literal["am", "pm", "any"]


class V2VisitPlan(BaseModel):
    """提案の単位 (1 件の訪問予定; weekday × start_time × course)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start_time: time_cls
    end_time: time_cls
    duration_min: int = Field(ge=1, le=480)
    course_code: str = Field(min_length=1, max_length=2)
    office_id: uuid.UUID
    am_pm: AmPmV2
    assigned_staff_id: uuid.UUID | None = None


class V2VisitForUI(BaseModel):
    """V2CourseSummary.visits 要素 — UI 表示用フィールド.

    Frontend ``frontend/lib/schemas/v2/autoScheduleV2.ts`` の
    ``v2VisitForUiSchema`` と完全一致させる.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    start_time: str  # "HH:MM" or "HH:MM:SS"
    end_time: str
    duration_min: int = Field(ge=1, le=480)
    am_pm: AmPmV2


class V2CourseSummary(BaseModel):
    """Before/After で表示する 1 コース (=1 曜日 × 1 スタッフ枠) のサマリ.

    Note: weekday は V2WeekdayBeforeAfter が保持するので重複を避けて削除.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    office_id: uuid.UUID
    assigned_staff_id: uuid.UUID | None = None
    visits: list[V2VisitForUI] = Field(default_factory=list)
    distance_km: float = 0.0
    visits_count: int = 0


class V2CourseContainer(BaseModel):
    """``V2WeekdayBeforeAfter.before/after`` の型付きコンテナ.

    W41 v2 final cross-review (M-Codex-2): 旧実装は ``dict`` (untyped) だったため、
    Pydantic が shape を検証できず、UI 側で ``courses`` キーの有無が常時 zod 検証任せだった.
    型付きにして API レスポンス段階で検証する.
    """

    model_config = ConfigDict(extra="forbid")

    courses: list[V2CourseSummary] = Field(default_factory=list)


class V2WeekdayBeforeAfter(BaseModel):
    """機能 B (full-optimize) で 1 曜日の Before/After を並べた構造."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    before: V2CourseContainer = Field(default_factory=V2CourseContainer)
    after: V2CourseContainer = Field(default_factory=V2CourseContainer)


class V2KpiOverall(BaseModel):
    """全週まとめての KPI 比較値 (機能 A/B 共通)."""

    model_config = ConfigDict(extra="forbid")

    total_distance_km_before: float = 0.0
    total_distance_km_after: float = 0.0
    distance_reduction_pct: float = 0.0
    courses_count_before: int = 0
    courses_count_after: int = 0
    capacity_overflows: int = 0
    h_violations: dict[str, int] = Field(default_factory=dict)


class V2ProposalDelta(BaseModel):
    """採用前後の差分サマリ (UI ポップアップで表示)."""

    model_config = ConfigDict(extra="forbid")

    distance_km: float = 0.0
    capacity: str | None = Field(default=None, description="例 '4→5'")
    course_visits_count_before: int = 0
    course_visits_count_after: int = 0


class V2BeforeAfterSummary(BaseModel):
    """1 患者の採用前後サマリ (機能 A の各 proposal で使用)."""

    model_config = ConfigDict(extra="forbid")

    course_visits_count: int = 0
    distance_km: float = 0.0


# ---------------------------------------------------------------------------
# 1) /diff-add (機能 A)
# ---------------------------------------------------------------------------


class AutoScheduleV2DiffAddRequest(BaseModel):
    """``POST /api/v1/schedule/v2/diff-add`` request."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class V2DiffAddProposal(BaseModel):
    """機能 A: プール患者 1 件の提案候補."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    suggested: V2VisitPlan
    suggested_visits: list[V2VisitPlan] = Field(
        default_factory=list,
        description="同一患者が複数曜日に展開される場合の各 visit. suggested は代表 1 件.",
    )
    before_summary: V2BeforeAfterSummary = Field(default_factory=V2BeforeAfterSummary)
    after_summary: V2BeforeAfterSummary = Field(default_factory=V2BeforeAfterSummary)
    delta: V2ProposalDelta = Field(default_factory=V2ProposalDelta)
    warnings: list[str] = Field(default_factory=list)


class AutoScheduleV2DiffAddResponse(BaseModel):
    """``POST /api/v1/schedule/v2/diff-add`` response."""

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    proposals: list[V2DiffAddProposal] = Field(default_factory=list)
    kpi_overall: V2KpiOverall = Field(default_factory=V2KpiOverall)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2) /full-optimize (機能 B)
# ---------------------------------------------------------------------------


class AutoScheduleV2FullOptimizeRequest(BaseModel):
    """``POST /api/v1/schedule/v2/full-optimize`` request."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class V2IndividualProposal(BaseModel):
    """機能 B: 1 患者の Before/After 差分 (個別ポップアップ用)."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    current_pfv: list[V2VisitPlan] = Field(default_factory=list)
    proposed_pfv: list[V2VisitPlan] = Field(default_factory=list)
    delta: V2ProposalDelta = Field(default_factory=V2ProposalDelta)
    warnings: list[str] = Field(default_factory=list)


class AutoScheduleV2FullOptimizeResponse(BaseModel):
    """``POST /api/v1/schedule/v2/full-optimize`` response."""

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    week_proposals: list[V2WeekdayBeforeAfter] = Field(default_factory=list)
    individual_proposals: list[V2IndividualProposal] = Field(default_factory=list)
    kpi_overall: V2KpiOverall = Field(default_factory=V2KpiOverall)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3) /apply-individual (機能 A/B 共通; 1 件採用)
# ---------------------------------------------------------------------------


class AutoScheduleV2ApplyIndividualRequest(BaseModel):
    """``POST /api/v1/schedule/v2/apply-individual`` request.

    proposal_id (差分追加モード) または (proposal_batch_id + patient_id) の
    どちらかを指定する. confirm=true 必須.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID | None = None
    proposal_batch_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    confirm: bool = Field(default=False, description="必ず true を指定すること")
    iso_year: int | None = Field(default=None, ge=2020, le=2100)
    iso_week: int | None = Field(default=None, ge=1, le=53)
    # クライアントが提案内容を直接送る経路 (proposal_id ベースだと
    # in-memory cache が必要だが、stateless 設計のため visit_plans を送る).
    visit_plans: list[V2VisitPlan] = Field(
        default_factory=list,
        description=(
            "採用する visit 配置. ``patient_id`` を持つ全 visit を本リストで上書き. "
            "weekday × start_time が同じ既存 PFV は更新, 違うものは追加, 古いものは削除."
        ),
    )


class AutoScheduleV2ApplyIndividualResponse(BaseModel):
    """``POST /api/v1/schedule/v2/apply-individual`` response."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    applied: bool
    fixed_visit_ids: list[uuid.UUID] = Field(default_factory=list)
    # 既に同等の固定枠が存在し、no-op だった場合は ``idempotent=true`` を返す.
    idempotent: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4) /reset-to-fixed (機能 D)
# ---------------------------------------------------------------------------


class AutoScheduleV2ResetToFixedRequest(BaseModel):
    """``POST /api/v1/schedule/v2/reset-to-fixed`` request.

    W41 v2 final cross-review (M-Codex-1): ``confirm=True`` を必須化し、UI 側で
    確認ダイアログを経由せずに直接 API 叩く誤操作を防ぐ.
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    confirm: Literal[True] = Field(
        default=True,
        description="必ず true を指定すること (UI 側で確認ダイアログ後に True 固定送信)",
    )


class AutoScheduleV2ResetToFixedResponse(BaseModel):
    """``POST /api/v1/schedule/v2/reset-to-fixed`` response."""

    model_config = ConfigDict(extra="forbid")

    visits_regenerated: int = Field(ge=0)
    visits_soft_deleted: int = Field(ge=0)
    courses_used: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5) /apply-week-only (この週だけ反映)
#
# 全面最適化の結果を **その週の visits だけ** に反映する慎重モード.
# patient_fixed_visits は更新しないので、来週からは元の固定枠ベースに戻る.
# ---------------------------------------------------------------------------


class V2PatientVisitPlans(BaseModel):
    """``apply-week-only`` で 1 患者分の採用 visit 計画."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    visit_plans: list[V2VisitPlan] = Field(default_factory=list)


class AutoScheduleV2ApplyWeekOnlyRequest(BaseModel):
    """``POST /api/v1/schedule/v2/apply-week-only`` request.

    全面最適化の結果を visits のみに反映する.
    patient_fixed_visits は更新しない (来週からは元の固定枠に戻る).
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    visit_plans_per_patient: list[V2PatientVisitPlans] = Field(default_factory=list)
    confirm: Literal[True] = Field(
        default=True,
        description="必ず true を指定すること (UI 側で確認ダイアログ後に True 固定送信)",
    )


class AutoScheduleV2ApplyWeekOnlyResponse(BaseModel):
    """``POST /api/v1/schedule/v2/apply-week-only`` response."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    visits_created: int = Field(ge=0)
    visits_soft_deleted: int = Field(ge=0)
    courses_created: int = Field(ge=0)
    visit_staff_assignments_created: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "AmPmV2",
    "AutoScheduleV2ApplyIndividualRequest",
    "AutoScheduleV2ApplyIndividualResponse",
    "AutoScheduleV2ApplyWeekOnlyRequest",
    "AutoScheduleV2ApplyWeekOnlyResponse",
    "AutoScheduleV2DiffAddRequest",
    "AutoScheduleV2DiffAddResponse",
    "AutoScheduleV2FullOptimizeRequest",
    "AutoScheduleV2FullOptimizeResponse",
    "AutoScheduleV2ResetToFixedRequest",
    "AutoScheduleV2ResetToFixedResponse",
    "V2BeforeAfterSummary",
    "V2CourseContainer",
    "V2CourseSummary",
    "V2DiffAddProposal",
    "V2IndividualProposal",
    "V2KpiOverall",
    "V2PatientVisitPlans",
    "V2ProposalDelta",
    "V2VisitForUI",
    "V2VisitPlan",
    "V2WeekdayBeforeAfter",
]
