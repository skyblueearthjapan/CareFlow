"""PatientV2 / WeeklyPatternV2 schemas (Wave 0-C).

設計仕様書 v0.9 §4.1 に基づき、v2 で残す **16 項目** のみを保持する。
v1 で削除する 10 項目（年齢 / NG時間 / エリア / 指定タイプ / NGスタッフ /
同行希望スタッフ / 継続要望 / 必要スタッフ数 / 曜日優先度 / NG曜日）は
v2 schema には含めない。

追加項目（§3.3 / §3.4）:
- weekly_pattern.staff_count (既定 1, 2 で 2 名体制)
- special_weekly_pattern (JSONB)
- special_week_active (適用週リスト [{iso_year, iso_week}, ...])
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 7 weekday short codes used in JSONB (Mon..Sun, English short form).
# v2 でも v1 と同じ表現を維持する。
WeekdayV2 = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 時間タイプ (§4.1):
#   固定 / 時間帯 (preferred_start / preferred_end 必須)
#   午前 / 午後 / 終日 (時刻不要)
TimeTypeV2 = Literal["固定", "時間帯", "午前", "午後", "終日"]

# 訪問頻度 (§4.1 / §7.1 残置確定):
#   毎週 / 隔週 / 月次。具体的な使い方は別途検討（"TBD" のまま）。
VisitFrequencyV2 = Literal["every", "biweekly", "monthly"]

# 性別 (§4.1):
#   保存値は英語小文字。UI 表示は ja-JP に変換する。
SexV2 = Literal["male", "female", "unknown"]

# 性別制限 (§4.1, ハード制約):
#   "female_only" = 女性スタッフのみ可
#   "male_only" = 男性スタッフのみ可
#   None = 制限なし
SexRestrictionV2 = Literal["female_only", "male_only"]

# 患者状態 (§4.1):
#   active = 稼働中 (割当対象)
#   suspended = 一時休止
#   admitted = 入院中
#   pending = 開始前
#   cancelled = 解約済み
PatientStatusV2 = Literal["active", "suspended", "admitted", "pending", "cancelled"]

# 保険区分 (§4.1):
InsuranceV2 = Literal["medical", "care"]

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise ValueError(f"invalid HH:MM time string: {value!r}")
    return value


class WeeklyPatternEntryV2(BaseModel):
    """希望訪問パターンの 1 曜日エントリ (§3.3 / §4.1).

    `weekly_pattern` JSONB は曜日キー → このエントリ の形を取る場合と、
    リスト形式 (`[{weekday, ...}]`) の場合の両方が運用上ありうる。
    本クラスは後者の要素として用い、前者の場合は `WeeklyPatternV2.entries`
    に flatten したリストを格納する想定。
    """

    model_config = ConfigDict(extra="allow")

    weekday: WeekdayV2 | None = None
    time_type: TimeTypeV2 | None = None
    preferred_start: str | None = Field(
        default=None,
        description="HH:MM (24h). time_type=固定/時間帯 のときに使用",
    )
    preferred_end: str | None = Field(
        default=None,
        description="HH:MM (24h). time_type=時間帯 のときに使用",
    )
    service_minutes: int | None = Field(default=None, ge=0, le=300)
    staff_count: int = Field(
        default=1,
        ge=1,
        le=2,
        description="必要スタッフ数 (§3.3). 既定 1, 2 で 2 名体制",
    )

    @field_validator("preferred_start", "preferred_end")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)


class WeeklyPatternV2(BaseModel):
    """患者の希望訪問パターン (§4.1 / §3.3).

    JSONB として `patients.weekly_pattern` に保存される。
    通常パターンと特別週パターン (`special_weekly_pattern`) で同じ shape を共有。
    """

    model_config = ConfigDict(extra="allow")

    frequency_per_week: int | None = Field(
        default=None,
        ge=1,
        le=7,
        description="週あたり訪問回数 (§4.1 核項目)",
    )
    visit_frequency: VisitFrequencyV2 | None = Field(
        default=None,
        description="訪問頻度 (毎週/隔週/月次, §7.1 残置確定, 具体仕様 TBD)",
    )
    visit_weeks: str | None = Field(
        default=None,
        description="訪問週 (例 '1,3', §7.1 残置確定, 具体仕様 TBD)",
    )
    preferred_weekdays: list[WeekdayV2] | None = None
    service_minutes: int | None = Field(default=None, ge=0, le=300)
    time_type: TimeTypeV2 | None = None
    preferred_start: str | None = None
    preferred_end: str | None = None
    entries: list[WeeklyPatternEntryV2] | None = Field(
        default=None,
        description=(
            "曜日ごとの細粒度設定 (§3.3 staff_count 等). "
            "リスト形式運用時に使用。曜日サマリ系は preferred_weekdays/time_type 等が真値"
        ),
    )

    @field_validator("preferred_start", "preferred_end")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)


class SpecialWeekRefV2(BaseModel):
    """special_week_active の 1 要素 (§3.4 / §4.1).

    "適用週リスト [{iso_year, iso_week}, ...]" の各要素。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)


class PatientV2Base(BaseModel):
    """患者マスタ v2 (§4.1 残す 16 項目).

    削除済み (v2 schema に含めない): age, ng_time_start, ng_time_end,
    required_staff_count, area, ng_staff_ids, preferred_staff_ids,
    specified_type, continuous_request, weekday_priority。
    """

    model_config = ConfigDict(extra="forbid")

    # 基本情報
    code: str = Field(min_length=1, max_length=64, description="患者コード (一意識別)")
    name: str = Field(min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120, description="表示・並び順")
    sex: SexV2 | None = Field(default=None, description="性別制限判定にも使用")
    status: PatientStatusV2 = Field(
        default="active",
        description="稼働/休止/入院/解約 等の割当除外フラグ",
    )

    # 連絡先
    address: str | None = Field(default=None, description="距離計算ベースの住所")
    lat: float | None = Field(default=None, ge=-90, le=90, description="距離計算用緯度")
    lng: float | None = Field(default=None, ge=-180, le=180, description="距離計算用経度")

    # 保険・拠点
    insurance: InsuranceV2 | None = Field(default=None, description="医療/介護")
    primary_office_id: UUID | None = Field(
        default=None,
        description=(
            "主担当拠点 (§4.3). 住所から自動紐付け + 手動上書き可。稲毛 / 都賀のみ運用 (MVP)"
        ),
    )

    # 訪問条件
    sex_restriction: SexRestrictionV2 | None = Field(
        default=None,
        description="ハード制約 (§4.1 / §5.4)",
    )
    # W18 Phase A: 複数スタッフ同行必須フラグ (§4.1 / §5.4).
    # 患者単位の独立フラグ。weekly_pattern.entries[].staff_count とは別軸。
    requires_multiple_staff: bool = Field(
        default=False,
        description=(
            "TRUE で常に 2 名体制が必要な患者. weekly_pattern.staff_count とは独立した "
            "患者単位フラグ (§4.1 / §5.4)."
        ),
    )

    # 希望訪問パターン (§4.1 核)
    weekly_pattern: WeeklyPatternV2 | None = Field(
        default=None,
        description="通常パターン (§3.4 / §4.1)",
    )
    special_weekly_pattern: WeeklyPatternV2 | None = Field(
        default=None,
        description="特別週用パターン (§3.4 Option A 確定)",
    )
    special_week_active: list[SpecialWeekRefV2] = Field(
        default_factory=list,
        description=(
            "適用週リスト [{iso_year, iso_week}, ...] (§3.4 / §4.1). "
            "該当週は special_weekly_pattern を優先、非該当週は weekly_pattern を使用"
        ),
    )

    # 備考
    note: str | None = Field(default=None, description="自由記述")


class PatientV2Create(PatientV2Base):
    """POST /api/v1/patients リクエスト (W1-BE1)."""


class PatientV2Update(BaseModel):
    """PATCH /api/v1/patients/{id} リクエスト (W1-BE1).

    全フィールド optional. base から `.partial()` 風に手書きで列挙する
    （v1 patient.py のスタイル踏襲。未指定値が default で誤って上書きされるのを防ぐため）。
    """

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120)
    sex: SexV2 | None = None
    status: PatientStatusV2 | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    insurance: InsuranceV2 | None = None
    primary_office_id: UUID | None = None
    sex_restriction: SexRestrictionV2 | None = None
    # W18 Phase A: 患者単位の複数スタッフ必須フラグ. None=未指定 (PATCH 時に touch しない)
    requires_multiple_staff: bool | None = None
    weekly_pattern: WeeklyPatternV2 | None = None
    special_weekly_pattern: WeeklyPatternV2 | None = None
    special_week_active: list[SpecialWeekRefV2] | None = None
    note: str | None = None


class PatientV2Read(PatientV2Base):
    """GET /api/v1/patients/{id} レスポンス (W1-BE1).

    Read 経路は JSONB を緩く受ける (v1 patient.py と同じ asymmetric 戦略)。
    weekly_pattern / special_weekly_pattern は **dict | None** で受け、
    フロント側で coerce する。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    # asymmetric: 書込みは構造化 schema、読込は dict のまま
    weekly_pattern: dict | None = None  # type: ignore[assignment]
    special_weekly_pattern: dict | None = None  # type: ignore[assignment]
    special_week_active: list[dict] = []  # type: ignore[assignment]
