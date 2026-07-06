"""週ボード read API (Phase2-3a) Pydantic v2 schemas.

エンドポイント:
  - GET /api/v1/schedule/v2/board (admin/manager)

対象週 × 拠点の実 Visit を「モバイル現場ボード `/m`」が描画しやすい構造
(office × weekday × course → visits[]) で返す read-only API のレスポンス型.

実装方針:
  - サービス層 (``app.services.scheduling.board_service``) が DB を 1 回ずつ
    ロード (週 Visit / 患者 / 拠点) して構造を組み立てる. 本 schema は形のみ規定.
  - 実 Visit 由来の **実時刻** (start/end) を HH:MM (秒なし) でそのまま返す.
    空き枠の時間帯は別途 propose-slots が扱う (本 API は扱わない).
  - DB 書込なし.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 保険区分 (UI 表示用に短縮). 患者マスタ insurance="medical"/"care" を med/care に正規化.
BoardInsurance = Literal["med", "care"]

# visit の配置種別 (Visit.type 由来). regular→normal, それ以外→special.
BoardVisitMode = Literal["normal", "special"]

# 曜日コード (患者マスタ weekly_pattern / propose-slots と同一表記).
WeekdayCode = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Response sub-models
# ---------------------------------------------------------------------------


class BoardVisit(BaseModel):
    """ボード上の 1 訪問 (実 Visit 由来, 実時刻)."""

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_kana: str | None = None
    insurance: BoardInsurance | None = Field(
        default=None, description="med (医療) / care (介護). 不明は None"
    )
    service_minutes: int = Field(..., ge=0, description="end - start (分)")
    start_time: str = Field(..., description="HH:MM (実訪問開始)")
    end_time: str = Field(..., description="HH:MM (実訪問終了)")
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    same_address_group_id: str | None = Field(
        default=None, description="同住所連結用 group_id (📍同住所表示)"
    )
    mode: BoardVisitMode = Field(default="normal")
    slot_index: int = Field(..., ge=0, description="コース内 start 昇順での 0 始まり連番")
    status: str = Field(default="planned", description="planned / cancelled")


class BoardCapacity(BaseModel):
    """コース当日の容量集計."""

    model_config = ConfigDict(extra="forbid")

    filled: int = Field(..., ge=0, description="実訪問件数")
    max: int = Field(..., description="コース定員 (6)")
    total_minutes: int = Field(..., ge=0, description="実訪問サービス分の合計")
    remaining: int = Field(..., description="max - filled (負にしない)")


class BoardCourse(BaseModel):
    """office × weekday の 1 コース."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID | None = None
    course_code: str = Field(..., description="A..M / M2..M9")
    course_label: str = Field(..., description="拠点短縮 + コード (例: 稲A / 都A)")
    staff_name: str | None = Field(default=None, description="担当 Ns")
    visits: list[BoardVisit] = Field(default_factory=list)
    capacity: BoardCapacity


class BoardCell(BaseModel):
    """office × weekday の 1 マス (courses のコンテナ + 休講フラグ)."""

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    weekday_code: WeekdayCode
    closed: bool = Field(
        default=False,
        description="その曜日にスタッフ 0 名 = 休講 (courses も空になる)",
    )
    staff_count: int = Field(default=0, ge=0, description="その曜日の稼働 staff 数")
    manager_count: int = Field(default=0, ge=0, description="その曜日の稼働 manager 数")
    patient_count: int = Field(default=0, ge=0, description="その日の実訪問患者数")
    courses: list[BoardCourse] = Field(default_factory=list)


class BoardOffice(BaseModel):
    """対象拠点."""

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    office_name: str = Field(..., description="例: 稲毛 / 都賀")


class BoardWeekday(BaseModel):
    """曜日ヘッダー (日付 + 全拠点横断の集計)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6)
    weekday_code: WeekdayCode
    date: str = Field(..., description="ISO 日付 (YYYY-MM-DD)")
    patient_count: int = Field(default=0, ge=0, description="全拠点合計の実訪問患者数")


class BoardResponse(BaseModel):
    """``GET /v2/board`` レスポンス (週ボード描画構造)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    course_max: int = Field(default=6, description="1 コースの定員")
    offices: list[BoardOffice] = Field(default_factory=list)
    weekdays: list[BoardWeekday] = Field(default_factory=list)
    board: list[BoardCell] = Field(
        default_factory=list,
        description="office × weekday ごとの courses コンテナ",
    )


__all__ = [
    "BoardCapacity",
    "BoardCell",
    "BoardCourse",
    "BoardInsurance",
    "BoardOffice",
    "BoardResponse",
    "BoardVisit",
    "BoardVisitMode",
    "BoardWeekday",
    "WeekdayCode",
]
