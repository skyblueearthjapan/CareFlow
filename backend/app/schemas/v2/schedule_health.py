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


class CourseDetailTransition(BaseModel):
    """コース内の連続する 2 訪問間の移動 1 件 (健康診断ドリルダウン H1).

    同住所 (<=100m) は travel 0/0、座標欠損を含む遷移は travel 0 (健康診断と同一規約)。
    """

    model_config = ConfigDict(extra="forbid")

    from_patient_id: UUID
    from_patient_name: str
    from_end_time: str = Field(..., description="前の訪問の終了 HH:MM")
    to_patient_id: UUID
    to_patient_name: str
    to_start_time: str = Field(..., description="次の訪問の開始 HH:MM")
    travel_minutes: int = Field(..., ge=0)
    travel_km: float = Field(..., ge=0.0, description="小数1桁")


class CourseDetailPatientCost(BaseModel):
    """患者 1 名の配置コスト = 厳密限界コスト (その患者を抜くと浮く travel+buffer)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str
    start_time: str = Field(..., description="HH:MM")
    marginal_minutes: int = Field(..., description="配置コスト (分/週相当。移動+バッファー)")
    marginal_km: float = Field(..., description="配置コスト (km。小数1桁)")


class CourseDetailTotals(BaseModel):
    """ドリルダウン対象コース (曜日単位) の移動合計 (割合計算の分母)."""

    model_config = ConfigDict(extra="forbid")

    travel_minutes: int = Field(..., ge=0)
    travel_km: float = Field(..., ge=0.0)


class CourseDetailWeekday(BaseModel):
    """対象コースの 1 曜日ぶんの原因内訳."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    course_label: str = Field(..., description="拠点短縮 + コード (例: 稲B)")
    staff_name: str | None = None
    totals: CourseDetailTotals
    transitions: list[CourseDetailTransition] = Field(default_factory=list)
    patient_costs: list[CourseDetailPatientCost] = Field(
        default_factory=list, description="配置コスト降順 (座標欠損の患者は対象外)"
    )


class ScheduleHealthCourseDetailResponse(BaseModel):
    """``GET /v2/schedule-health/course-detail`` レスポンス (H1: 原因ドリルダウン)."""

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    course_code: str
    weekdays: list[CourseDetailWeekday] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# 見直しどきトレンド (schedule-advisor §3 Phase 3「見直しどき通知」)
#   GET /api/v1/schedule/v2/schedule-health/trend
#   指定週から遡って weeks 週分、各週の office 横断合計のみを古→新順で返す。
#   劣化判定は FE 側 (BE は素データのみ返す)。
# ---------------------------------------------------------------------------


class ScheduleHealthTrendTotals(BaseModel):
    """トレンド 1 週の office 横断合計 (4 指標のみ)."""

    model_config = ConfigDict(extra="forbid")

    visit_count: int = Field(..., ge=0, description="訪問件数の合計 (全拠点)")
    travel_minutes: int = Field(..., ge=0, description="移動時間 (分) の合計 (全拠点)")
    travel_km: float = Field(..., ge=0.0, description="移動距離 (km) の合計 (全拠点, 小数1桁)")
    gap_minutes: int = Field(..., ge=0, description="移動しても余る待ち時間 (分) の合計 (全拠点)")


class ScheduleHealthTrendWeek(BaseModel):
    """トレンドの 1 週分 (ISO 週 + office 横断合計)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    totals: ScheduleHealthTrendTotals


class ScheduleHealthTrendResponse(BaseModel):
    """``GET /api/v1/schedule/v2/schedule-health/trend`` レスポンス.

    ``weeks`` は古→新順 (最古週が先頭, 指定週が末尾)。visit ゼロの週も
    totals 全 0 で含める (欠測が見えるように)。
    """

    model_config = ConfigDict(extra="forbid")

    weeks: list[ScheduleHealthTrendWeek] = Field(default_factory=list)


__all__ = [
    "ScheduleHealthCourse",
    "ScheduleHealthOffice",
    "ScheduleHealthResponse",
    "ScheduleHealthTotals",
    "ScheduleHealthTrendResponse",
    "ScheduleHealthTrendTotals",
    "ScheduleHealthTrendWeek",
    "ScheduleHealthWeekday",
]
