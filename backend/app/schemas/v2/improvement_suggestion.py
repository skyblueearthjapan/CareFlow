"""改善提案 API (P2-B) Pydantic v2 schemas.

エンドポイント:
  - GET  /api/v1/schedule/v2/improvement-suggestions  (admin/manager, read-only)
  - POST /api/v1/schedule/v2/improvement-suggestions/dismiss (admin/manager, 書込)

設計書: docs/plans/p2-improvement-mvp-design.md §2.3.

方針:
  - 実現可能性 / 限界コスト判定は純ロジック層
    (``app.services.scheduling.improvement_engine``) が担う. 本 schema は
    入出力の形のみを規定する.
  - ``time`` は HH:MM 文字列でやり取りする (propose_slots と同一作法).
  - 全モデル ``extra="forbid"`` (既存 v2 作法).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.propose_slots import WeekdayCode

# 提案種別 (suggestion_dismissals.kind と同一値域).
ImprovementKind = Literal["time_change", "day_change"]

# 却下理由 (suggestion_dismissals.reason と同一値域).
ImprovementDismissReason = Literal[
    "day_immovable", "time_immovable", "staff_relation", "other"
]


# ---------------------------------------------------------------------------
# GET レスポンス sub-models
# ---------------------------------------------------------------------------


class ImprovementDelta(BaseModel):
    """限界コスト方式による効果差分 (現在枠 − 候補枠)."""

    model_config = ConfigDict(extra="forbid")

    travel_minutes_saved: int = Field(
        ..., description="週あたり短縮できる移動 + バッファー (分). 正 = 改善."
    )
    travel_km_saved: float = Field(
        ..., description="週あたり短縮できる直線距離 (km). 正 = 改善."
    )


class ImprovementCurrentSlot(BaseModel):
    """対象患者の現在の固定枠 (PFV ベース)."""

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    weekday_code: WeekdayCode
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    course_label: str = Field(..., description="拠点名 + コード (例: 稲A)")
    staff_name: str | None = None


class ImprovementCandidateSlot(BaseModel):
    """提案する移動先の枠."""

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    office_name: str | None = None
    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    weekday_code: WeekdayCode
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    course_code: str = Field(..., description="A..E / M..M9")
    course_label: str = Field(..., description="拠点名 + コード (例: 稲A)")
    staff_name: str | None = None


class ImprovementChanges(BaseModel):
    """変わるもの / 変わらないものの日本語差分."""

    model_config = ConfigDict(extra="forbid")

    changes: list[str] = Field(default_factory=list, description="変わる項目 (日本語)")
    unchanged: list[str] = Field(default_factory=list, description="変わらない項目 (日本語)")


class ImprovementSuggestion(BaseModel):
    """改善提案 1 件."""

    model_config = ConfigDict(extra="forbid")

    kind: ImprovementKind
    target_weekday: int = Field(
        ..., ge=0, le=6, description="却下指紋の対象曜日 (= 現在枠の曜日)"
    )
    current: ImprovementCurrentSlot
    candidate: ImprovementCandidateSlot
    delta: ImprovementDelta
    changes: ImprovementChanges
    staff_warnings: list[str] = Field(
        default_factory=list,
        description="候補コースの割付スタッフ実態警告 (P0-1 の 3 コード再利用)",
    )
    feasibility_basis: str = Field(
        default="pfv", description="実現可能性判定のベース (PFV = 恒久パターン)"
    )
    requires_patient_confirmation: bool = Field(
        default=False,
        description="movability=unknown の時刻提案に付く要確認ラベル",
    )


class ImprovementFilteredSummary(BaseModel):
    """黙って消さない (N-6): 提案を出さなかった内訳件数.

    各カウンタの単位を明記する. カテゴリ間は重複しない (1 PFV / 候補は 1 カテゴリのみ):
    - ``pinned`` / ``locked`` / ``no_current_visit`` は **PFV 単位** (固定枠 1 件 = 1 カウント).
    - ``dismissed`` は **却下指紋 (kind × 1 PFV) 単位** (time_change と day_change は別計上).
    - ``below_threshold`` / ``day_restricted`` は **候補スロット単位**.
    """

    model_config = ConfigDict(extra="forbid")

    pinned: int = Field(
        default=0,
        ge=0,
        description="is_pinned で除外した PFV 数 (PFV 単位). is_pinned=true ⇒ 自動化保護.",
    )
    locked: int = Field(
        default=0,
        ge=0,
        description="movability='locked' で除外した PFV 数 (PFV 単位). 完全固定.",
    )
    no_current_visit: int = Field(
        default=0,
        ge=0,
        description=(
            "当週 visit が未展開のため評価できなかった PFV 数 (PFV 単位). "
            "次週以降の展開後に再度取得すれば評価対象になる."
        ),
    )
    dismissed: int = Field(
        default=0,
        ge=0,
        description=(
            "却下指紋一致で抑制した候補数 (指紋単位 = kind × PFV 1 件). "
            "time_change と day_change は別指紋."
        ),
    )
    below_threshold: int = Field(
        default=0,
        ge=0,
        description=(
            "効果が閾値未満で除外した候補スロット数 (候補単位). "
            "閾値は IMPROVEMENT_THRESHOLD_MIN (10 分)."
        ),
    )
    day_restricted: int = Field(
        default=0,
        ge=0,
        description=(
            "曜日変更による改善候補があったが movability が同曜日限定のため抑制した "
            "候補スロット数 (候補単位). day_flexible に昇格すれば対象になる."
        ),
    )


class ImprovementSuggestionsResponse(BaseModel):
    """``GET /v2/improvement-suggestions`` レスポンス."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    iso_year: int
    iso_week: int
    suggestions: list[ImprovementSuggestion] = Field(default_factory=list)
    filtered_summary: ImprovementFilteredSummary = Field(
        default_factory=ImprovementFilteredSummary
    )


# ---------------------------------------------------------------------------
# POST dismiss
# ---------------------------------------------------------------------------


class ImprovementDismissRequest(BaseModel):
    """``POST /v2/improvement-suggestions/dismiss`` リクエスト."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    kind: ImprovementKind
    target_weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    reason: ImprovementDismissReason
    reason_note: str | None = Field(default=None, description="自由記述 (任意)")
    promote_movability: bool = Field(
        default=False,
        description=(
            "true で該当 PFV の movability を昇格 "
            "(day_immovable→time_flexible / time_immovable→locked). "
            "is_pinned=true の行は locked のまま."
        ),
    )


class ImprovementDismissResponse(BaseModel):
    """``POST /v2/improvement-suggestions/dismiss`` レスポンス."""

    model_config = ConfigDict(extra="forbid")

    dismissal_id: uuid.UUID
    movability_updated: bool = Field(
        default=False, description="movability を実際に更新したか"
    )
    new_movability: str | None = Field(
        default=None, description="更新後の movability (更新しなければ null)"
    )


__all__ = [
    "ImprovementCandidateSlot",
    "ImprovementChanges",
    "ImprovementCurrentSlot",
    "ImprovementDelta",
    "ImprovementDismissReason",
    "ImprovementDismissRequest",
    "ImprovementDismissResponse",
    "ImprovementFilteredSummary",
    "ImprovementKind",
    "ImprovementSuggestion",
    "ImprovementSuggestionsResponse",
]
