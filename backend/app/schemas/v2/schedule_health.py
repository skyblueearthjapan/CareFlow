"""スケジュール健康診断 (schedule-advisor Phase 1「診る」) v2 schemas.

エンドポイント:
  - GET /api/v1/schedule/v2/schedule-health (admin/manager)

現場の固定スケジュール運用に対し、「移動や隙間の無駄がどれだけあるか」を
週次・拠点別・曜日別・コース別に **数字で** 返す read-only 集計 API のレスポンス型。

設計方針 (docs/plans/schedule-advisor-design.md §3 Phase 1):
  - 効果の物差しは提案エンジンと同一の travel モデル (単一ソース)。距離 / 移動時間 /
    バッファー / 同住所判定は ``auto_allocator_v2`` / ``proposal_solver`` の関数・定数を
    再利用し、``load_scheduling_config`` の speed/buffer 上書きを反映する。
  - DB 書込なし (集計のみ)。空のコース / 曜日は出力しない。``travel_km`` は小数1桁丸め。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScheduleHealthTotals(BaseModel):
    """曜日 / 週の集計小計 (コース横断の合算)."""

    model_config = ConfigDict(extra="forbid")

    visit_count: int = Field(..., ge=0, description="訪問件数の合計")
    service_minutes: int = Field(..., ge=0, description="サービス時間 (分) の合計")
    travel_minutes: int = Field(..., ge=0, description="移動時間 (分) の合計")
    travel_km: float = Field(..., ge=0.0, description="移動距離 (km) の合計 (小数1桁)")
    buffer_minutes: int = Field(..., ge=0, description="訪問間バッファー (分) の合計")
    gap_minutes: int = Field(..., ge=0, description="移動しても余る待ち時間 (分) の合計")


class ScheduleHealthCourse(BaseModel):
    """1 コース (office × weekday × course_code) の健康診断メトリクス."""

    model_config = ConfigDict(extra="forbid")

    course_code: str = Field(..., description="コースコード (例: A)")
    staff_name: str | None = Field(default=None, description="割付スタッフ名 (未割付なら null)")
    visit_count: int = Field(..., ge=0, description="このコースの訪問件数")
    patient_count: int = Field(..., ge=0, description="このコースの distinct 患者数")
    service_minutes: int = Field(..., ge=0, description="サービス時間 (分) の合計")
    travel_minutes: int = Field(..., ge=0, description="移動時間 (分) の合計")
    travel_km: float = Field(..., ge=0.0, description="移動距離 (km) の合計 (小数1桁)")
    buffer_minutes: int = Field(..., ge=0, description="訪問間バッファー (分) の合計")
    gap_minutes: int = Field(..., ge=0, description="移動しても余る待ち時間 (分) の合計")


class ScheduleHealthWeekday(BaseModel):
    """1 拠点 × 1 曜日 (コースのコンテナ + 曜日小計)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    courses: list[ScheduleHealthCourse] = Field(default_factory=list)
    totals: ScheduleHealthTotals


class ScheduleHealthOffice(BaseModel):
    """1 拠点分の健康診断 (曜日別のコンテナ + 週小計)."""

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    office_name: str = Field(..., description="拠点名")
    weekdays: list[ScheduleHealthWeekday] = Field(default_factory=list)
    week_totals: ScheduleHealthTotals


class ScheduleHealthResponse(BaseModel):
    """``GET /api/v1/schedule/v2/schedule-health`` レスポンス."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    offices: list[ScheduleHealthOffice] = Field(default_factory=list)


__all__ = [
    "ScheduleHealthCourse",
    "ScheduleHealthOffice",
    "ScheduleHealthResponse",
    "ScheduleHealthTotals",
    "ScheduleHealthWeekday",
]
