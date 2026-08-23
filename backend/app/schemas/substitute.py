"""Schemas for ``POST /api/v1/schedule/v2/substitute-candidates`` (急休の代替候補)
および ``POST /api/v1/schedule/v2/assign-candidates`` (担当なしへの投入提案).

正典 = ``docs/plans/week-cockpit-design.md`` §2-1 (Phase E / BE-1 の契約書) と
``docs/plans/unassigned-suggestions-design.md`` §2 (Phase 2-A の契約書)。

read-only。「この人がこの日休む」ときに、その日の担当訪問をコース単位で束ね、
各コースに入れる候補スタッフを ◎(ok) / △(warn) / ×(ng) で返す。付替の実行は
既存 API (``PATCH /courses/{id}`` / ``visit-assign-staff-week``) が担う。
``assign-candidates`` は同じレスポンス形で「抜ける人は居ない」版 (対象訪問を
コース or 訪問 ID で直接指定する) を返す。
"""

from __future__ import annotations

from datetime import date as _Date  # noqa: N812 (project-local alias)
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class AssignCandidatesRequest(BaseModel):
    """「担当なし」への投入提案リクエスト (抜けるスタッフは居ない).

    ``course_id`` / ``course_ids`` / ``visit_ids`` は **いずれか 1 つだけ** 必須
    (複数指定 / 全部なしは 422)。``course_ids`` は「担当なし行のコースをまとめて
    見積もる」用で、重い前処理 (稼働スタッフ / 当日訪問 / 継続性) を **1 回だけ**
    走らせてコース分の結果をまとめて返す。
    """

    model_config = ConfigDict(extra="forbid")

    date: _Date = Field(description="対象日 (YYYY-MM-DD)")
    course_id: UUID | None = Field(
        default=None, description="その日のこのコースの planned 訪問すべてを対象にする"
    )
    course_ids: list[UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="複数コースを 1 回の呼び出しでまとめて評価する (最大 50 コース)",
    )
    visit_ids: list[UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="対象訪問を直接指定する (その日の planned のみ・最大 200 件)",
    )

    @model_validator(mode="after")
    def _exactly_one_target(self) -> AssignCandidatesRequest:
        given = [
            name
            for name, value in (
                ("course_id", self.course_id),
                ("course_ids", self.course_ids),
                ("visit_ids", self.visit_ids),
            )
            if value is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "course_id / course_ids / visit_ids はいずれか 1 つだけを指定してください"
                f"（指定: {', '.join(given) or 'なし'}）"
            )
        return self


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
    """代替候補 / 投入提案のレスポンス (2 本の API で共有)."""

    model_config = ConfigDict(extra="forbid")

    absent_staff: SubstituteAbsentStaff | None = Field(
        default=None,
        description=(
            "抜けるスタッフ (substitute-candidates)。assign-candidates は抜ける人が居ないので null"
        ),
    )
    date: _Date
    weekday: int = Field(ge=0, le=6, description="0=月 .. 6=日")
    groups: list[SubstituteGroup] = Field(default_factory=list)
    whole_ok_staff_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "全対象訪問 (= 全 group) で status=ok だったスタッフの交差。"
            "対象訪問どうしが時間的にぶつかる場合 (1 人では回れない) は空。"
            "束を丸ごと引き受けられる人。並びは score 合計の降順"
        ),
    )
    whole_ok_by_course: dict[UUID, list[UUID]] = Field(
        default_factory=dict,
        description=(
            "コースごとの「そのコースを丸ごと引き受けられる人」。"
            "course_id → そのコースの全訪問で status=ok だったスタッフ (score 降順)。"
            "コース内の訪問どうしがぶつかる場合は空配列。"
            "course_id を持たない束 (臨時・未所属) は含まれない"
        ),
    )
    warnings: list[str] = Field(default_factory=list)
