"""提案 API (Phase2-2) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/propose-slots (admin/manager)

候補患者の希望から「実スケジュール (対象週 × 拠点) の実現可能な空き枠」を
算出・ランキングして返す read-only API のリクエスト / レスポンス型.

実装方針:
  - 純ロジック層 (``app.services.scheduling.proposal_solver``) が実現可能性を
    判定するので、本 schema は入出力の形のみを規定する.
  - ``time`` は HH:MM (秒なし) 文字列でやり取りする (UI / モバイル都合).
  - DB 書込なし (提案算出のみ).
"""

from __future__ import annotations

import uuid
from datetime import time as time_cls
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 候補患者の希望時刻種別 (患者マスタ TimeTypeV2 に準拠).
ProposeTimeType = Literal["固定", "時間帯", "午前", "午後", "終日"]

# 訪問頻度 (患者マスタ VisitFrequencyV2 に準拠). 提案算出には未使用だが
# 入力として受け取り将来の隔週/月次フィルタに備える.
ProposeVisitFrequency = Literal["every", "biweekly", "monthly"]

# 曜日コード (患者マスタ weekly_pattern と同一表記).
WeekdayCode = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}
WEEKDAY_INT_TO_CODE: dict[int, str] = {v: k for k, v in WEEKDAY_CODE_TO_INT.items()}


def _parse_hhmm(value: str | None) -> time_cls | None:
    """ "HH:MM" / "HH:MM:SS" を ``time`` に変換. None / 空文字は None."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(f"時刻は HH:MM 形式が必要です (received: {value!r})")
    try:
        h = int(parts[0])
        m = int(parts[1])
        sec = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"時刻は HH:MM 形式が必要です (received: {value!r})") from exc
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59):
        raise ValueError(f"時刻が範囲外です (received: {value!r})")
    return time_cls(h, m, sec)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ProposeSlotsRequest(BaseModel):
    """``POST /v2/propose-slots`` リクエスト (候補患者の希望)."""

    model_config = ConfigDict(extra="forbid")

    # --- 候補の座標確定 (address 優先 → lat/lng) ---
    address: str | None = Field(default=None, description="候補患者の住所 (geocode 対象)")
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)

    # --- 候補の希望 (患者マスタ項目に準拠) ---
    service_minutes: int = Field(..., ge=1, le=480, description="訪問サービス時間 (分)")
    time_type: ProposeTimeType | None = Field(default=None)
    preferred_start: str | None = Field(default=None, description="HH:MM")
    preferred_end: str | None = Field(default=None, description="HH:MM")
    preferred_weekdays: list[WeekdayCode] = Field(default_factory=list)
    visit_frequency: ProposeVisitFrequency | None = Field(default=None)
    frequency_per_week: int | None = Field(default=None, ge=1, le=7)
    requires_multiple_staff: bool = Field(default=False)
    sex_restriction: str | None = Field(default=None)

    # --- 対象週 / 拠点 ---
    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="未指定なら住所判定拠点 or 全拠点を対象にする",
    )

    # --- 既存患者からの提案用 (同一患者の既存訪問をペア候補から除外) ---
    existing_patient_id: uuid.UUID | None = Field(default=None)

    # 返却件数上限 (上位 N 件).
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("preferred_start", "preferred_end")
    @classmethod
    def _validate_hhmm(cls, v: str | None) -> str | None:
        # 形式チェックのみ (実体変換は endpoint 側で行う).
        _parse_hhmm(v)
        return v


# ---------------------------------------------------------------------------
# Response sub-models
# ---------------------------------------------------------------------------


class ProposeMiniScheduleEntry(BaseModel):
    """ミニスケジュール 1 行 (そのコース当日の既存訪問 + 提案枠)."""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(..., description="HH:MM (開始時刻)")
    name: str = Field(..., description="患者名 (提案枠は候補名 or 「(提案)」)")
    ins: str | None = Field(default=None, description="保険区分等の補助表示 (現状 None)")
    is_here: bool = Field(default=False, description="この行が提案枠 (ここに入れる) か")
    is_pair: bool = Field(default=False, description="同住所ペアとして入る行か")
    # ③ 表示統一: 通常リストと同じ色分け用 (性別制限・2名体制).
    sex_restriction: str | None = Field(
        default=None, description="'female_only' | 'male_only' | None"
    )
    is_multi_staff: bool = Field(default=False, description="2名体制 (requires_multiple_staff) か")


class ProposeSlotItem(BaseModel):
    """ランキング済み提案スロット 1 件."""

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    office_name: str | None = None
    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    weekday_code: WeekdayCode
    course_code: str = Field(..., description="A..E / M..M9")
    course_label: str = Field(..., description="拠点名 + コード (例: 稲A)")
    staff_name: str | None = None
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    score: float
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_pair: bool = False
    pair_partner: str | None = Field(
        default=None, description="同住所ペア相手の患者名 (is_pair=True のとき)"
    )
    mini_schedule: list[ProposeMiniScheduleEntry] = Field(default_factory=list)


class ProposeCoverageDay(BaseModel):
    """週N日カバレッジ: 希望曜日 1 日ぶんの実現可否と最良枠."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    weekday_code: WeekdayCode
    has_slot: bool = Field(..., description="この曜日に実現可能枠が 1 つでもあるか")
    best_slot: ProposeSlotItem | None = Field(
        default=None, description="この曜日の最良枠 (has_slot=False なら null)"
    )


class ProposeCoverage(BaseModel):
    """週N日カバレッジ集計 (希望が週N日なら、その N 日とも提案できているか)."""

    model_config = ConfigDict(extra="forbid")

    required_days: int = Field(
        default=0,
        ge=0,
        description="必要日数. frequency_per_week 優先, 無ければ len(preferred_weekdays)",
    )
    requested_weekdays: list[int] = Field(
        default_factory=list, description="希望曜日 (preferred_weekdays). 無ければ全曜日"
    )
    per_day: list[ProposeCoverageDay] = Field(
        default_factory=list, description="希望曜日ごとの実現可否 + 最良枠"
    )
    covered_days: int = Field(default=0, ge=0, description="has_slot=True の曜日数")
    fully_covered: bool = Field(
        default=False, description="required_days>0 かつ covered_days>=required_days"
    )


class ProposeSlotsResponse(BaseModel):
    """``POST /v2/propose-slots`` レスポンス (ランキング済み候補リスト)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    candidate_lat: float | None = None
    candidate_lng: float | None = None
    resolved_office_id: uuid.UUID | None = Field(
        default=None, description="address から判定した拠点 (あれば)"
    )
    slots: list[ProposeSlotItem] = Field(default_factory=list)
    coverage: ProposeCoverage | None = Field(
        default=None, description="週N日カバレッジ (希望曜日ごとの実現可否)"
    )
    message: str | None = Field(
        default=None, description="0 件時の「入れられる枠なし」メッセージ等"
    )


__all__ = [
    "ProposeCoverage",
    "ProposeCoverageDay",
    "ProposeMiniScheduleEntry",
    "ProposeSlotItem",
    "ProposeSlotsRequest",
    "ProposeSlotsResponse",
    "ProposeTimeType",
    "ProposeVisitFrequency",
    "WEEKDAY_CODE_TO_INT",
    "WEEKDAY_INT_TO_CODE",
    "WeekdayCode",
]
