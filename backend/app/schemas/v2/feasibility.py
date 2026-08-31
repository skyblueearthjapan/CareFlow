"""``GET /schedule/v2/feasibility-report`` のレスポンス (実現性チェック)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FeasibilityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff: str
    day: str  # YYYY-MM-DD
    kind: str
    severity: str  # hard | soft | info
    at: str  # HH:MM ('' = 日単位の指摘)
    to: str
    from_: str = Field(alias="from")
    gap_min: int | None = None
    need_min: int | None = None
    km: float | None = None


class FeasibilityAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    travel_speed_kmh: float
    visit_buffer_min: int
    lunch_duration_min: int
    lunch_window: str
    road_factor: float
    same_address_pair_min_occupancy: int


class FeasibilityReportRead(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    iso_year: int
    iso_week: int
    week_start: str
    week_end: str
    generated_at: str
    visit_count: int
    event_count: int
    hard_count: int
    soft_count: int
    summary: dict[str, int]
    assumptions: FeasibilityAssumptions
    findings: list[FeasibilityFinding]
    # 印刷用の自己完結 HTML (FE が新しいタブに書き出す)。
    html: str | None = None
