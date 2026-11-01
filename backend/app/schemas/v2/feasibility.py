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


# --- 取り込み後の確認・カードの印 (設計 schedule-feasibility-alerts-design-2026-11-01) ---


class FeasibilityAlertItem(BaseModel):
    """帯・一覧・取り込み結果に出す指摘 1 件 (赤 = hard / 黄 = soft)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    tier: str  # hard | soft
    kind: str
    day: str  # YYYY-MM-DD
    at: str  # HH:MM ('' = 日単位)
    staff: str
    patient_name: str = Field(alias="patientName")
    reason: str
    visit_ids: list[str] = Field(default_factory=list, alias="visitIds")


class FeasibilityMark(BaseModel):
    """カード 1 枚に付ける印 1 件."""

    model_config = ConfigDict(extra="forbid")

    tier: str
    kind: str
    reason: str


class FeasibilityMarksRead(BaseModel):
    """``GET /schedule/v2/feasibility-marks`` の応答 (訪問 ID ごとの印・一覧・件数)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    date_from: str = Field(alias="dateFrom")
    date_to: str = Field(alias="dateTo")
    hard_count: int = Field(alias="hardCount")
    soft_count: int = Field(alias="softCount")
    # 担当が決まっていない (判定から外した) 訪問の数
    unassigned_count: int = Field(default=0, alias="unassignedCount")
    items: list[FeasibilityAlertItem]
    marks: dict[str, list[FeasibilityMark]]


class ImportFeasibilityRead(BaseModel):
    """カイポケ取り込みで新しく出た赤・黄 (取り込み結果の画面に出す)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ok: bool
    hard_count: int = Field(default=0, alias="hardCount")
    soft_count: int = Field(default=0, alias="softCount")
    unassigned_count: int = Field(default=0, alias="unassignedCount")
    items: list[FeasibilityAlertItem] = Field(default_factory=list)
    # 判定できなかったときの文 (ok=False)
    message: str | None = None
