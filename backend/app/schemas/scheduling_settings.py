"""Pydantic schemas for scheduling_settings (Phase G-88 Step2).

自動最適化ルールの事業所別設定 API (GET / PUT) の入出力スキーマ.

* GET レスポンス: 現在の有効値 (= DB 行 or 既定の合成) と、各値が既定かどうか.
* PUT リクエスト: 部分更新 (全フィールド省略可). 範囲バリデーションは
  pydantic Field の制約で行い、違反は 422.

この Step では最適化挙動は変えない (値の保存・読み出しのみ). フロント (Step4) と
パイプライン (Step3) が依存する確定契約.
"""

from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchedulingSettingsValues(BaseModel):
    """有効値 (8 項目) のみを表す. 各値は確定 (NULL なし)."""

    model_config = ConfigDict(extra="forbid")

    visit_buffer_min: int = Field(description="① 訪問間バッファー (分). 0..30.")
    travel_speed_kmh: float = Field(description="② 移動速度 (km/h). 10..40.")
    lunch_duration_min: int = Field(description="③a 昼休み長さ (分). 30..90.")
    lunch_window_start: time = Field(description="③b 昼休み開始時刻.")
    lunch_window_end: time = Field(description="③b 昼休み終了時刻.")
    business_start: time = Field(description="④ 営業開始時刻.")
    business_end: time = Field(description="④ 営業終了時刻.")
    max_patients_per_course: int = Field(description="⑤ 1 コース最大人数. 1..10.")


class SchedulingSettingsDefaults(BaseModel):
    """各フィールドが既定値由来か (= DB 未設定 / NULL) を表す bool フラグ群.

    true = その値はコード既定値 (DB に保存値が無い), false = DB 保存値を採用.
    フロントの「既定」バッジ表示に用いる.
    """

    model_config = ConfigDict(extra="forbid")

    visit_buffer_min: bool
    travel_speed_kmh: bool
    lunch_duration_min: bool
    lunch_window_start: bool
    lunch_window_end: bool
    business_start: bool
    business_end: bool
    max_patients_per_course: bool


class SchedulingSettingsRead(BaseModel):
    """GET / PUT レスポンス: 有効値 + 各値が既定かどうか."""

    model_config = ConfigDict(extra="forbid")

    values: SchedulingSettingsValues
    is_default: SchedulingSettingsDefaults


class SchedulingSettingsUpdate(BaseModel):
    """PUT リクエスト body (部分更新).

    * 全フィールド省略可 (= 未指定はその列を変更しない).
    * 明示的に ``null`` を指定するとその列を既定に戻す (DB 列を NULL に).
    * 範囲外は 422 (pydantic Field 制約).
    * lunch_window_start < lunch_window_end / business_start < business_end は
      両方が同一リクエストで非 NULL 指定された場合に検証 (片方のみ更新時は API 側で
      既存値とのマージ後に最終検証する).
    """

    model_config = ConfigDict(extra="forbid")

    # ``None`` = 既定に戻す. 未指定 (= フィールド非送出) は ``model_fields_set`` で
    # 区別する (= 変更しない).
    visit_buffer_min: int | None = Field(default=None, ge=0, le=30)
    travel_speed_kmh: float | None = Field(default=None, ge=10, le=40)
    lunch_duration_min: int | None = Field(default=None, ge=30, le=90)
    lunch_window_start: time | None = Field(default=None)
    lunch_window_end: time | None = Field(default=None)
    business_start: time | None = Field(default=None)
    business_end: time | None = Field(default=None)
    max_patients_per_course: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def _check_time_order_within_payload(self) -> SchedulingSettingsUpdate:
        """同一リクエスト内で両端が指定された場合の前後関係を検証 (422).

        片方のみ指定の場合は既存値とマージ後に API 側で最終検証するため、ここでは
        両方が明示指定 (非 NULL) のケースのみ弾く.
        """
        if (
            self.lunch_window_start is not None
            and self.lunch_window_end is not None
            and self.lunch_window_start >= self.lunch_window_end
        ):
            raise ValueError("lunch_window_start は lunch_window_end より前である必要があります")
        if (
            self.business_start is not None
            and self.business_end is not None
            and self.business_start >= self.business_end
        ):
            raise ValueError("business_start は business_end より前である必要があります")
        return self
