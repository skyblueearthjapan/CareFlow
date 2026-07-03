"""範囲最適化 API (scope-optimization W1 BE-2) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/scope-optimization/simulate (admin/manager, read-only)
  - (W2 予定) POST /api/v1/schedule/v2/scope-optimization/apply

設計書: docs/plans/scope-optimization-design.md §4.

方針:
  - 手順 1 件の中身は既存 ``ImprovementSuggestion`` を再利用する (FE は
    ImprovementSuggestionCard の表現をそのまま流用できる)。
  - ``time`` は HH:MM 文字列でやり取りする (propose_slots と同一作法)。
  - 全モデル ``extra="forbid"`` (既存 v2 作法)。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.improvement_suggestion import (
    CourseSnapshot,
    CourseSnapshotVisit,
    ImprovementSuggestion,
)

Weekday = Annotated[int, Field(ge=0, le=6, description="0=Mon..6=Sun")]

# UI 統一: スナップショットの正典は improvement_suggestion.py (改善提案と共有)。
# 旧名は互換エイリアス (schedule_v2 の import / __all__ を壊さない)。
ScopeSnapshotVisit = CourseSnapshotVisit
ScopeCourseSnapshot = CourseSnapshot


class ScopeOptimizationScope(BaseModel):
    """最適化の対象範囲。移動元も移動先もこの範囲内に限る (設計書 §2).

    4 区分の表現例:
      ①1コース  = ``{weekdays: [0], course_codes: ["A"]}``
      ②1曜日    = ``{weekdays: [1]}``
      ③複数曜日 = ``{weekdays: [0, 1]}``
      ④拠点全体 = 両方 null
    """

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    weekdays: list[Weekday] | None = Field(default=None, description="対象曜日 (null = 全曜日)")
    course_codes: list[str] | None = Field(
        default=None, description="対象コース code (null = 全コース)"
    )


class ScopeOptimizationSimulateRequest(BaseModel):
    """``POST /v2/scope-optimization/simulate`` リクエスト."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    scope: ScopeOptimizationScope
    search_scope: ScopeOptimizationScope | None = Field(
        default=None,
        description=(
            "§10 フォーカス最適化: 移動先・入れ替え相手を探す範囲。scope (フォーカス) を"
            "包含すること (同一拠点・曜日/コースは上位集合)。None = フォーカスと同じ "
            "(従来挙動・後方互換)。"
        ),
    )


class ScopeOptimizationStep(BaseModel):
    """手順 1 件 (move または swap)。

    ``suggestion`` は改善提案と同一契約 (kind / current / candidate / delta /
    changes / staff_warnings / within_preference / swap_counterpart)。
    ``source_course`` / ``destination_course`` は適用前のコーススナップショット
    (同一コース内の手は destination_course=null。後方互換: 既定 null)。
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(..., ge=1, description="手順番号 (1 始まり連番)")
    patient_id: uuid.UUID
    patient_name: str
    suggestion: ImprovementSuggestion
    cumulative_delta_minutes: int = Field(
        ..., description="手順 1..seq まで適用したときの累積短縮 (分/週)"
    )
    cumulative_delta_km: float = Field(
        ..., description="手順 1..seq まで適用したときの累積短縮 (km/週)"
    )
    source_course: ScopeCourseSnapshot | None = Field(
        default=None, description="移動元コースの適用前スナップショット"
    )
    destination_course: ScopeCourseSnapshot | None = Field(
        default=None,
        description="移動先コースの適用前スナップショット (同一コース内は null)",
    )


class ScopeOptimizationMetrics(BaseModel):
    """scope 内合計の健康診断メトリクス (schedule_health と同一物差し)."""

    model_config = ConfigDict(extra="forbid")

    visit_count: int = Field(default=0, ge=0)
    travel_minutes: int = Field(default=0, ge=0, description="移動時間合計 (分)")
    travel_km: float = Field(default=0.0, ge=0, description="移動距離合計 (km, 小数1桁)")
    buffer_minutes: int = Field(default=0, ge=0, description="バッファー合計 (分)")
    gap_minutes: int = Field(default=0, ge=0, description="隙間 (待ち時間) 合計 (分)")


class ScopeOptimizationExcludedSummary(BaseModel):
    """黙って消さない (N-6): 動かさなかった / 数えなかった内訳.

    粒度:
    - ``pinned`` / ``locked`` / ``no_current_visit`` は「scope 内に配置がある
      patient × weekday」単位。
    - ``dismissed`` / ``confirmation_required_excluded`` は初回イテレーションの
      kind × PFV 単位 (improvement_suggestions の filtered_summary と同粒度)。
    """

    model_config = ConfigDict(extra="forbid")

    pinned: int = Field(default=0, ge=0, description="ピン留めで除外した枠数")
    locked: int = Field(default=0, ge=0, description="可動域=完全固定で除外した枠数")
    no_current_visit: int = Field(
        default=0, ge=0, description="PFV 対応が取れず評価不能だった訪問数"
    )
    dismissed: int = Field(default=0, ge=0, description="却下記憶で抑制した指紋数")
    confirmation_required_excluded: int = Field(
        default=0,
        ge=0,
        description=(
            "患者確認が必要になるため既定モードでは生成しなかった指紋数 "
            "(希望外かつ movability が確認不要で許さない手)"
        ),
    )
    truncated: bool = Field(
        default=False,
        description="手順数が上限 (SCOPE_MAX_STEPS) に達し打ち切ったか",
    )


class ScopeOptimizationCourseBeforeAfter(BaseModel):
    """コース別の実行後見通し (H2: 「稲Bが 92分→51分 になる」).

    見通しは恒久パターン (PFV) 基準のシミュレーション値 (設計 D-1)。
    """

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    weekday: Weekday
    course_code: str
    course_label: str
    staff_name: str | None = None
    before: ScopeOptimizationMetrics
    after: ScopeOptimizationMetrics


class ScopeOptimizationSimulateResponse(BaseModel):
    """``POST /v2/scope-optimization/simulate`` レスポンス (read-only).

    0 手でも 200 (before=after + excluded_summary)。``state_token`` は W2 apply の
    楽観ロック (scope 患者の PFV 集合の指紋。simulate 時点から変化すると apply が 409)。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    office_id: uuid.UUID
    steps: list[ScopeOptimizationStep] = Field(default_factory=list)
    before: ScopeOptimizationMetrics
    after: ScopeOptimizationMetrics
    excluded_summary: ScopeOptimizationExcludedSummary = Field(
        default_factory=ScopeOptimizationExcludedSummary
    )
    state_token: str = Field(..., description="apply 用の楽観ロック指紋 (sha256)")
    courses: list[ScopeOptimizationCourseBeforeAfter] = Field(
        default_factory=list,
        description="H2: コース別の実行後見通し (変化なしコースも含む)",
    )
    focus_before: ScopeOptimizationMetrics | None = Field(
        default=None,
        description="§10: フォーカス範囲のみの合計 (before/after は探索範囲全体)",
    )
    focus_after: ScopeOptimizationMetrics | None = Field(default=None)


class ScopeOptimizationApplyRequest(BaseModel):
    """``POST /v2/scope-optimization/apply`` リクエスト (W2).

    ``steps`` は **simulate 結果の先頭からの連続区間** (seq=1..N) をそのまま送る
    (プレフィックス適用のみ。途中の欠番は依存関係が壊れるため 422)。
    ``state_token`` は simulate レスポンスの値。サーバが再計算して不一致なら 409
    (simulate 以降に scope 患者の固定枠が変わった = 再計算が必要)。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    scope: ScopeOptimizationScope
    state_token: str = Field(..., min_length=1)
    steps: list[ScopeOptimizationStep] = Field(..., min_length=1)
    search_scope: ScopeOptimizationScope | None = Field(
        default=None,
        description=(
            "simulate 時と同じ探索範囲 (state_token の再計算規約を一致させるため必須で"
            "エコーバックする。None = フォーカスと同じ)。"
        ),
    )
    # Wave U-1 (§2.2 反映先の統一): 適用結果をどこへ書くか.
    #   pattern_only      = 型 (PFV) のみ変更 (既定・従来挙動; 後方互換).
    #   pattern_and_week  = PFV 移動後、影響患者の今週 visits を PFV から再生成 (A).
    #   week_only         = PFV は不変、今週の visits にのみ反映 (B; source='manual_week').
    change_scope: Literal["pattern_only", "pattern_and_week", "week_only"] = Field(
        default="pattern_only",
        description=(
            "反映先の統一 (Wave U-1). pattern_only=型のみ(既定) / "
            "pattern_and_week=型+今週(A) / week_only=今週だけ(B)."
        ),
    )


class ScopeOptimizationWeekSync(BaseModel):
    """apply の今週反映サマリ (Wave U-1). ``change_scope`` が pattern_only のとき null."""

    model_config = ConfigDict(extra="forbid")

    patients: int = Field(default=0, ge=0, description="今週へ反映した患者数")
    visits_regenerated: int = Field(
        default=0,
        ge=0,
        description="A: PFV から再生成した visit 数 / B: 移動した visit 数",
    )
    visits_soft_deleted: int = Field(
        default=0, ge=0, description="A: reset で soft-delete した visit 数 (B は 0)"
    )


class ScopeOptimizationApplyResponse(BaseModel):
    """``POST /v2/scope-optimization/apply`` レスポンス (all-or-nothing / 1 TX)."""

    model_config = ConfigDict(extra="forbid")

    applied_count: int = Field(..., ge=0, description="適用した手順数 (= len(steps))")
    warnings: list[str] = Field(
        default_factory=list,
        description="N-4 再検証で検出した非致命の警告 (現場向け日本語). ブロックしない.",
    )
    # Wave U-1: 反映先の統一 (後方互換の追加のみ; 旧 FE は無視して従来動作).
    change_scope: Literal["pattern_only", "pattern_and_week", "week_only"] = Field(
        default="pattern_only", description="適用した反映先 (リクエストのエコーバック)."
    )
    week_sync: ScopeOptimizationWeekSync | None = Field(
        default=None,
        description="今週反映サマリ (A/B のとき付与。pattern_only は null).",
    )


__all__ = [
    "ScopeCourseSnapshot",
    "ScopeOptimizationApplyRequest",
    "ScopeOptimizationCourseBeforeAfter",
    "ScopeOptimizationApplyResponse",
    "ScopeOptimizationExcludedSummary",
    "ScopeOptimizationMetrics",
    "ScopeOptimizationScope",
    "ScopeOptimizationSimulateRequest",
    "ScopeOptimizationSimulateResponse",
    "ScopeOptimizationStep",
    "ScopeOptimizationWeekSync",
    "ScopeSnapshotVisit",
]
