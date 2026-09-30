"""Pydantic schemas for checkin_settings (QR 訪問チェックイン Phase 4).

しきい値 (距離 / 精度 / 時間) の **全社一律** 設定 API (GET / PUT / public) の入出力.

* GET レスポンス: 現在の有効値 (= DB 行 or コード既定の合成) と、各値が既定かどうか.
* PUT リクエスト: 部分更新 (全フィールド省略可). 範囲は pydantic Field 制約 (422),
  交差条件 ``review_m >= match_m`` は model_validator (422). DB CHECK と二重検証.
* public レスポンス: staff も読める最小サブセット (match_m / review_m / accuracy_m
  のみ). モバイルの到着プレビュー (概算) しきい値同期に用いる. 時間系は出さない.

``scheduling_settings`` のスキーマを雛形とする (Phase G-88).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 設定列名 (PUT のマージ / is_default 判定で共通利用)。
SETTING_FIELDS: tuple[str, ...] = (
    "match_m",
    "review_m",
    "accuracy_m",
    "no_show_grace_min",
    "late_min",
    "max_inprogress_min",
    "arrival_max_back_min",
    "departure_max_ahead_min",
    "staff_adjust_window_days",
    "unplanned_default_minutes",
)


class CheckinSettingsValues(BaseModel):
    """有効値 (6 項目) のみを表す. 各値は確定 (NULL なし)."""

    model_config = ConfigDict(extra="forbid")

    match_m: int = Field(description="一致しきい値 (m). 10..1000.")
    review_m: int = Field(description="要確認しきい値 (m). 10..2000.")
    accuracy_m: int = Field(description="GPS 精度許容 (m). 5..500.")
    no_show_grace_min: int = Field(description="未訪問の猶予 (分). 0..240.")
    late_min: int = Field(description="遅延しきい値 (分). 0..240.")
    max_inprogress_min: int = Field(description="退出忘れしきい値 (分). 30..1440.")
    arrival_max_back_min: int = Field(description="到着をさかのぼれる上限 (分). 10..240.")
    departure_max_ahead_min: int = Field(
        description="退出を読取時刻より後ろへ動かせる上限 (分). 0..180."
    )
    staff_adjust_window_days: int = Field(
        description="スタッフが実績の時刻を合わせられる期間 (日). 0..31."
    )
    unplanned_default_minutes: int = Field(description="予定外訪問の仮の所要時間 (分). 10..240.")


class CheckinSettingsDefaults(BaseModel):
    """各フィールドが既定値由来か (= DB 未設定 / NULL) を表す bool フラグ群.

    true = その値はコード既定値 (DB に保存値が無い), false = DB 保存値を採用.
    フロントの「既定」バッジ表示に用いる.
    """

    model_config = ConfigDict(extra="forbid")

    match_m: bool
    review_m: bool
    accuracy_m: bool
    no_show_grace_min: bool
    late_min: bool
    max_inprogress_min: bool
    arrival_max_back_min: bool
    departure_max_ahead_min: bool
    staff_adjust_window_days: bool
    unplanned_default_minutes: bool


class CheckinSettingsRead(BaseModel):
    """GET / PUT レスポンス: 有効値 + 各値が既定かどうか."""

    model_config = ConfigDict(extra="forbid")

    values: CheckinSettingsValues
    is_default: CheckinSettingsDefaults


class CheckinSettingsUpdate(BaseModel):
    """PUT リクエスト body (部分更新).

    * 全フィールド省略可 (= 未指定はその列を変更しない).
    * 明示的に ``null`` を指定するとその列を既定に戻す (DB 列を NULL に).
    * 範囲外は 422 (pydantic Field 制約).
    * ``review_m >= match_m`` は両方が同一リクエストで非 NULL 指定された場合に検証
      (片方のみ更新時は API 側で既存値とのマージ後に最終検証する).
    """

    model_config = ConfigDict(extra="forbid")

    # ``None`` = 既定に戻す. 未指定 (= フィールド非送出) は ``model_fields_set`` で
    # 区別する (= 変更しない).
    match_m: int | None = Field(default=None, ge=10, le=1000)
    review_m: int | None = Field(default=None, ge=10, le=2000)
    accuracy_m: int | None = Field(default=None, ge=5, le=500)
    no_show_grace_min: int | None = Field(default=None, ge=0, le=240)
    late_min: int | None = Field(default=None, ge=0, le=240)
    max_inprogress_min: int | None = Field(default=None, ge=30, le=1440)
    arrival_max_back_min: int | None = Field(default=None, ge=10, le=240)
    departure_max_ahead_min: int | None = Field(default=None, ge=0, le=180)
    staff_adjust_window_days: int | None = Field(default=None, ge=0, le=31)
    unplanned_default_minutes: int | None = Field(default=None, ge=10, le=240)

    @model_validator(mode="after")
    def _check_review_gte_match_within_payload(self) -> CheckinSettingsUpdate:
        """同一リクエスト内で両方が指定された場合の ``review_m >= match_m`` を検証 (422).

        片方のみ指定の場合は既存値とマージ後に API 側で最終検証するため、ここでは
        両方が明示指定 (非 NULL) のケースのみ弾く.
        """
        if self.match_m is not None and self.review_m is not None and self.review_m < self.match_m:
            raise ValueError("review_m は match_m 以上である必要があります")
        return self


class CheckinSettingsPublic(BaseModel):
    """public レスポンス (staff も読める最小サブセット).

    モバイル到着プレビューの概算しきい値同期用の距離系と、スマホで実績の時刻を合わせる
    シートの範囲 (上限 3 項目) を返す. 判定の時間系 (no_show_grace_min / late_min /
    max_inprogress_min) と予定外訪問の仮の所要時間は出さない.
    """

    model_config = ConfigDict(extra="forbid")

    match_m: int
    review_m: int
    accuracy_m: int
    # 実績の時刻を合わせる上限 (スマホのシートが範囲を出すのに使う・mig 0089)。
    # 古い応答との互換のため FE 側は無ければ既定を使う。
    arrival_max_back_min: int
    departure_max_ahead_min: int
    staff_adjust_window_days: int
