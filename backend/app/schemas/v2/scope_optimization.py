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
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.improvement_suggestion import ImprovementSuggestion

Weekday = Annotated[int, Field(ge=0, le=6, description="0=Mon..6=Sun")]


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


class ScopeOptimizationStep(BaseModel):
    """手順 1 件 (move または swap)。

    ``suggestion`` は改善提案と同一契約 (kind / current / candidate / delta /
    changes / staff_warnings / within_preference / swap_counterpart)。
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


class ScopeOptimizationApplyResponse(BaseModel):
    """``POST /v2/scope-optimization/apply`` レスポンス (all-or-nothing / 1 TX)."""

    model_config = ConfigDict(extra="forbid")

    applied_count: int = Field(..., ge=0, description="適用した手順数 (= len(steps))")
    warnings: list[str] = Field(
        default_factory=list,
        description="N-4 再検証で検出した非致命の警告 (現場向け日本語). ブロックしない.",
    )


__all__ = [
    "ScopeOptimizationApplyRequest",
    "ScopeOptimizationApplyResponse",
    "ScopeOptimizationExcludedSummary",
    "ScopeOptimizationMetrics",
    "ScopeOptimizationScope",
    "ScopeOptimizationSimulateRequest",
    "ScopeOptimizationSimulateResponse",
    "ScopeOptimizationStep",
]
