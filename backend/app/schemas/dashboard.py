"""Dashboard aggregation schemas — Phase 7 (W2-D)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DashboardKpiResponse(BaseModel):
    """Today + this-week roll-up for the dashboard KPI cards.

    No `from_attributes=True` because the route hand-builds this from
    aggregate scalars, never from an ORM row.
    """

    today_visits: int = Field(..., ge=0, description="Total visits scheduled today.")
    today_completed: int = Field(..., ge=0, description="Visits with status='completed' today.")
    today_unassigned: int = Field(
        ..., ge=0, description="Visits today with no primary_staff assigned."
    )
    today_overlapping: int = Field(
        ...,
        ge=0,
        description=(
            "Number of visits today that overlap (in time) with another visit "
            "sharing the same primary_staff. Counts each side of the overlap."
        ),
    )
    this_week_visits: int = Field(..., ge=0, description="Total visits this ISO week (Mon–Sun).")
    this_week_completion_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="completed / total for the current ISO week (0.0 when no visits).",
    )


class DashboardTrendItem(BaseModel):
    """One day's bucket in a trend series."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    total: int = Field(..., ge=0)
    completed: int = Field(..., ge=0)
    unassigned: int = Field(..., ge=0)


class DashboardTrendResponse(BaseModel):
    """`days`-long trend series (oldest → newest)."""

    model_config = ConfigDict(from_attributes=True)

    items: list[DashboardTrendItem]
    days: int = Field(..., ge=1, le=90)
    start_date: date
    end_date: date


# ---- スタッフ別の実績 (docs/plans/dashboard-staff-performance-design-2026-09-30.md) ----


class PerformanceMetrics(BaseModel):
    """1 人 (またはチーム平均) の、ある期間 (全体 or 1 週) の数字。

    訪問が無い期間は ``days == 0`` で、割り算の数字は null。
    チーム平均 (``team``) では ``days`` / ``visits`` / ``patients`` / ``km_total`` /
    ``actual_samples`` / ``arrival_only`` / ``skipped_legs`` / ``meeting_min_total`` は
    **合計**、それ以外は訪問のあった人の平均 (``actual_min`` だけは全員の実績を合わせた平均)。
    """

    days: int = Field(..., ge=0, description="出勤日数 = 訪問が 1 件以上あった日数")
    visits: int = Field(..., ge=0)
    patients: int = Field(..., ge=0, description="利用者の実人数")
    per_day: float | None = Field(None, description="1 日あたり件数")
    plan_min: float | None = Field(None, description="1 回あたりの予定の長さ (分)")
    actual_min: float | None = Field(
        None, description="1 回あたりの実績 (分)。到着・退出が揃った訪問が 5 件以上のときだけ"
    )
    actual_samples: int = Field(..., ge=0, description="到着・退出が揃った訪問の件数")
    arrival_only: int = Field(..., ge=0, description="到着だけ記録がある訪問の件数")
    qr_ratio: float | None = Field(None, description="actual_samples / visits")
    km_total: float = Field(..., ge=0)
    km_per_day: float | None = None
    km_per_visit: float | None = None
    skipped_legs: int = Field(..., ge=0, description="座標が無く距離に入れなかった区間の数")
    visit_min_per_day: float | None = Field(None, description="1 日の内訳: 訪問 (予定の合計)")
    travel_min_per_day: float | None = Field(None, description="1 日の内訳: 移動")
    meeting_min_per_day: float | None = Field(None, description="1 日の内訳: 会議・研修など")
    idle_min_per_day: float | None = Field(None, description="1 日の内訳: 訪問の合間")
    meeting_min_total: int = Field(..., ge=0)
    no_show_count: int = Field(
        0, ge=0, description="未訪問 (no_show) の記録がある訪問の件数 (件数には数えたまま)"
    )
    staff_count: int | None = Field(None, description="チーム平均のときだけ: 訪問のあった人数")


class PerformanceWeek(BaseModel):
    """週 (月〜日) の区切り。期間の端は期間に合わせて切る。"""

    start: date
    end: date


class PerformanceOffice(BaseModel):
    id: UUID
    name: str
    short_label: str


class StaffPerformanceRow(BaseModel):
    staff_id: UUID
    name: str
    office_id: UUID | None
    office_short: str | None
    is_manager: bool
    is_trainee: bool
    qualification: str | None
    period: PerformanceMetrics
    weeks: list[PerformanceMetrics]


class StaffPerformanceTeam(BaseModel):
    period: PerformanceMetrics
    weeks: list[PerformanceMetrics]


class StaffPerformanceResponse(BaseModel):
    date_from: date
    date_to: date
    office_id: UUID | None
    travel_speed_kmh: float
    min_actual_samples: int
    weeks: list[PerformanceWeek]
    offices: list[PerformanceOffice]
    team: StaffPerformanceTeam
    staff: list[StaffPerformanceRow]
