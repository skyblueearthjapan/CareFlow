"""特別訪問週間 (special visit week) の API スキーマ — 設計 §4 と 1:1.

`docs/plans/special-visit-week-design.md` §4 のリクエスト / レスポンス契約を
そのまま pydantic に写したもの。時刻は FE と揃えて ``"HH:MM"`` 文字列で扱う
(DB 上は ``Time`` 型)。
"""

from __future__ import annotations

from datetime import date as _Date  # noqa: N812 (project-local alias)
from datetime import datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _validate_hhmm(value: str) -> str:
    """``HH:MM`` を検証して 0 埋め正規化する (staff_events と同方針)."""
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid HH:MM time string: {value!r}")
    try:
        h, m = int(parts[0]), int(parts[1])
        time(h, m)
    except ValueError as exc:
        raise ValueError(f"invalid HH:MM time string: {value!r}") from exc
    return f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------------------
# 期間 (special_visit_periods)
# ---------------------------------------------------------------------------


class PeriodCreate(BaseModel):
    """POST /special-visit-periods のリクエスト."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    start_date: _Date
    end_date: _Date
    weekly_target: Annotated[int, Field(ge=1, le=7)] = 5
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_range(self) -> PeriodCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


class PeriodUpdate(BaseModel):
    """PATCH /special-visit-periods/{id} のリクエスト (部分更新)."""

    model_config = ConfigDict(extra="forbid")

    end_date: _Date | None = None
    weekly_target: Annotated[int, Field(ge=1, le=7)] | None = None
    note: str | None = Field(default=None, max_length=500)
    status: Literal["active", "ended", "cancelled"] | None = None


class PeriodRead(BaseModel):
    """期間のレスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    patient_id: UUID
    start_date: _Date
    end_date: _Date
    weekly_target: int
    note: str | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# マーク (special_visit_marks)
# ---------------------------------------------------------------------------


class MarkCreate(BaseModel):
    """POST /special-visit-periods/{id}/marks (○ 追加) / .../displace (退避)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: Annotated[int, Field(ge=2000, le=2100)]
    iso_week: Annotated[int, Field(ge=1, le=53)]
    # 0=Mon..5=Sat (日曜は対象外)。
    weekday: Annotated[int, Field(ge=0, le=5)]


class PlacedSummary(BaseModel):
    """配置済みマークの配置先サマリ (○ → ● のツールチップ用)."""

    model_config = ConfigDict(extra="forbid")

    start_time: str
    course_label: str | None = None


class MarkRead(BaseModel):
    """マークのレスポンス (設計 §4 末尾の MarkRead 定義)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    period_id: UUID
    patient_id: UUID
    iso_year: int
    iso_week: int
    weekday: int
    kind: str
    status: str
    placed_visit_id: UUID | None = None
    placed_summary: PlacedSummary | None = None


class PlaceRequest(BaseModel):
    """POST /special-visit-marks/{id}/place のリクエスト.

    配置先コースは ``course_id`` 直指定か、propose-slots の候補が持つ
    ``(office_id, course_code)`` のどちらかで指定する (週・曜日は mark 側が正)。
    """

    model_config = ConfigDict(extra="forbid")

    course_id: UUID | None = None
    office_id: UUID | None = None
    course_code: str | None = None
    start_time: str
    # NG スタッフ / 性別制限の確認フロー (docs/plans/patient-ng-staff-design.md §7-2)。
    # 既定 False = 従来どおり。違反があれば 422 (code=constraint_confirmation_required)。
    acknowledge_constraint_warnings: bool = False

    @model_validator(mode="after")
    def _check_course_ref(self) -> PlaceRequest:
        if self.course_id is None and (self.office_id is None or self.course_code is None):
            raise ValueError("course_id か (office_id + course_code) のどちらかが必要です")
        return self

    @field_validator("start_time")
    @classmethod
    def _check_hhmm(cls, v: str) -> str:
        return _validate_hhmm(v)


class PlaceResponse(BaseModel):
    """POST /special-visit-marks/{id}/place のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    mark: MarkRead
    visit_id: UUID


# ---------------------------------------------------------------------------
# カレンダー (GET /special-visit-periods/{id}/calendar)
# ---------------------------------------------------------------------------


class FixedVisitRead(BaseModel):
    """カレンダー 1 セルの固定訪問カード 1 枚.

    生成済み週は実 visit (``visit_id`` 非 NULL・``generated=True``)、未生成週は
    ``patient_fixed_visits`` (mode='normal') の投影 (``visit_id=None`` ・
    ``generated=False``)。
    """

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID | None = None
    start_time: str
    end_time: str
    course_label: str | None = None
    staff_name: str | None = None
    generated: bool


class PreferredSlot(BaseModel):
    """希望訪問カレンダーの当該曜日の希望時間帯 (薄敷き表示用)."""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str


class CalendarDay(BaseModel):
    """カレンダー 1 セル (週 × 曜日)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int
    date: _Date
    fixed_visits: list[FixedVisitRead] = []
    extra_mark: MarkRead | None = None
    displaced_mark: MarkRead | None = None
    preferred: list[PreferredSlot] = []


class CalendarWeek(BaseModel):
    """カレンダー 1 行 (= 1 ISO 週)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    week_monday: _Date
    days: list[CalendarDay] = []
    # 週合計 = 固定訪問の残数 + extra ○ (pool/placed 両方) + displaced チケット数 (§3)。
    total: int
    # 目標達成 = 週合計 >= weekly_target (「以上」判定)。
    target_met: bool


class CalendarRead(BaseModel):
    """GET /special-visit-periods/{id}/calendar のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    period: PeriodRead
    weeks: list[CalendarWeek] = []


# ---------------------------------------------------------------------------
# プール (GET /special-visit-marks/pool)
# ---------------------------------------------------------------------------


class PoolPatient(BaseModel):
    """プールチケットに同梱する患者情報 (候補提案 UI の入力に使う)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    code: str
    sex: str | None = None
    sex_restriction: str | None = None
    requires_multiple_staff: bool = False
    lat: float | None = None
    lng: float | None = None
    primary_office_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        """``Numeric`` の lat/lng を float に落として返す."""
        if isinstance(data, dict):
            return data
        return {
            "id": data.id,
            "name": data.name,
            "code": data.code,
            "sex": data.sex,
            "sex_restriction": data.sex_restriction,
            "requires_multiple_staff": bool(data.requires_multiple_staff),
            "lat": float(data.lat) if data.lat is not None else None,
            "lng": float(data.lng) if data.lng is not None else None,
            "primary_office_id": data.primary_office_id,
        }


class PoolPeriod(BaseModel):
    """プールチケットに同梱する期間の要約."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    weekly_target: int
    end_date: _Date


class LastPlacement(BaseModel):
    """同一期間内の直近の placed マークの配置先 (参考ヒント・強制しない)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int
    start_time: str
    course_label: str | None = None
    staff_name: str | None = None


class PoolTicketRead(BaseModel):
    """GET /special-visit-marks/pool の 1 チケット."""

    model_config = ConfigDict(extra="forbid")

    mark: MarkRead
    patient: PoolPatient
    period: PoolPeriod
    last_placement: LastPlacement | None = None
    # 提案と配置の枠長を一致させるための正典所要分 (place の _resolve_service_minutes
    # と同一計算: PFV duration_min → weekly_pattern → 30分)。FE は propose-slots の
    # service_minutes にこれを使う (レビュー補強 2026-07-29)。
    service_minutes: int = 30
