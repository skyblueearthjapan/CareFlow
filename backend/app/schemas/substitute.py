"""Schemas for ``POST /api/v1/schedule/v2/substitute-candidates`` (急休の代替候補).

正典 = ``docs/plans/week-cockpit-design.md`` §2-1 (Phase E / BE-1 の契約書)。

read-only。「この人がこの日休む」ときに、その日の担当訪問をコース単位で束ね、
各コースに入れる候補スタッフを ◎(ok) / △(warn) / ×(ng) で返す。付替の実行は
既存 API (``PATCH /courses/{id}`` / ``visit-assign-staff-week``) が担う。
"""

from __future__ import annotations

from datetime import date as _Date  # noqa: N812 (project-local alias)
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 判定値 (設計書 §2-1 の規則と 1:1)
# ---------------------------------------------------------------------------
#   ok   (◎) = ハード制約すべて OK かつ 時間重なり無し
#   warn (△) = 時間重なり / イベント重なり / 同行拘束 **のみ**
#   ng   (×) = 休み・非勤務日・NG スタッフ・性別・新人・拠点不可
SubstituteStatus = Literal["ok", "warn", "ng"]

SubstituteReasonCode = Literal[
    "off",  # 当該週の休み申請 (StaffWeeklyOverride override_type='off')
    "ng_staff",  # 患者の NG スタッフ指定
    "gender",  # 患者の性別制限に不適合
    "trainee",  # 新人 (単独で担当は不可・同行で割り当てる)
    "office",  # 実効拠点がコース拠点と違う
    "event_overlap",  # 個別業務 (staff_events) と重なる (±15分バッファ込み)
    "time_overlap",  # 本人の別訪問と時間が重なる
    "not_working_day",  # そもそも勤務曜日ではない
    "accompaniment",  # 同行 (メンター) で拘束されている (2026-08-22 レビュー追補)
]


class SubstituteCandidatesRequest(BaseModel):
    """急休の代替候補リクエスト."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID = Field(description="休む (= 抜ける) スタッフ")
    date: _Date = Field(description="対象日 (YYYY-MM-DD)")
    course_id: UUID | None = Field(
        default=None, description="指定するとそのコースだけに絞る (省略=その日の全担当)"
    )


class SubstituteAbsentStaff(BaseModel):
    """抜けるスタッフ (見出し表示用)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str


class SubstituteVisit(BaseModel):
    """付け替え対象の訪問 1 件."""

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID
    patient_id: UUID
    patient_name: str
    start_time: str = Field(description="HH:MM")
    end_time: str = Field(description="HH:MM")
    week_pinned: bool
    status: str = Field(
        description=(
            "訪問の状態 (planned / in_progress / completed)。"
            "付替の対象になるのは planned だけ — FE の件数表示と青ピン判定も "
            "planned で数えて BE (staff-off-week) の対象集合と一致させる"
        )
    )


class SubstituteReason(BaseModel):
    """候補が ◎ でない理由 1 件 (FE のツールチップ表示用)."""

    model_config = ConfigDict(extra="forbid")

    code: SubstituteReasonCode
    message: str
    visit_id: UUID | None = Field(
        default=None, description="理由の原因になった対象訪問 (該当しなければ null)"
    )


class SubstituteCandidate(BaseModel):
    """1 コースに対する候補スタッフ 1 名."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    name: str
    sex: str | None = None
    office_name: str | None = None
    status: SubstituteStatus
    reasons: list[SubstituteReason] = Field(default_factory=list)
    score: float = Field(description="高いほど推奨 (継続性 + / 負荷 -)")
    load_today: int = Field(description="その日の既存訪問数")


class SubstituteGroup(BaseModel):
    """コース単位の束 (``course_id`` null = 臨時 / 未所属をまとめた束)."""

    model_config = ConfigDict(extra="forbid")

    course_id: UUID | None = None
    course_label: str
    visits: list[SubstituteVisit] = Field(default_factory=list)
    candidates: list[SubstituteCandidate] = Field(default_factory=list)


class SubstituteCandidatesResponse(BaseModel):
    """急休の代替候補レスポンス."""

    model_config = ConfigDict(extra="forbid")

    absent_staff: SubstituteAbsentStaff
    date: _Date
    weekday: int = Field(ge=0, le=6, description="0=月 .. 6=日")
    groups: list[SubstituteGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
