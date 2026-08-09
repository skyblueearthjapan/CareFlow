"""受け入れ枠マトリックス (acceptance matrix) v2 schemas — P1.

``GET /api/v1/acceptance-matrix`` のレスポンス型。拠点 (office) × 曜日 × 時間帯
で「受け入れ可能か」を ○ (available) / △ (consult) / × (unavailable) の 3 値で
返す read-only な集計 API。

設計メモ:
  - ○△× は **当週の実 Visit から自動算出** (容量ベース・移動時間は見ない粗い目安).
  - ``auto_status`` = 自動算出値、``manual_status`` = 既存 ``acceptance_calendar``
    (常設・毎週共通) の手動値、``effective_status = manual ?? auto``.
  - 週別上書き層 (``acceptance_calendar_week``) は P4 で追加予定 (本 P1 では未実装).
  - 3 値の Literal は ``acceptance.py`` の ``AcceptanceStatus`` を再利用する.
  - DB 書込なし.
"""

from __future__ import annotations

from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.acceptance import AcceptanceStatus

# セルの実効値がどの層由来か (3 層: 週別上書き > 常設上書き > 自動算出).
MatrixCellSource = Literal["auto", "manual_standing", "week_override"]


class MatrixCellMetrics(BaseModel):
    """1 セルの根拠メトリクス (運用者が ○△× の妥当性を判断する材料)."""

    model_config = ConfigDict(extra="forbid")

    remaining_patients_total: int = Field(
        ..., ge=0, description="当該拠点・曜日の全コース残人数合計"
    )
    remaining_minutes_total: int = Field(..., ge=0, description="全コース残時間 (分) 合計")
    available_course_count: int = Field(..., ge=0, description="この時間帯に標準枠が入るコース数")
    consult_course_count: int = Field(..., ge=0, description="部分空きのみ (相談) のコース数")
    max_free_gap_minutes: int = Field(..., ge=0, description="この時間帯と重なる最大空き分")
    reasons: list[str] = Field(default_factory=list, description="判定理由 (日本語短文)")


class MatrixCell(BaseModel):
    """office × weekday × time_slot の 1 マス."""

    model_config = ConfigDict(extra="forbid")

    time_slot: time = Field(..., description="時間帯の開始時刻 (例 10:00:00)")
    auto_status: AcceptanceStatus = Field(..., description="自動算出 ○△×")
    manual_status: AcceptanceStatus | None = Field(
        default=None, description="常設手動上書き (acceptance_calendar). 無ければ None"
    )
    week_status: AcceptanceStatus | None = Field(
        default=None, description="週別手動上書き (acceptance_calendar_week). 無ければ None"
    )
    effective_status: AcceptanceStatus = Field(
        ..., description="week_status ?? manual_status ?? auto_status"
    )
    source: MatrixCellSource = Field(..., description="実効値の由来")
    note: str | None = Field(
        default=None, description="実効上書き層のコメント (週別 or 常設の notes). 無ければ None"
    )
    metrics: MatrixCellMetrics


class DayMatrix(BaseModel):
    """1 拠点 × 1 曜日の列 (時間帯セルのコンテナ)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    date: str = Field(..., description="ISO 日付 (YYYY-MM-DD)")
    office_closed: bool = Field(default=False, description="operating_weekdays 外 = 定休")
    cells: list[MatrixCell] = Field(default_factory=list)


class OfficeMatrix(BaseModel):
    """1 拠点分のマトリックス."""

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    office_name: str = Field(..., description="例: 稲毛拠点")
    city_names: list[str] = Field(
        default_factory=list, description="拠点が担当する区名 (例: 稲毛区, 美浜区)"
    )
    operating_weekdays: list[int] = Field(
        default_factory=list, description="稼働曜日 (0=Mon..6=Sun)"
    )
    week_generated: bool = Field(
        default=False, description="当週に確定コースが 1 つ以上あるか (false=週未生成)"
    )
    # 設定漏れの診断 (PO 要望 2026-08-10: なぜ○×が出ないのかを明示する)。
    #   null                 … 正常 (○×を計算できる)
    #   'no_manager'         … 業務ロール「マネージャー」のスタッフが拠点に不在
    #                          (週生成のコース作成前提を満たさない)
    #   'not_generated'      … マネージャーは居るが当週のコースが 1 本も無い (週未生成)
    #   'assignment_pending' … コースはあるが全て proposed (自動スタッフ割当が未実行。
    #                          この工程がコース確定を兼ねるため ○× の対象にならない)
    setup_state: str | None = Field(
        default=None,
        description="設定漏れ診断: no_manager / not_generated / assignment_pending / null=正常",
    )
    days: list[DayMatrix] = Field(default_factory=list)


class AcceptanceMatrixResponse(BaseModel):
    """``GET /api/v1/acceptance-matrix`` レスポンス."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    service_minutes: int = Field(..., description="判定に用いた標準訪問の長さ (分)")
    slots: list[time] = Field(default_factory=list, description="時間帯ヘッダ (開始時刻列)")
    week_generated_any: bool = Field(
        default=False, description="いずれかの拠点で当週コースが生成済みか"
    )
    offices: list[OfficeMatrix] = Field(default_factory=list)


class WeekOverrideUpsert(BaseModel):
    """週別上書き 1 セルの upsert 入力 (PUT /acceptance-matrix/week-override)."""

    model_config = ConfigDict(extra="forbid")

    office_id: UUID
    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    weekday: int = Field(ge=0, le=6, description="0=Mon..6=Sun")
    time_slot: time = Field(description="時間帯の開始時刻 (例 10:00:00)")
    status: AcceptanceStatus
    notes: str | None = Field(default=None)


class WeekOverrideRead(WeekOverrideUpsert):
    """週別上書き 1 セルのレスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID


class StandingOverrideUpsert(BaseModel):
    """常設(毎週)上書き 1 セルの upsert 入力 (PUT /acceptance-matrix/standing-override).

    ``office_id`` を省略 (None) すると全拠点に一括適用する (例: 全社会議)。
    常設層は ``acceptance_calendar`` (毎週共通・全週一律) を指す。
    """

    model_config = ConfigDict(extra="forbid")

    office_id: UUID | None = Field(default=None, description="None=全拠点に一括")
    weekday: int = Field(ge=0, le=6, description="0=Mon..6=Sun")
    time_slot: time = Field(description="時間帯の開始時刻 (例 10:00:00)")
    status: AcceptanceStatus
    notes: str | None = Field(default=None)


__all__ = [
    "AcceptanceMatrixResponse",
    "DayMatrix",
    "MatrixCell",
    "MatrixCellMetrics",
    "MatrixCellSource",
    "OfficeMatrix",
    "StandingOverrideUpsert",
    "WeekOverrideRead",
    "WeekOverrideUpsert",
]
