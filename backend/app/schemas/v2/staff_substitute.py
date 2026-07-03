"""P3-① 当日欠勤の代替スタッフ提案 — Pydantic v2 schemas.

エンドポイント (設計書 docs/plans/p3-1-staff-substitute-design.md §1):
  - GET  /api/v1/schedule/staff-substitute/candidates  (admin/manager, read-only)
  - POST /api/v1/schedule/staff-substitute/apply        (admin/manager, all-or-nothing)

「今朝◯◯さんが欠勤 → 今日の担当 visit、誰が代われる？」に答える 3 段
(提案 → 人間が選ぶ → 適用) の入出力契約を規定する. 純ロジックは
``app.services.scheduling.staff_substitute`` が担い、本 schema は形のみ.

時刻は HH:MM (秒なし) 文字列でやり取りする (UI / モバイル都合).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# GET candidates レスポンス
# ---------------------------------------------------------------------------


class SubstituteScoreBreakdown(BaseModel):
    """候補スコアの内訳 (設計書 §3)."""

    model_config = ConfigDict(extra="forbid")

    continuity_bonus: float = Field(..., description="患者直近担当ボーナス (100/50/20/0)")
    travel_penalty: float = Field(..., description="追加移動ペナルティ (負値)")
    load_penalty: float = Field(..., description="当日 visit 件数ペナルティ (負値)")


class SubstituteCandidate(BaseModel):
    """1 visit に対する 1 候補スタッフ (score 降順)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: uuid.UUID
    staff_name: str
    staff_sex: str | None = None
    score: float
    score_breakdown: SubstituteScoreBreakdown


class SubstituteNoCandidateReason(BaseModel):
    """候補ゼロ理由コードと件数 (N-6). 除外ハード制約の集計."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        description=(
            "no_work_day / sex_mismatch / office_mismatch / event_overlap / "
            "time_conflict / trainee_solo"
        ),
    )
    count: int = Field(..., ge=0)


class SubstituteVisitCandidates(BaseModel):
    """欠勤スタッフの当日 planned visit 1 件 + その候補ランキング."""

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    sex_restriction: str | None = Field(
        default=None, description="'female_only' | 'male_only' | None"
    )
    # 2 名体制 (設計書 §1)
    visit_group_id: uuid.UUID | None = None
    required_staff_count: int = Field(..., ge=1, le=2)
    is_secondary: bool = Field(
        default=False, description="欠勤スタッフが当該グループの secondary 側か"
    )
    candidates: list[SubstituteCandidate] = Field(default_factory=list)
    no_candidate_reasons: list[SubstituteNoCandidateReason] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# P5 引き継ぎプラン (設計書 docs/plans/p5-course-substitute-design.md §1-2)
# ---------------------------------------------------------------------------


class SubstitutePlanAssignee(BaseModel):
    """プラン内の 1 受け手スタッフ + 担当ブロック / 件数 / 負荷."""

    model_config = ConfigDict(extra="forbid")

    staff_id: uuid.UUID
    staff_name: str
    staff_sex: str | None = None
    block: Literal["full", "am", "pm"] = Field(
        ..., description="担当ブロック (full=丸ごと / am / pm)"
    )
    visit_ids: list[uuid.UUID] = Field(default_factory=list)
    visit_count: int = Field(..., ge=0)
    added_travel_minutes: float = Field(..., description="担当 visit 群の追加移動 (分, 合計)")
    existing_load: int = Field(..., ge=0, description="当日既存 visit 件数")


class SubstitutePlanException(BaseModel):
    """プランで受け手が担えない visit (個別選択が必要)."""

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    reason: str = Field(..., description="ハード制約理由コード / no_feasible_staff")


class SubstitutePlan(BaseModel):
    """1 コース (または NULL コース) に対する引き継ぎプラン (層 1-4)."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    tier: int = Field(..., ge=1, le=4)
    tier_label: str
    course_id: uuid.UUID | None = None
    course_code: str | None = None
    assignees: list[SubstitutePlanAssignee] = Field(default_factory=list)
    total_visits: int = Field(..., ge=0)
    exception_count: int = Field(..., ge=0)
    exceptions: list[SubstitutePlanException] = Field(default_factory=list)
    score: float
    warnings: list[str] = Field(default_factory=list)


class SubstituteCandidatesResponse(BaseModel):
    """GET candidates レスポンス."""

    model_config = ConfigDict(extra="forbid")

    absent_staff_id: uuid.UUID
    absent_staff_name: str
    target_date: date
    visits: list[SubstituteVisitCandidates] = Field(default_factory=list)
    # P5: 引き継ぎプラン (後方互換: 旧 FE は無視). default [] で既存契約を破らない.
    plans: list[SubstitutePlan] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# POST apply リクエスト / レスポンス
# ---------------------------------------------------------------------------


class SubstituteApplyItem(BaseModel):
    """1 visit の差替え指示."""

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    substitute_staff_id: uuid.UUID


class SubstituteApplyRequest(BaseModel):
    """POST apply リクエスト (all-or-nothing).

    ``absent_staff_id`` / ``target_date`` は N-4 再検証 (適用直前の同一ハード制約
    再実行) と ``register_absence`` (StaffWeeklyOverride off 登録) の実行に必要な
    ため必須とする (設計書 §1 の body に対する最小の明示的拡張).
    """

    model_config = ConfigDict(extra="forbid")

    absent_staff_id: uuid.UUID
    target_date: date
    substitutions: list[SubstituteApplyItem] = Field(..., min_length=1)
    register_absence: bool = Field(
        default=False,
        description="true で欠勤スタッフの当日 off (StaffWeeklyOverride) を同時登録",
    )


class SubstituteApplied(BaseModel):
    """適用済み 1 件 (差替え結果)."""

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    original_staff_id: uuid.UUID
    substitute_staff_id: uuid.UUID
    visit_group_partner_visit_id: uuid.UUID | None = Field(
        default=None, description="2 名体制で同期した partner visit の id"
    )


class SubstituteApplyResponse(BaseModel):
    """POST apply レスポンス."""

    model_config = ConfigDict(extra="forbid")

    applied: list[SubstituteApplied] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "SubstituteApplied",
    "SubstituteApplyItem",
    "SubstituteApplyRequest",
    "SubstituteApplyResponse",
    "SubstituteCandidate",
    "SubstituteCandidatesResponse",
    "SubstituteNoCandidateReason",
    "SubstitutePlan",
    "SubstitutePlanAssignee",
    "SubstitutePlanException",
    "SubstituteScoreBreakdown",
    "SubstituteVisitCandidates",
]
