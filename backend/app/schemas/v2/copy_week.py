"""Schemas for 「前の週をコピーして週を作る」(docs/plans/copy-week-design-2026-09-30.md §4-7).

  - GET  /api/v1/schedule/v2/copy-week/sources   写す元の候補週 (read-only)
  - POST /api/v1/schedule/v2/copy-week/preview   確認画面の内容 (read-only・DB 不変)
  - POST /api/v1/schedule/v2/copy-week           実行 (``confirm: true`` 必須)

実行のレスポンスは自動スタッフ割当の結果を入れ子で持つため API 側
(``app/api/v1/copy_week.py``) で定義する。
"""

from __future__ import annotations

from datetime import date, time
from datetime import date as _Date  # noqa: N812 (フィールド名 date と衝突させない)
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CopyWeekMode = Literal["replace", "add_only"]


class HolidayRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: _Date
    name: str


class CopyWeekSourceItem(BaseModel):
    """写す元の候補週 1 件 (モックの一覧 1 行)."""

    model_config = ConfigDict(extra="forbid")

    week_start: date
    # 取消・予定外を除いた訪問件数 (= 写す対象になりうる件数の上限)
    visits: int
    patients: int
    cancelled: int
    unplanned: int
    # QR の到着記録がある訪問の件数
    qr_arrivals: int
    holidays: list[HolidayRead] = Field(default_factory=list)


class CopyWeekSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_week_start: date
    items: list[CopyWeekSourceItem] = Field(default_factory=list)


class CopyWeekPreviewRequest(BaseModel):
    """確認画面の内容. 選んだオプションを渡すと件数がそれに合わせて出る."""

    model_config = ConfigDict(extra="forbid")

    source_week_start: date
    target_week_start: date
    exclude_visit_ids: list[UUID] = Field(default_factory=list)
    fill_from_fixed: bool = False


class CopyCounts(BaseModel):
    """確認画面で見せた件数 (実行時に計画し直した件数と比べる)."""

    model_config = ConfigDict(extra="forbid")

    copy_count: int
    fill_count: int
    replace_count: int
    needs_manual_count: int


class CopyWeekRequest(CopyWeekPreviewRequest):
    """実行. ``confirm`` が true でなければ 422 (誤操作の最後の歯止め)."""

    assign_staff: bool = True
    confirm: bool = False
    # 確認画面で見せた件数。実行時の件数と違えば結果に印を付けて返す (止めはしない)。
    expected_counts: CopyCounts | None = None


class WeekdayCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    date: _Date
    count: int


class CopySkipCounts(BaseModel):
    """写さない訪問の内訳 (写す元の週の訪問件数ベース)."""

    model_config = ConfigDict(extra="forbid")

    cancelled: int = 0
    unplanned: int = 0
    special_extra: int = 0
    inactive_patient: int = 0
    user_excluded: int = 0
    # 写す先に残す取消行と同じ (患者・日・開始時刻)
    kept_conflict: int = 0
    # 写す先の同じ (患者・日) に打刻・取込・青ピンの訪問がある (時刻は問わない)
    kept_same_day: int = 0
    # 2 名体制の相方を写さないため組ごと写さない
    pair_partner: int = 0
    # 打刻のある週 (足すだけ) で、写す先のその (患者・日) に既に訪問がある
    occupied_day: int = 0
    # 写す先の日付が今日より前
    past_day: int = 0


class NotInFixedItem(BaseModel):
    """固定訪問に無い (患者・曜日) の訪問. 2 名体制は 1 行にまとめる."""

    model_config = ConfigDict(extra="forbid")

    visit_ids: list[UUID]
    patient_id: UUID
    patient_name: str
    weekday: int
    target_date: date
    start_time: time
    end_time: time
    excluded: bool


class MissingFixedItem(BaseModel):
    """写す元の週に無かった固定訪問 (補う候補)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str
    weekday: int
    target_date: date
    start_time: time
    end_time: time
    visits: int


class NeedsManualStaffItem(BaseModel):
    """担当を手で付ける必要がある訪問 (コースなしで入る) と理由."""

    model_config = ConfigDict(extra="forbid")

    origin: Literal["copy", "fill"]
    patient_id: UUID
    patient_name: str
    target_date: date
    start_time: time
    end_time: time
    reason: str


class ExistingCounts(BaseModel):
    """写す先の週に今ある訪問の扱い (§4-5)."""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    replace: int = 0
    keep_checked_in: int = 0
    keep_import: int = 0
    keep_pinned: int = 0
    keep_cancelled: int = 0
    # 今日より前の日の訪問 (置き換えない)
    keep_past: int = 0
    # 打刻のある週 (足すだけ) で残る、上の区分以外の訪問
    keep_other: int = 0


class CopyWeekPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_week_start: date
    target_week_start: date
    mode: CopyWeekMode
    # 写す訪問 (オプション反映後) と、固定訪問から補う訪問
    copy_count: int
    fill_count: int
    patients: int
    by_weekday: list[WeekdayCount]
    skipped: CopySkipCounts
    # 臨時コース配下 → コースなしで入る件数
    temp_course_count: int
    not_in_fixed: list[NotInFixedItem]
    missing_fixed: list[MissingFixedItem]
    missing_fixed_count: int
    missing_patients_without_visits: int
    existing: ExistingCounts
    needs_manual_staff: list[NeedsManualStaffItem]
    source_holidays: list[HolidayRead]
    target_holidays: list[HolidayRead]
